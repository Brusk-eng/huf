"""Signed WhatsApp ingress: batched fan-out, status acks, rate limit, 403 verify (T3)."""

from __future__ import annotations

import hashlib
import hmac
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from huf.ai import gateway_webhook
from huf.ai.gateway_adapters import GatewayInboundRequest
from huf.ai.gateway_adapters.whatsapp import WhatsAppGatewayAdapter

BRIDGE = "huf.ai.gateway_adapters.whatsapp_bridge"
CREDS = {
	"phone_number_id": "phone-123",
	"access_token": "access-token",
	"webhook_verify_token": "verify-value",
	"app_secret": "s3cret",
}


def _msg(wamid, text="hello", sender="1234567890", **extra):
	m = {"from": sender, "id": wamid, "type": "text", "text": {"body": text}}
	m.update(extra)
	return m


def _payload(messages=None, statuses=None):
	value = {"messaging_product": "whatsapp", "metadata": {"phone_number_id": "phone-123"}}
	if messages is not None:
		value["messages"] = messages
		value["contacts"] = [{"wa_id": "1234567890", "profile": {"name": "Ann"}}]
	if statuses is not None:
		value["statuses"] = statuses
	return {"entry": [{"id": "biz", "changes": [{"field": "messages", "value": value}]}]}


class TestWhatsAppIngress(unittest.TestCase):
	def _call(self, payload=None, *, secret="s3cret", method="POST", query=None, body=None, ingest=None, record=None, forward=None):
		adapter = WhatsAppGatewayAdapter(CREDS, gateway_context={"gateway_name": "WA"})
		raw = body if body is not None else json.dumps(payload or {}).encode()
		sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
		request = GatewayInboundRequest(raw, headers={"X-Hub-Signature-256": sig}, query=query or {}, method=method)
		response = SimpleNamespace()
		self.mock_frappe = MagicMock()
		self.mock_frappe.PermissionError = frappe.PermissionError
		self.mock_frappe.request = SimpleNamespace(args={"gateway_name": "WA"})
		self.mock_frappe.local = SimpleNamespace(response=response)
		self.mock_frappe.get_doc.return_value = SimpleNamespace(name="WA", provider="WhatsApp", is_enabled=1, whatsapp_account="")
		self.gate = MagicMock()
		self.ingest = ingest or MagicMock(
			side_effect=lambda gw, eid, ctx, **kw: {"event_name": f"GE-{eid}", "status": "Queued"}
		)
		self.forward = forward or MagicMock(return_value=True)
		self.record = record or MagicMock(return_value="WM-1")
		with patch("huf.ai.gateway_webhook.frappe", self.mock_frappe), patch(
			"huf.ai.gateway_webhook.get_gateway_adapter", return_value=adapter
		), patch("huf.ai.gateway_webhook._inbound_request", return_value=request), patch(
			"huf.ai.gateway_webhook._ingest_rate_gate", self.gate
		), patch("huf.ai.gateway_service.ingest_gateway_event", self.ingest), patch(
			f"{BRIDGE}.forward_status", self.forward
		), patch(f"{BRIDGE}.record_incoming", self.record), patch(
			f"{BRIDGE}.resolve_account", return_value={"name": "Acct"}
		), patch("huf.ai.gateway_webhook._text_response") as self.text:
			self.result = gateway_webhook.handle_gateway_webhook()
		self.response = response
		return self.result

	def test_batch_of_two_messages_and_one_status_fans_out(self):
		status = {"id": "wamid.out", "status": "delivered", "recipient_id": "1234567890"}
		result = self._call(_payload([_msg("wamid.1"), _msg("wamid.2", "second")], statuses=[status]))
		self.assertTrue(result["success"])
		self.assertEqual(self.ingest.call_count, 2)
		self.assertEqual(self.forward.call_count, 1)
		self.assertEqual(self.record.call_count, 2)
		self.assertEqual((result["ingested"], result["statuses"], result["forwarded"]), (2, 1, 1))
		first = self.ingest.call_args_list[0]
		self.assertEqual(first.args[:2], ("WA", "wamid.1"))
		self.assertTrue(first.kwargs["verified_sender"])
		self.gate.assert_called_once()

	def test_status_only_payload_succeeds_without_ingest_or_rate_limit(self):
		result = self._call(_payload(statuses=[{"id": "wamid.out", "status": "read"}]))
		self.assertTrue(result["success"])
		self.ingest.assert_not_called()
		self.gate.assert_not_called()
		self.assertEqual(self.forward.call_count, 1)

	def test_unknown_benign_payload_is_acked(self):
		result = self._call({"entry": [{"changes": [{"field": "account_update", "value": {"event": "X"}}]}]})
		self.assertTrue(result["success"])
		self.assertEqual(result["ignored"], 1)
		self.ingest.assert_not_called()
		self.gate.assert_not_called()

	def test_duplicate_wamid_is_counted_and_not_mirrored(self):
		dup = MagicMock(return_value={"duplicate": True, "event_name": "GE-1"})
		result = self._call(_payload([_msg("wamid.1")]), ingest=dup)
		self.assertEqual((result["ingested"], result["duplicates"]), (0, 1))
		self.record.assert_not_called()

	def test_mirror_failure_never_breaks_the_ack(self):
		result = self._call(_payload([_msg("wamid.1")]), record=MagicMock(side_effect=RuntimeError("boom")))
		self.assertTrue(result["success"])
		self.assertEqual(result["ingested"], 1)

	def test_forward_failure_never_breaks_the_ack(self):
		result = self._call(
			_payload(statuses=[{"id": "w", "status": "sent"}]), forward=MagicMock(side_effect=RuntimeError("x"))
		)
		self.assertTrue(result["success"])
		self.assertEqual(result["forwarded"], 0)

	def test_forged_signature_is_401_and_counts_against_budget(self):
		result = self._call(_payload([_msg("wamid.1")]), secret="wrong")
		self.assertEqual(result, {"success": False, "error": "Provider verification failed"})
		self.assertEqual(self.response.http_status_code, 401)
		self.ingest.assert_not_called()
		self.gate.assert_called_once()

	def test_verify_failure_on_get_is_403_not_500(self):
		query = {"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "abc"}
		result = self._call(method="GET", query=query, body=b"")
		self.assertEqual(result["success"], False)
		self.assertEqual(self.response.http_status_code, 403)
		self.text.assert_not_called()

	def test_verify_success_on_get_returns_challenge(self):
		query = {"hub.mode": "subscribe", "hub.verify_token": "verify-value", "hub.challenge": "abc"}
		self.assertIsNone(self._call(method="GET", query=query, body=b""))
		self.text.assert_called_once_with("abc")

	def test_malformed_signed_json_is_400(self):
		result = self._call(body=b"not json")
		self.assertFalse(result["success"])
		self.assertEqual(self.response.http_status_code, 400)

	def test_quoted_reply_keeps_phone_as_conversation(self):
		self._call(_payload([_msg("wamid.q", "re", context={"id": "wamid.prev"})]))
		ctx = self.ingest.call_args.args[2]
		self.assertEqual(ctx["conversation_id"], "1234567890")
		self.assertEqual(ctx["sender_id"], "1234567890")
		self.assertFalse(ctx["thread_id"])
		self.assertEqual(self.ingest.call_args.kwargs["raw_payload"]["quoted_message_id"], "wamid.prev")


if __name__ == "__main__":
	unittest.main()
