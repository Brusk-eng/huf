# Copyright (c) 2026, Huf and contributors
# For license information, please see license.txt

"""Unit tests for the WhatsApp management API (mock-based, no bench required)."""

import json
import unittest
from unittest.mock import MagicMock, patch

import frappe

if isinstance(frappe, MagicMock):
	# Keep decorated functions callable when frappe is the test stub.
	frappe.whitelist = lambda *args, **kwargs: (lambda fn: fn)
	frappe.PermissionError = PermissionError

from huf.ai import whatsapp_management as mgmt  # noqa: E402
from huf.ai.gateway_adapters import whatsapp_bridge as bridge  # noqa: E402
from huf.ai.gateway_adapters.whatsapp_bridge import BridgeError  # noqa: E402

TOKEN_SECRET = "EAAG-very-secret-token"


def _cred(key: str, value: str) -> MagicMock:
	row = MagicMock(key=key)
	row.get_password.return_value = value
	return row


class TestWhatsAppManagement(unittest.TestCase):
	def setUp(self):
		frappe_patcher = patch("huf.ai.whatsapp_management.frappe")
		self.frappe = frappe_patcher.start()
		self.addCleanup(frappe_patcher.stop)
		self.frappe.PermissionError = PermissionError
		self.frappe.db.exists.return_value = True
		self.frappe.db.get_value.return_value = "ACC-1"
		self.frappe.has_permission.return_value = True
		self.frappe.get_roles.return_value = ["System Manager"]

		available = patch.object(bridge, "is_available", return_value=True)
		available.start()
		self.addCleanup(available.stop)

		self.resolve = patch.object(
			bridge,
			"resolve_account",
			return_value={"name": "ACC-1", "phone_id": "111", "status": "Active"},
		)
		self.resolve.start()
		self.addCleanup(self.resolve.stop)

	# --- window policy -------------------------------------------------

	def test_send_text_refused_when_window_closed(self):
		with (
			patch.object(bridge, "window_state", return_value={"open": False, "expires_at": None}) as window,
			patch.object(bridge, "send_text") as send_text,
		):
			result = mgmt.send_text_message(gateway_name="GW-1", to="+44 7700 900123", text="hello")

		self.assertFalse(result["success"])
		self.assertEqual(result["error"], "window_closed")
		self.assertIn("send_template_message", result["hint"])
		window.assert_called_once_with("GW-1", "447700900123")
		send_text.assert_not_called()

	def test_send_text_sends_when_window_open(self):
		with (
			patch.object(bridge, "window_state", return_value={"open": True}),
			patch.object(bridge, "send_text", return_value={"success": True, "name": "WM-1"}) as send_text,
		):
			result = mgmt.send_text_message(gateway_name="GW-1", to="+44 7700 900123", text="hello")

		self.assertTrue(result["success"])
		send_text.assert_called_once_with("ACC-1", "447700900123", "hello")

	def test_send_text_rejects_empty_recipient_before_bridge(self):
		with patch.object(bridge, "window_state") as window:
			result = mgmt.send_text_message(gateway_name="GW-1", to="  ", text="hello")

		self.assertEqual(result["error"], "invalid_recipient")
		window.assert_not_called()

	# --- bridge passthrough --------------------------------------------

	def test_bridge_unavailable_passthrough_for_messages(self):
		with patch.object(bridge, "is_available", return_value=False):
			self.assertEqual(mgmt.list_messages(), {"success": False, "error": "bridge_unavailable"})
			self.assertEqual(mgmt.list_conversations(), {"success": False, "error": "bridge_unavailable"})
			self.assertEqual(
				mgmt.send_template_message(to="447700900123", template_name="welcome"),
				{"success": False, "error": "bridge_unavailable"},
			)

	def test_bridge_error_is_converted_to_error_dict(self):
		with patch.object(bridge, "send_template", side_effect=BridgeError({"error": "send_failed", "message": "boom"})):
			result = mgmt.send_template_message(to="447700900123", template_name="welcome")

		self.assertFalse(result["success"])
		self.assertEqual(result["error"], "send_failed")
		self.assertEqual(result["message"], "boom")

	def test_send_template_validates_template_name(self):
		with patch.object(bridge, "send_template") as send_template:
			result = mgmt.send_template_message(to="447700900123", template_name="Bad Name!")

		self.assertEqual(result["error"], "invalid_name")
		send_template.assert_not_called()

	def test_create_template_rejects_bad_name_without_bridge_call(self):
		with patch.object(bridge, "create_template") as create:
			result = mgmt.create_template(payload={"template_name": "Hello-World", "category": "UTILITY"})

		self.assertEqual(result["error"], "invalid_name")
		create.assert_not_called()

	def test_delete_template_requires_name(self):
		with patch.object(bridge, "delete_template") as delete:
			result = mgmt.delete_template(name="")

		self.assertEqual(result["error"], "invalid_name")
		delete.assert_not_called()

	# --- setup status ----------------------------------------------------

	def test_setup_status_flags_missing_app_secret_without_leaking_values(self):
		self.frappe.utils.get_url.return_value = "https://huf.example.com"
		self.frappe.get_all.side_effect = lambda doctype, **kwargs: (
			[{"name": "GW-1", "is_enabled": 1, "integration_settings": "IS-1"}]
			if doctype == "Gateway"
			else [{"name": "ACC-1", "phone_id": "111", "status": "Active"}]
		)
		settings = MagicMock(
			credentials=[
				_cred("app_secret", ""),
				_cred("webhook_verify_token", "verify-me"),
			]
		)
		self.frappe.get_doc.return_value = settings

		result = mgmt.get_setup_status()

		self.assertTrue(result["success"])
		self.assertTrue(result["bridge_available"])
		self.assertEqual(result["accounts"], [{"name": "ACC-1", "phone_id": "111", "status": "Active"}])
		gateway = result["gateways"][0]
		self.assertTrue(gateway["app_secret_missing"])
		self.assertIn("app_secret_missing", gateway["failing_checks"])
		self.assertTrue(gateway["verify_token_present"])
		self.assertEqual(
			gateway["webhook_url"],
			"https://huf.example.com/api/method/huf.ai.gateway_webhook.handle_gateway_webhook?gateway_name=GW-1",
		)
		self.assertIn("connector", gateway["callback_warning"])
		self.assertNotIn("verify-me", json.dumps(result))

	def test_setup_status_requires_admin_role(self):
		self.frappe.get_roles.return_value = ["Guest"]

		with self.assertRaises(PermissionError):
			mgmt.get_setup_status()

	def test_setup_status_without_bridge_reports_no_accounts(self):
		self.frappe.get_all.return_value = []
		with patch.object(bridge, "is_available", return_value=False):
			result = mgmt.get_setup_status()

		self.assertFalse(result["bridge_available"])
		self.assertEqual(result["accounts"], [])

	def test_mutating_calls_never_use_ignore_permissions(self):
		with (
			patch.object(bridge, "window_state", return_value={"open": True}),
			patch.object(bridge, "send_text", return_value={"success": True}),
		):
			mgmt.send_text_message(gateway_name="GW-1", to="447700900123", text="hi")
		for call in self.frappe.mock_calls:
			self.assertNotIn("ignore_permissions", call.kwargs)
		self.frappe.set_user.assert_not_called()

	def test_permission_denied_propagates_not_converted(self):
		self.frappe.has_permission.return_value = False

		with self.assertRaises(PermissionError):
			mgmt.list_messages()


if __name__ == "__main__":
	unittest.main()
