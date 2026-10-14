# Copyright (c) 2026, Huf and contributors
# For license information, please see license.txt

"""Unit tests for WhatsApp gateway health (pure evaluation + scoped setup status)."""

import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import frappe

if isinstance(frappe, MagicMock):
	frappe.whitelist = lambda *args, **kwargs: (lambda fn: fn)
	frappe.PermissionError = PermissionError

from huf.ai import whatsapp_health as wh  # noqa: E402
from huf.ai import whatsapp_management as mgmt  # noqa: E402
from huf.ai.gateway_adapters import whatsapp_bridge as bridge  # noqa: E402

NOW = datetime(2026, 10, 14, 12, 0, 0)


def facts(**over):
	base = {
		"enabled": True,
		"has_integration": True,
		"access_token_present": True,
		"phone_number_id_present": True,
		"verify_token_present": True,
		"app_secret_present": True,
		"app_id_present": True,
		"account_phone_mismatch": False,
		"last_event_at": NOW - timedelta(hours=1),
		"last_error": "",
		"events_ever": 5,
		"replied_ever": True,
		"events_24h": {"received": 2, "failed": 0, "unrouted": 0, "rejected": 0},
		"fallback_template_approved": None,
		"fallback_template_status": "",
	}
	base.update(over)
	return base


def codes(result):
	return {r["code"] for r in result["reasons"]}


def states(result):
	return {c["code"]: c["state"] for c in result["checklist"]}


class TestEvaluate(unittest.TestCase):
	def test_healthy(self):
		r = wh.evaluate(facts(), NOW)
		self.assertEqual(r["health"], "healthy")
		self.assertEqual(r["reasons"], [])
		self.assertIsNone(r["next_step"])
		self.assertTrue(all(c["state"] == "done" for c in r["checklist"]))

	def test_disabled_wins(self):
		r = wh.evaluate(facts(enabled=False, app_secret_present=False), NOW)
		self.assertEqual(r["health"], "disabled")
		self.assertEqual(r["next_step"]["code"], "add_app_secret")
		self.assertEqual(states(r)["enabled"], "todo")

	def test_blocked_missing_secret_and_token_wording(self):
		r = wh.evaluate(facts(app_secret_present=False, access_token_present=False), NOW)
		self.assertEqual(r["health"], "blocked")
		msgs = {x["code"]: x["message"] for x in r["reasons"]}
		self.assertEqual(msgs["access_token_missing"], "The access token is missing.")
		self.assertEqual(
			msgs["app_secret_missing"], "Add the app secret so we can confirm messages really come from Meta."
		)
		self.assertTrue(all(x["severity"] == "error" for x in r["reasons"]))

	def test_blocked_on_phone_mismatch(self):
		r = wh.evaluate(facts(account_phone_mismatch=True), NOW)
		self.assertEqual(r["health"], "blocked")
		self.assertEqual(
			r["reasons"][0]["message"],
			"This number belongs to a different WhatsApp account than the one linked.",
		)
		self.assertEqual(r["next_step"]["code"], "fix_account_link")
		self.assertEqual(states(r)["credentials"], "blocked")

	def test_not_set_up_when_nothing_configured(self):
		r = wh.evaluate(
			facts(
				has_integration=False,
				access_token_present=False,
				phone_number_id_present=False,
				verify_token_present=False,
				app_secret_present=False,
				app_id_present=False,
				last_event_at=None,
				events_ever=0,
				replied_ever=False,
			),
			NOW,
		)
		self.assertEqual(r["health"], "not_set_up")
		self.assertEqual(r["next_step"]["code"], "create_meta_app")

	def test_not_set_up_when_configured_but_silent(self):
		r = wh.evaluate(facts(last_event_at=None, events_ever=0, replied_ever=False), NOW)
		self.assertEqual(r["health"], "not_set_up")
		self.assertEqual(r["next_step"]["code"], "send_test_message")
		self.assertEqual(
			[x["message"] for x in r["reasons"]],
			[
				"Meta hasn't sent us anything yet. Check the callback URL and that the messages field is subscribed."
			],
		)
		s = states(r)
		self.assertEqual((s["webhook"], s["subscribed"], s["test_message"]), ("todo", "todo", "todo"))

	def test_degraded_on_failed_deliveries(self):
		r = wh.evaluate(facts(events_24h={"received": 9, "failed": 3, "unrouted": 0, "rejected": 0}), NOW)
		self.assertEqual(r["health"], "degraded")
		self.assertEqual(r["reasons"][0]["message"], "Replies are failing: 3 in the last 24 hours.")
		self.assertEqual(r["reasons"][0]["severity"], "warning")

	def test_degraded_on_recent_error_only(self):
		r = wh.evaluate(facts(last_error="Token expired"), NOW)
		self.assertEqual(r["health"], "degraded")
		self.assertIn("recent_error", codes(r))
		old = wh.evaluate(facts(last_error="Token expired", last_event_at=NOW - timedelta(days=3)), NOW)
		self.assertEqual(old["health"], "healthy")

	def test_degraded_on_rejected_or_paused_template(self):
		for status in ("REJECTED", "PAUSED"):
			r = wh.evaluate(facts(fallback_template_approved=False, fallback_template_status=status), NOW)
			self.assertEqual(r["health"], "degraded")
			self.assertIn("fallback_template_unavailable", codes(r))
		pending = wh.evaluate(facts(fallback_template_approved=False, fallback_template_status="PENDING"), NOW)
		self.assertEqual(pending["health"], "healthy")

	def test_checklist_inference(self):
		r = wh.evaluate(facts(replied_ever=False, events_ever=1), NOW)
		s = states(r)
		self.assertEqual((s["webhook"], s["subscribed"], s["test_message"]), ("done", "done", "todo"))
		self.assertEqual(r["next_step"]["code"], "send_test_message")
		self.assertEqual(
			[c["code"] for c in r["checklist"]],
			["meta_app", "credentials", "webhook", "subscribed", "app_secret", "enabled", "test_message"],
		)
		blocked = wh.evaluate(facts(access_token_present=False, replied_ever=False), NOW)
		self.assertEqual(states(blocked)["test_message"], "blocked")


