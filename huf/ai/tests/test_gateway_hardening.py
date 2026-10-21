"""Hardening fixes from live security testing of the WhatsApp gateway."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from huf.ai import gateway_service, gateway_webhook
from huf.ai.gateway_adapters.types import GatewayInboundRequest
from huf.ai.gateway_adapters.whatsapp import WhatsAppGatewayAdapter


def _req(body: bytes) -> GatewayInboundRequest:
	return GatewayInboundRequest(body=body, headers={}, query={}, method="POST")


class TestUniqueRaceIsDuplicate(unittest.TestCase):
	def test_unique_validation_error_counts_as_duplicate(self):
		doc = MagicMock()
		doc.insert.side_effect = frappe.UniqueValidationError("dup")
		real_get_doc = frappe.get_doc

		def fake_get_doc(*args, **kwargs):
			if args and isinstance(args[0], dict) and args[0].get("doctype") == "Gateway Event":
				return doc
			return real_get_doc(*args, **kwargs)

		real_get_value = frappe.db.get_value
		calls = []

		def fake_get_value(doctype, *args, **kwargs):
			if doctype == "Gateway Event":
				calls.append(1)
				return None if len(calls) == 1 else "GEV-1"
			return real_get_value(doctype, *args, **kwargs)

		with (
			patch.object(gateway_service.frappe, "get_doc", side_effect=fake_get_doc),
			patch.object(gateway_service.frappe.db, "get_value", side_effect=fake_get_value),
			patch.object(gateway_service.frappe, "log_error") as log_error,
		):
			result = gateway_service.ingest_gateway_event("GW", "wamid.1", {}, verified_sender=True)
		self.assertEqual(result, {"duplicate": True, "event_name": "GEV-1"})
		log_error.assert_not_called()


class TestPayloadParsing(unittest.TestCase):
	def test_recursion_and_value_errors_are_invalid_payload(self):
		for exc in (RecursionError(), ValueError("x"), TypeError("x")):
			with patch("huf.ai.gateway_adapters.whatsapp.json.loads", side_effect=exc):
				self.assertIsNone(WhatsAppGatewayAdapter._payload(_req(b"{}")))

	def test_deeply_nested_body_is_none(self):
		body = b"[" * 200000 + b"]" * 200000
		self.assertIsNone(WhatsAppGatewayAdapter._payload(_req(body)))


class TestMessageTextCap(unittest.TestCase):
	def test_long_text_is_capped(self):
		out = gateway_service._cap_message_text("a" * 100_000)
		self.assertLess(len(out), 17000)
		self.assertTrue(out.endswith("[truncated]"))

	def test_short_text_untouched(self):
		self.assertEqual(gateway_service._cap_message_text("hi"), "hi")
		self.assertEqual(gateway_service._cap_message_text(None), "")


class TestConversationRaceRetry(unittest.TestCase):
	def test_retries_deadlock_then_succeeds(self):
		run = MagicMock(side_effect=[frappe.QueryDeadlockError(), frappe.TimestampMismatchError(), {"ok": 1}])
		with (
			patch("time.sleep") as sleep,
			patch.object(gateway_service.frappe.db, "rollback") as rollback,
		):
			self.assertEqual(gateway_service._run_agent_with_retry(run, prompt="x"), {"ok": 1})
		self.assertEqual(run.call_count, 3)
		self.assertEqual(rollback.call_count, 2)
		self.assertEqual(sleep.call_count, 2)

	def test_retry_that_finds_inflight_run_does_not_reexecute(self):
		run = MagicMock(
			side_effect=[
				frappe.QueryDeadlockError(),
				{"queued": True, "status": "Queued", "agent_run_id": "AR-1"},
			]
		)
		with patch("time.sleep"), patch.object(gateway_service.frappe.db, "rollback"):
			with self.assertRaises(RuntimeError):
				gateway_service._run_agent_with_retry(run, client_idempotency_key="gateway-event:E1")
		self.assertEqual(run.call_count, 2)

	def test_retry_returns_finished_run(self):
		done = {"success": True, "status": "Success", "agent_run_id": "AR-1", "response": "hi"}
		run = MagicMock(side_effect=[frappe.TimestampMismatchError(), done])
		with patch("time.sleep"), patch.object(gateway_service.frappe.db, "rollback"):
			self.assertEqual(gateway_service._run_agent_with_retry(run), done)

	def test_gives_up_after_max_attempts(self):
		run = MagicMock(side_effect=frappe.QueryDeadlockError())
		with patch("time.sleep"), patch.object(gateway_service.frappe.db, "rollback"):
			with self.assertRaises(frappe.QueryDeadlockError):
				gateway_service._run_agent_with_retry(run)
		self.assertEqual(run.call_count, gateway_service._CONVERSATION_RACE_ATTEMPTS)

	def test_other_errors_not_retried(self):
		run = MagicMock(side_effect=ValueError("boom"))
		with self.assertRaises(ValueError):
			gateway_service._run_agent_with_retry(run)
		self.assertEqual(run.call_count, 1)


class FakeResponse:
	http_status_code = 200


class TestWebhookLimits(unittest.TestCase):
	def test_oversized_body_is_413_before_verification(self):
		gateway = SimpleNamespace(name="GW", provider="WhatsApp", is_enabled=1)
		adapter = MagicMock()
		with (
			patch.object(gateway_webhook, "frappe") as mf,
			patch.object(gateway_webhook, "get_gateway_adapter", return_value=adapter),
		):
			mf.request = SimpleNamespace(args={"gateway_name": "GW"}, content_length=5 * 1024 * 1024)
			mf.local = SimpleNamespace(response=FakeResponse())
			mf.get_doc.return_value = gateway
			result = gateway_webhook.handle_gateway_webhook()
		self.assertEqual(result["error"], "Payload too large")
		self.assertEqual(mf.local.response.http_status_code, 413)
		adapter.verify_inbound.assert_not_called()

	def test_message_cap_ignores_the_rest(self):
		n = gateway_webhook.MAX_MESSAGES_PER_PAYLOAD + 20
		events = [
			SimpleNamespace(
				provider_event_id=f"id{i}",
				sender_id="1",
				conversation_id="1",
				thread_id="",
				message_text="t",
				is_room=0,
				mentioned=0,
				raw_payload={},
			)
			for i in range(n)
		]
		parsed = SimpleNamespace(
			messages=tuple(events), statuses=(), template_updates=(), ignored=0, unknown_phone_ids=()
		)
		adapter = MagicMock()
		adapter.parse_payload.return_value = parsed
		gateway = SimpleNamespace(name="GW", whatsapp_account="")
		with (
			patch.object(gateway_webhook, "_ingest_rate_gate"),
			patch.object(gateway_webhook, "touch_last_event"),
			patch.object(gateway_webhook, "_warn"),
			patch.object(gateway_webhook, "_record_unknown_phone_ids"),
			patch(
				"huf.ai.gateway_service.ingest_gateway_event",
				return_value={"event_name": "E", "status": "Rejected"},
			) as ingest,
		):
			result = gateway_webhook._handle_batched_payload(gateway, adapter, _req(b"{}"))
		self.assertEqual(ingest.call_count, gateway_webhook.MAX_MESSAGES_PER_PAYLOAD)
		self.assertEqual(result["ignored"], 20)
		self.assertTrue(result["note"])

	def test_rate_limit_sets_retry_after(self):
		headers = {}
		with (
			patch.object(
				gateway_webhook,
				"_ingest_rate_limited",
				side_effect=frappe.RateLimitExceededError("slow"),
			),
			patch.object(gateway_webhook.frappe, "local", SimpleNamespace(response_headers=headers)),
		):
			with self.assertRaises(frappe.RateLimitExceededError):
				gateway_webhook._ingest_rate_gate()
		self.assertEqual(headers["Retry-After"], "60")
