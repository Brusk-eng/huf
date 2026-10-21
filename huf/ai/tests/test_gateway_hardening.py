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


class TestTerminalStatePersistence(unittest.TestCase):
	def setUp(self):
		self._in_test = frappe.flags.get("in_test")
		frappe.flags.in_test = False
		self.addCleanup(lambda: setattr(frappe.flags, "in_test", self._in_test))

	def test_commits_are_skipped_under_the_test_runner(self):
		frappe.flags.in_test = True
		with patch.object(gateway_service.frappe.db, "commit") as commit:
			gateway_service._commit_unless_testing()
		commit.assert_not_called()

	def test_lost_terminal_write_is_repaired_and_logged(self):
		result = {"status": "Failed", "agent_run_id": "AR-1"}
		with (
			patch.object(gateway_service.frappe.db, "commit") as commit,
			patch.object(
				gateway_service.frappe.db,
				"get_value",
				return_value={"status": "Running", "agent_run": None},
			),
			patch.object(gateway_service.frappe.db, "set_value") as set_value,
			patch.object(gateway_service.frappe, "log_error") as log_error,
		):
			gateway_service._persist_terminal_state("GEV-1", result)
		set_value.assert_called_once_with("Gateway Event", "GEV-1", {"status": "Failed", "agent_run": "AR-1"})
		log_error.assert_called_once()
		self.assertEqual(commit.call_count, 2)

	def test_persisted_state_is_left_alone(self):
		with (
			patch.object(gateway_service.frappe.db, "commit"),
			patch.object(
				gateway_service.frappe.db,
				"get_value",
				return_value={"status": "Succeeded", "agent_run": "AR-1"},
			),
			patch.object(gateway_service.frappe.db, "set_value") as set_value,
		):
			gateway_service._persist_terminal_state(
				"GEV-1", {"status": "Succeeded", "agent_run_id": "AR-1"}
			)
		set_value.assert_not_called()

	def test_process_gateway_event_verifies_after_run(self):
		with (
			patch.object(gateway_service, "reap_stuck_gateway_events") as reap,
			patch.object(gateway_service, "_process_gateway_event", return_value={"status": "Queued"}),
			patch.object(gateway_service, "_persist_terminal_state") as persist,
		):
			out = gateway_service.process_gateway_event("GEV-1")
		reap.assert_called_once()
		persist.assert_called_once_with("GEV-1", {"status": "Queued"})
		self.assertEqual(out, {"status": "Queued"})

	def test_reaper_fails_stuck_running_events(self):
		with (
			patch.object(gateway_service.frappe, "get_all", return_value=["GEV-1", "GEV-2"]) as get_all,
			patch.object(gateway_service.frappe.db, "set_value") as set_value,
			patch.object(gateway_service.frappe.db, "commit"),
		):
			self.assertEqual(gateway_service.reap_stuck_gateway_events(), 2)
		self.assertEqual(get_all.call_args.kwargs["filters"]["status"], "Running")
		self.assertEqual(get_all.call_args.kwargs["filters"]["reply_status"], ["!=", "retrying"])
		self.assertEqual(set_value.call_count, 2)
		self.assertEqual(set_value.call_args.args[2]["status"], "Failed")


class TestPairingFixes(unittest.TestCase):
	def test_mask_code_keeps_last_two(self):
		self.assertEqual(gateway_service._mask_code("PAIR-ABCD1234"), "*" * 11 + "34")
		self.assertEqual(gateway_service._mask_code(""), "")

	def test_admission_reason_does_not_leak_code(self):
		gw = SimpleNamespace(direct_policy="Pairing")
		with (
			patch.object(gateway_service, "_sender_negative_state", return_value=None),
			patch.object(gateway_service, "_has_access_entry", return_value=False),
			patch.object(gateway_service, "_create_pairing_request", return_value="PAIR-ABCD1234"),
		):
			ok, reason = gateway_service._admission(gw, {"sender_id": "1"})
		self.assertFalse(ok)
		self.assertNotIn("PAIR-ABCD1234", reason)
		self.assertTrue(reason.endswith("34"))

	def test_approve_rate_limit_is_per_user_with_retry_after(self):
		headers = {}
		with (
			patch.object(gateway_service.frappe, "form_dict", {}),
			patch.object(gateway_service.frappe, "session", SimpleNamespace(user="a@x.com")),
			patch.object(gateway_service.frappe, "local", SimpleNamespace(response_headers=headers)),
			patch.object(
				gateway_service, "_approve_rate_limited", side_effect=frappe.RateLimitExceededError("x")
			),
		):
			with self.assertRaises(frappe.RateLimitExceededError):
				gateway_service.approve_gateway_pairing("PAIR-1")
			self.assertEqual(gateway_service.frappe.form_dict["_approve_rl_user"], "a@x.com")
		self.assertEqual(headers["Retry-After"], "60")

	def test_cooldown_ignores_expired_entries(self):
		captured = []

		def fake_get_all(doctype, **kw):
			captured.append(kw.get("filters"))
			return []

		real_get_doc = frappe.get_doc

		def fake_get_doc(*args, **kwargs):
			if args and isinstance(args[0], dict):
				return MagicMock()
			return real_get_doc(*args, **kwargs)

		gw = SimpleNamespace(
			name="GW", provider="WhatsApp", pairing_ttl_minutes=60, integration_settings="", pairing_reply_enabled=0
		)
		with (
			patch.object(gateway_service.frappe, "get_all", side_effect=fake_get_all),
			patch.object(gateway_service, "_new_unique_pairing_code", return_value="PAIR-X"),
			patch.object(gateway_service.frappe, "get_doc", side_effect=fake_get_doc),
			patch.object(gateway_service.frappe.db, "get_value", return_value=None),
		):
			gateway_service._create_pairing_request(gw, "1")
		cooldown = [f for f in captured if f and "pairing_sent_at" in f][0]
		self.assertEqual(cooldown["state"], ["!=", gateway_service.ENTRY_STATE_EXPIRED])


class TestReaperScheduled(unittest.TestCase):
	def test_reaper_is_in_scheduler_events(self):
		from huf import hooks

		self.assertIn(
			"huf.ai.gateway_service.reap_stuck_gateway_events",
			hooks.scheduler_events["cron"]["*/10 * * * *"],
		)