class TestEventCounts(unittest.TestCase):
	def test_events_24h_counting(self):
		rows = [
			{"status": "Succeeded", "reply_status": "delivered", "delivery_error": ""},
			{"status": "Succeeded", "reply_status": "failed", "delivery_error": "error 131047: x"},
			{"status": "Failed", "reply_status": "", "delivery_error": ""},
			{"status": "Unrouted", "reply_status": "", "delivery_error": ""},
			{"status": "Rejected", "reply_status": "", "delivery_error": ""},
			{"status": "Rejected", "reply_status": "", "delivery_error": ""},
		]
		self.assertEqual(
			wh.count_events(rows), {"received": 6, "failed": 2, "unrouted": 1, "rejected": 2}
		)

	def test_meta_links(self):
		links = wh.meta_links("1234567890")
		self.assertEqual(links["app_dashboard"], "https://developers.facebook.com/apps/1234567890/")
		self.assertTrue(links["webhook_config"].endswith("/whatsapp-business/wa-settings/"))
		self.assertEqual(links["whatsapp_manager"], wh.WHATSAPP_MANAGER)
		none = wh.meta_links("not an id/../x")
		self.assertIsNone(none["app_dashboard"])
		self.assertIsNone(none["webhook_config"])


class TestReads(unittest.TestCase):
	def setUp(self):
		p = patch("huf.ai.whatsapp_health.frappe")
		self.frappe = p.start()
		self.addCleanup(p.stop)

	def test_fallback_template_status_is_account_scoped(self):
		self.frappe.get_all.return_value = [{"status": "APPROVED"}]
		out = wh.read_fallback_template("ACC-1", "hello", "en_US", True)
		self.assertEqual(out, {"approved": True, "status": "APPROVED"})
		kwargs = self.frappe.get_all.call_args.kwargs
		self.assertEqual(kwargs["filters"]["whatsapp_account"], "ACC-1")
		self.assertEqual(kwargs["filters"]["language_code"], "en_US")

	def test_fallback_template_unknown_without_connector_or_row(self):
		self.assertIsNone(wh.read_fallback_template("ACC-1", "hello", "en", False)["approved"])
		self.frappe.get_all.return_value = []
		self.assertIsNone(wh.read_fallback_template("ACC-1", "hello", "en", True)["approved"])

	def test_unrouted_phone_ids_from_payload_metadata(self):
		self.frappe.get_all.return_value = [
			{"raw_payload": {"metadata": {"phone_number_id": "999"}}},
			{"raw_payload": json.dumps({"metadata": {"phone_number_id": "111"}})},
			{"raw_payload": {"metadata": {"phone_number_id": "999"}}},
			{"raw_payload": None},
		]
		self.assertEqual(wh.read_unrouted_phone_ids("GW", "111", NOW), ["999"])

	def test_last_failed_event_is_redacted(self):
		self.frappe.get_all.side_effect = [
			[],
			[{"name": "EV-1", "received_at": NOW, "delivery_error": "bad access_token=EAAGsecret12345678", "error_message": ""}],
		]
		self.frappe.db.count.return_value = 3
		self.frappe.db.exists.return_value = None
		out = wh.read_events("GW", NOW)
		self.assertNotIn("EAAGsecret", out["last_failed_event"]["error"])
		self.assertEqual(out["last_failed_event"]["name"], "EV-1")
		self.assertEqual(out["events_ever"], 3)
		self.assertFalse(out["replied_ever"])


