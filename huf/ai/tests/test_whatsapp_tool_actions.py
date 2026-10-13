# Copyright (c) 2026, Huf and contributors
# For license information, please see license.txt

"""Tests for the bridge-backed WhatsApp agent tool: privilege gating (A8),
bridge routing for send_message, recipient normalisation, the Graph version
constant, and the list_templates / create_template actions.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from huf.ai.gateway_adapters import whatsapp_bridge as bridge
from huf.ai.tools import whatsapp


class FakeResponse:
	def __init__(self, status_code=200, body=None):
		self.status_code = status_code
		self._body = body or {}
		self.text = json.dumps(self._body)

	def json(self):
		return self._body


def _account(name="WA-1"):
	return {"name": name, "phone_id": "PN1", "token": "t", "url": "https://graph.facebook.com", "version": "v21.0"}


class TestWhatsAppToolGating(unittest.TestCase):
	def setUp(self):
		self._patches = [
			patch.object(bridge, "is_available", return_value=False),
			patch.object(bridge, "resolve_account", return_value=None),
			patch.object(bridge, "window_state", return_value={"open": False}),
		]
		self.mock_available, self.mock_resolve, self.mock_window = (p.start() for p in self._patches)
		self.post = patch.object(whatsapp.requests, "post", return_value=FakeResponse(body={"messages": [{"id": "wamid.9"}]})).start()
		self.addCleanup(patch.stopall)

	def _send(self, **overrides):
		kwargs = {"phone_number_id": "PN1", "access_token": "token", "to": "+1 (555) 123-4567", "message": "hi"}
		kwargs.update(overrides)
		return json.loads(whatsapp.handle_action("send_message", **kwargs))

	def test_proactive_send_without_gateway_is_refused_by_default(self):
		result = self._send()
		self.assertFalse(result["success"])
		self.assertEqual(result["error"], "proactive_send_not_permitted")
		self.post.assert_not_called()

	def test_proactive_send_allowed_with_allow_proactive(self):
		result = self._send(allow_proactive=True)
		self.assertTrue(result["success"])
		self.post.assert_called_once()

	def test_string_false_does_not_enable_flag(self):
		result = self._send(allow_proactive="false")
		self.assertEqual(result["error"], "proactive_send_not_permitted")
		self.post.assert_not_called()

	def test_open_window_allows_send_without_flag(self):
		self.mock_window.return_value = {"open": True}
		result = self._send(gateway_name="GW-1")
		self.assertTrue(result["success"])
		self.mock_window.assert_called_once_with("GW-1", "15551234567")

	def test_closed_window_requires_flag(self):
		result = self._send(gateway_name="GW-1")
		self.assertEqual(result["error"], "proactive_send_not_permitted")
		self.post.assert_not_called()

	def test_unreadable_window_fails_closed(self):
		self.mock_window.side_effect = RuntimeError("db down")
		result = self._send(gateway_name="GW-1")
		self.assertEqual(result["error"], "proactive_send_not_permitted")
		self.post.assert_not_called()

	def test_send_template_is_not_gated(self):
		result = json.loads(
			whatsapp.handle_action(
				"send_template",
				phone_number_id="PN1",
				access_token="token",
				to="15551234567",
				template_name="order_update",
			)
		)
		self.assertTrue(result["success"])

	def test_create_template_refused_without_management_flag(self):
		with patch.object(bridge, "create_template") as mock_create:
			result = json.loads(
				whatsapp.handle_action("create_template", template_name="promo", category="MARKETING", language_code="en")
			)
		self.assertEqual(result["error"], "management_not_permitted")
		mock_create.assert_not_called()

	def test_list_templates_without_bridge_returns_bridge_unavailable(self):
		result = json.loads(whatsapp.handle_action("list_templates"))
		self.assertFalse(result["success"])
		self.assertEqual(result["error"], "bridge_unavailable")


class TestWhatsAppToolBridgeRouting(unittest.TestCase):
	def test_send_message_uses_bridge_send_text_when_account_resolvable(self):
		with (
			patch.object(bridge, "is_available", return_value=True),
			patch.object(bridge, "resolve_account", return_value=_account()),
			patch.object(bridge, "send_text", return_value={"success": True, "name": "WM-1", "message_id": "wamid.1", "status": "Sent"}) as mock_send,
			patch.object(whatsapp.requests, "post") as mock_post,
		):
			result = json.loads(
				whatsapp.handle_action("send_message", to="+1 555-123-4567", message="hello", allow_proactive=True)
			)
		self.assertTrue(result["success"])
		self.assertEqual(result["data"]["message_id"], "wamid.1")
		mock_send.assert_called_once_with("WA-1", "15551234567", "hello")
		mock_post.assert_not_called()

	def test_send_message_bridge_error_payload_is_returned(self):
		with (
			patch.object(bridge, "is_available", return_value=True),
			patch.object(bridge, "resolve_account", return_value=_account()),
			patch.object(bridge, "send_text", side_effect=bridge.BridgeError({"success": False, "error": "send_failed"})),
		):
			result = json.loads(whatsapp.handle_action("send_message", to="15551234567", message="hi", allow_proactive=True))
		self.assertEqual(result, {"success": False, "error": "send_failed"})

	def test_send_message_falls_back_to_graph_when_no_account(self):
		with (
			patch.object(bridge, "is_available", return_value=True),
			patch.object(bridge, "resolve_account", return_value={"error": "no_account"}),
			patch.object(whatsapp.requests, "post", return_value=FakeResponse(body={"messages": [{"id": "wamid.2"}]})) as mock_post,
		):
			result = json.loads(
				whatsapp.handle_action(
					"send_message",
					phone_number_id="PN1",
					access_token="token",
					to="15551234567",
					message="hi",
					allow_proactive=True,
				)
			)
		self.assertTrue(result["success"])
		mock_post.assert_called_once()

	def test_graph_url_uses_bridge_default_version(self):
		self.assertEqual(whatsapp.META_GRAPH_URL, f"https://graph.facebook.com/{bridge.GRAPH_DEFAULT_VERSION}")
		self.assertNotIn("v18.0", whatsapp.META_GRAPH_URL)
		with (
			patch.object(bridge, "is_available", return_value=False),
			patch.object(whatsapp.requests, "post", return_value=FakeResponse(body={"messages": [{"id": "wamid.3"}]})) as mock_post,
		):
			whatsapp.handle_action(
				"send_message", phone_number_id="PN1", access_token="token", to="15551234567", message="hi", allow_proactive=True
			)
		self.assertTrue(mock_post.call_args.args[0].startswith(f"https://graph.facebook.com/{bridge.GRAPH_DEFAULT_VERSION}/"))

	def test_send_template_normalizes_recipient(self):
		with patch.object(whatsapp.requests, "post", return_value=FakeResponse(body={"messages": [{"id": "wamid.4"}]})) as mock_post:
			whatsapp.handle_action(
				"send_template",
				phone_number_id="PN1",
				access_token="token",
				to="+1 (555) 123-4567",
				template_name="order_update",
			)
		self.assertEqual(mock_post.call_args.kwargs["json"]["to"], "15551234567")


class TestWhatsAppToolTemplateActions(unittest.TestCase):
	def test_list_templates_returns_bridge_rows(self):
		rows = [{"name": "TPL-1", "template_name": "promo", "status": "APPROVED"}]
		with (
			patch.object(bridge, "is_available", return_value=True),
			patch.object(bridge, "list_templates", return_value=rows) as mock_list,
		):
			result = json.loads(whatsapp.handle_action("list_templates", whatsapp_account="WA-1", status="APPROVED"))
		self.assertTrue(result["success"])
		self.assertEqual(result["data"], rows)
		mock_list.assert_called_once_with("WA-1", "APPROVED")

	def test_create_template_with_management_flag_calls_bridge(self):
		with (
			patch.object(bridge, "is_available", return_value=True),
			patch.object(bridge, "resolve_account", return_value=_account()),
			patch.object(
				bridge,
				"create_template",
				return_value={"success": True, "name": "TPL-2", "status": "PENDING", "id": "123"},
			) as mock_create,
		):
			result = json.loads(
				whatsapp.handle_action(
					"create_template",
					allow_whatsapp_management=True,
					template_name="promo",
					category="MARKETING",
					language_code="en_US",
					body="Hello {{1}}",
				)
			)
		self.assertTrue(result["success"])
		self.assertEqual(result["data"]["name"], "TPL-2")
		account_name, payload = mock_create.call_args.args
		self.assertEqual(account_name, "WA-1")
		self.assertEqual(payload["template_name"], "promo")
		self.assertEqual(payload["body"], "Hello {{1}}")

	def test_create_template_bridge_unavailable(self):
		with patch.object(bridge, "is_available", return_value=False):
			result = json.loads(
				whatsapp.handle_action(
					"create_template",
					allow_whatsapp_management=True,
					template_name="promo",
					category="MARKETING",
					language_code="en",
				)
			)
		self.assertEqual(result["error"], "bridge_unavailable")

	def test_unknown_action_lists_new_actions(self):
		result = json.loads(whatsapp.handle_action("nope"))
		self.assertFalse(result["success"])
		self.assertIn("list_templates", result["error"])
		self.assertIn("create_template", result["error"])


if __name__ == "__main__":
	unittest.main()