class TestScopedSetupStatus(unittest.TestCase):
	GATEWAYS = {
		"GW-A": {"name": "GW-A", "is_enabled": 1, "integration_settings": "IS-A", "phone_number_id": "111"},
		"GW-B": {"name": "GW-B", "is_enabled": 0, "integration_settings": "IS-B", "phone_number_id": "222"},
	}
	ACCOUNTS = {"ACC-A": {"name": "ACC-A", "phone_id": "111", "status": "Active"},
		"ACC-B": {"name": "ACC-B", "phone_id": "222", "status": "Active"}}

	def setUp(self):
		p = patch("huf.ai.whatsapp_management.frappe")
		self.frappe = p.start()
		self.addCleanup(p.stop)
		self.frappe.PermissionError = PermissionError
		self.frappe.get_roles.return_value = ["System Manager"]
		self.frappe.utils.get_url.return_value = "https://huf.example.com"

		def get_all(doctype, **kw):
			filters = kw.get("filters") or {}
			if doctype == "Gateway":
				return [dict(g) for n, g in self.GATEWAYS.items() if filters.get("name") in (None, n)]
			if doctype == "WhatsApp Account":
				return [dict(a) for n, a in self.ACCOUNTS.items() if filters.get("name") in (None, n)]
			return []

		self.frappe.get_all.side_effect = get_all
		self.frappe.db.get_value.side_effect = lambda dt, name, field=None, *a, **k: (
			{"GW-A": "ACC-A", "GW-B": "ACC-B"}.get(name) if dt == "Gateway" else ""
		)
		self.frappe.get_doc.return_value = MagicMock(credentials=[])
		for target, value in (("is_available", True),):
			q = patch.object(bridge, target, return_value=value)
			q.start()
			self.addCleanup(q.stop)
		h = patch("huf.ai.whatsapp_health.build_health", side_effect=self._fake_health)
		h.start()
		self.addCleanup(h.stop)

	@staticmethod
	def _fake_health(gateway, presence, **kw):
		return {"health": "healthy" if gateway["is_enabled"] else "disabled", "marker": gateway["name"]}

	def test_one_gateway_does_not_leak_another(self):
		result = mgmt.get_setup_status("GW-A")
		self.assertTrue(result["success"])
		self.assertEqual(result["gateway"]["name"], "GW-A")
		self.assertEqual([a["name"] for a in result["accounts"]], ["ACC-A"])
		blob = json.dumps(result, default=str)
		self.assertNotIn("GW-B", blob)
		self.assertNotIn("ACC-B", blob)
		self.assertNotIn("bridge_available", result)
		self.assertTrue(result["connector_available"])
		self.assertNotIn("callback_warning", result)
		self.assertNotIn("callback_warning", result["gateway"])

	def test_unknown_gateway_is_not_found(self):
		self.assertEqual(mgmt.get_setup_status("GW-Z")["error"], "not_found")

	def test_no_arg_returns_summary_only(self):
		result = mgmt.get_setup_status()
		self.assertEqual(
			result,
			{
				"success": True,
				"connector_available": True,
				"gateways": [
					{"name": "GW-A", "health": "healthy", "enabled": True},
					{"name": "GW-B", "health": "disabled", "enabled": False},
				],
			},
		)

	def test_requires_admin(self):
		self.frappe.get_roles.return_value = ["Guest"]
		with self.assertRaises(PermissionError):
			mgmt.get_setup_status("GW-A")
