# Copyright (c) 2026, Huf and contributors
# For license information, please see license.txt

"""Real-doctype WhatsApp gateway integration tests (T9).

``TestWhatsAppFullStack`` needs frappe_whatsapp installed and is skipped
otherwise. ``TestWhatsAppCoreWithoutBridge`` asserts the degraded behaviour on
a site without it. Only outbound HTTP to Meta is faked: frappe_whatsapp calls
``frappe.integrations.utils.make_request`` (which uses ``get_request_session``),
the bridge's template sync uses ``whatsapp_bridge._default_get`` and the
direct-mode adapter uses its injectable ``http_post``.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from huf.ai import gateway_webhook, whatsapp_management as mgmt
from huf.ai.gateway_adapters import GatewayInboundRequest, whatsapp_bridge as bridge
from huf.ai.gateway_adapters.types import GatewayReply
from huf.ai.gateway_adapters.whatsapp import WhatsAppGatewayAdapter, WhatsAppWindowClosed

APP_SECRET = "full-stack-secret"
PHONE_ID = "5550001111"
SENDER = "15551234567"
EXEC_USER = "wa-fullstack-exec@example.com"


class _Resp:
	def __init__(self, payload, status=200):
		self._payload = payload
		self.status_code = status
		self.headers = {"content-type": "application/json"}
		self.text = json.dumps(payload)

	def raise_for_status(self):
		if self.status_code >= 400:
			raise RuntimeError(f"HTTP {self.status_code}")

	def json(self):
		return self._payload


class FakeMeta:
	"""Stand-in for the requests session used by frappe.integrations.utils."""

	def __init__(self):
		self.calls = []
		self.template_list = {"data": []}

	def request(self, method, url, data=None, headers=None, json=None, params=None, **kwargs):
		body = data if json is None else json
		if isinstance(body, (str, bytes)):
			try:
				body = globals()["json"].loads(body)
			except ValueError:
				pass
		self.calls.append({"method": method, "url": url, "body": body, "headers": headers or {}})
		if url.endswith("/messages"):
			return _Resp({"messages": [{"id": f"wamid.OUT{len(self.calls)}"}]})
		if url.endswith("/message_templates") and method == "POST":
			return _Resp({"id": "meta-tpl-1", "status": "PENDING"})
		return _Resp({"success": True})

	def posts(self, suffix):
		return [c for c in self.calls if c["method"] == "POST" and c["url"].endswith(suffix)]


def _signed_request(payload):
	raw = json.dumps(payload).encode()
	sig = "sha256=" + hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
	return GatewayInboundRequest(raw, headers={"X-Hub-Signature-256": sig}, query={}, method="POST")


def _payload(messages=None, statuses=None):
	value = {"messaging_product": "whatsapp", "metadata": {"phone_number_id": PHONE_ID}}
	if messages is not None:
		value["messages"] = messages
		value["contacts"] = [{"wa_id": SENDER, "profile": {"name": "Ann"}}]
	if statuses is not None:
		value["statuses"] = statuses
	return {"entry": [{"id": "biz", "changes": [{"field": "messages", "value": value}]}]}


def _text_msg(wamid, text="hello"):
	return {"from": "+1 555 123 4567", "id": wamid, "type": "text", "text": {"body": text}}


class TestWhatsAppFullStack(IntegrationTestCase):
	def _build_fixtures(self):
		cls = self
		if not frappe.db.exists("Role", "Huf Gateway User"):
			frappe.get_doc({"doctype": "Role", "role_name": "Huf Gateway User"}).insert(ignore_permissions=True)
		if not frappe.db.exists("User", EXEC_USER):
			user = frappe.get_doc(
				{"doctype": "User", "email": EXEC_USER, "first_name": "WA Exec", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
			user.add_roles("Huf Gateway User")

		suffix = frappe.generate_hash(length=6)
		cls.account = frappe.get_doc(
			{
				"doctype": "WhatsApp Account",
				"account_name": f"FS Account {suffix}",
				"status": "Active",
				"url": "https://graph.facebook.com",
				"version": "v21.0",
				"phone_id": PHONE_ID,
				"business_id": "biz-1",
				"app_id": "app-1",
				"token": "meta-access-token",
				"webhook_verify_token": "verify-me",
				"is_default_outgoing": 1,
			}
		).insert(ignore_permissions=True)

		cls.integration = frappe.get_doc(
			{
				"doctype": "Integration Settings",
				"service": "whatsapp",
				"credentials": [
					{"key": "phone_number_id", "value": PHONE_ID},
					{"key": "access_token", "value": "meta-access-token"},
					{"key": "webhook_verify_token", "value": "verify-me"},
					{"key": "app_secret", "value": APP_SECRET},
				],
			}
		).insert(ignore_permissions=True)

		cls.gateway = frappe.get_doc(
			{
				"doctype": "Gateway",
				"gateway_name": f"FS WhatsApp {suffix}",
				"provider": "WhatsApp",
				"is_enabled": 1,
				"integration_settings": cls.integration.name,
				"whatsapp_account": cls.account.name,
				"execution_user": EXEC_USER,
				"direct_policy": "Allow list",
			}
		).insert(ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "Gateway Access Entry",
				"gateway": cls.gateway.name,
				"entry_type": "Sender",
				"provider": "WhatsApp",
				"external_id": SENDER,
				"state": "Approved",
			}
		).insert(ignore_permissions=True)

	def setUp(self):
		if not bridge.is_available():
			self.skipTest("frappe_whatsapp is not installed on this site; full-stack tests need the real doctypes")
		frappe.set_user("Administrator")
		self._build_fixtures()
		self.meta = FakeMeta()
		for target, new in (
			("frappe.utils.get_url", lambda *a, **k: "http://gw-wa-full.localhost"),
			("frappe.integrations.utils.get_request_session", lambda: self.meta),
			("huf.ai.gateway_adapters.whatsapp_bridge._default_get", self._meta_get),
			("huf.ai.gateway_webhook._ingest_rate_gate", lambda: None),
		):
			patcher = patch(target, new)
			patcher.start()
			self.addCleanup(patcher.stop)
		self._prev_request = getattr(frappe.local, "request", None)
		frappe.local.request = SimpleNamespace(args={"gateway_name": self.gateway.name})
		if not getattr(frappe.local, "response", None):
			frappe.local.response = frappe._dict()
		self.addCleanup(self._restore_request)
		# no worker is running; never really enqueue
		enq = patch("frappe.enqueue")
		enq.start()
		self.addCleanup(enq.stop)

	def tearDown(self):
		frappe.db.rollback()

	def _restore_request(self):
		frappe.local.request = self._prev_request

	def _meta_get(self, url, **kwargs):
		self.meta.calls.append({"method": "GET", "url": url, "body": None, "headers": kwargs.get("headers", {})})
		return _Resp(self.meta.template_list)

	# ----------------------------------------------------------------- helpers
	def _post_webhook(self, payload):
		request = _signed_request(payload)
		with patch("huf.ai.gateway_webhook._inbound_request", return_value=request):
			return gateway_webhook.handle_gateway_webhook()

	def _events(self, wamid=None):
		filters = {"gateway": self.gateway.name}
		if wamid:
			filters["provider_event_id"] = wamid
		return frappe.get_all("Gateway Event", filters=filters, pluck="name")

	def _inbound(self, wamid):
		result = self._post_webhook(_payload([_text_msg(wamid)]))
		self.assertTrue(result["success"], result)
		return result

	def _outgoing_count(self):
		return frappe.db.count("WhatsApp Message", {"type": "Outgoing", "whatsapp_account": self.account.name})

	def _make_template(self, name="fs_welcome", **extra):
		values = {
			"template_name": name,
			"category": "UTILITY",
			"language": "en",
			"language_code": "en_US",
			"body": "Hello {{1}}",
		}
		values.update(extra)
		return mgmt.create_template(account=self.account.name, payload=values)

	# ------------------------------------------------------------------- tests
	def test_01_fixtures_resolve_account_and_credentials(self):
		acct = bridge.resolve_account(self.account.name)
		self.assertEqual(acct["token"], "meta-access-token")
		self.assertTrue(bridge.account_matches_phone_id(self.account.name, PHONE_ID))
		status = mgmt.get_setup_status(self.gateway.name)
		self.assertTrue(status["bridge_available"])
		gw = status["gateways"][0]
		self.assertTrue(gw["enabled"])  # regression: Gateway field is is_enabled
		self.assertTrue(gw["app_secret_present"])
		self.assertEqual(gw["whatsapp_account"], self.account.name)
		self.assertNotIn(APP_SECRET, json.dumps(status))

	def test_02_inbound_creates_one_event_and_replay_is_deduped(self):
		result = self._inbound("wamid.IN1")
		self.assertEqual((result["ingested"], result["duplicates"]), (1, 0))
		events = self._events("wamid.IN1")
		self.assertEqual(len(events), 1)
		event = frappe.get_doc("Gateway Event", events[0])
		self.assertEqual(event.sender_id, SENDER)
		self.assertEqual(event.verified_sender, 1)
		self.assertEqual(event.message_text, "hello")
		# admitted via the approved Gateway Access Entry (no default route -> Unrouted, not Rejected)
		self.assertNotEqual(event.status, "Rejected", event.error_message)
		# mirrored once into frappe_whatsapp
		self.assertEqual(frappe.db.count("WhatsApp Message", {"message_id": "wamid.IN1", "type": "Incoming"}), 1)

		replay = self._post_webhook(_payload([_text_msg("wamid.IN1")]))
		self.assertEqual((replay["ingested"], replay["duplicates"]), (0, 1))
		self.assertEqual(len(self._events("wamid.IN1")), 1)
		self.assertEqual(frappe.db.count("WhatsApp Message", {"message_id": "wamid.IN1"}), 1)

	def test_03_status_only_payload_acks_without_new_event(self):
		before = len(self._events())
		result = self._post_webhook(
			_payload(statuses=[{"id": "wamid.unknown", "status": "delivered", "recipient_id": SENDER}])
		)
		self.assertTrue(result["success"])
		self.assertEqual((result["messages"], result["statuses"]), (0, 1))
		self.assertEqual(len(self._events()), before)

	def test_04_message_status_updates_real_whatsapp_message(self):
		sent = bridge.send_text(self.account.name, SENDER, "to be delivered", trusted=True)
		self.assertTrue(frappe.db.exists("WhatsApp Message", {"message_id": sent["message_id"]}))
		result = self._post_webhook(
			_payload(statuses=[{"id": sent["message_id"], "status": "delivered", "recipient_id": SENDER}])
		)
		self.assertEqual(result["forwarded"], 1)
		self.assertEqual(frappe.db.get_value("WhatsApp Message", sent["name"], "status"), "delivered")

	def test_05_template_status_payload_updates_templates_row(self):
		created = self._make_template("fs_status_tpl")
		self.assertTrue(created["success"], created)
		self.assertEqual(frappe.db.get_value("WhatsApp Templates", created["name"], "status"), "PENDING")
		payload = {
			"entry": [
				{
					"id": "biz",
					"changes": [
						{
							"field": "message_template_status_update",
							"value": {"event": "APPROVED", "message_template_id": "meta-tpl-1"},
						}
					],
				}
			]
		}
		result = self._post_webhook(payload)
		self.assertEqual((result["template_updates"], result["forwarded"]), (1, 1))
		self.assertEqual(frappe.db.get_value("WhatsApp Templates", created["name"], "status"), "APPROVED")
		self.assertEqual(len(self._events()), 0)

	def test_06_window_open_after_inbound_and_closed_after_24h(self):
		self.assertFalse(bridge.window_state(self.gateway.name, SENDER)["open"])
		self._inbound("wamid.IN2")
		state = bridge.window_state(self.gateway.name, "+1 (555) 123-4567")
		self.assertTrue(state["open"], state)
		event_name = self._events("wamid.IN2")[0]
		frappe.db.set_value(
			"Gateway Event", event_name, "received_at", now_datetime() - timedelta(hours=25)
		)
		self.assertFalse(bridge.window_state(self.gateway.name, SENDER)["open"])

	def test_07_bridge_send_text_creates_one_message_and_one_meta_post(self):
		before = self._outgoing_count()
		result = bridge.send_text(self.account.name, "+1 555 123 4567", "hi there", trusted=True)
		self.assertTrue(result["success"])
		self.assertEqual(self._outgoing_count(), before + 1)
		posts = self.meta.posts("/messages")
		self.assertEqual(len(posts), 1)
		self.assertTrue(posts[0]["url"].endswith(f"/v21.0/{PHONE_ID}/messages"))
		self.assertEqual(posts[0]["body"]["to"], SENDER)
		self.assertEqual(posts[0]["body"]["text"]["body"], "hi there")
		self.assertEqual(posts[0]["headers"]["authorization"], "Bearer meta-access-token")

	def test_08_adapter_reply_via_bridge_when_window_open(self):
		self._inbound("wamid.IN3")
		event = frappe.get_doc("Gateway Event", self._events("wamid.IN3")[0])
		before = self._outgoing_count()
		gateway = frappe.get_doc("Gateway", self.gateway.name)
		delivery = gateway_webhook.send_gateway_reply(gateway, event, "agent answer")
		self.assertTrue(delivery.provider_message_id)
		self.assertEqual(self._outgoing_count(), before + 1)
		self.assertEqual(len(self.meta.posts("/messages")), 1)
		self.assertEqual(self.meta.posts("/messages")[0]["body"]["context"]["message_id"], "wamid.IN3")

	def test_09_adapter_reply_refused_when_window_closed(self):
		gateway = frappe.get_doc("Gateway", self.gateway.name)
		event = SimpleNamespace(conversation_id=SENDER, provider_event_id="wamid.none")
		with self.assertRaises(WhatsAppWindowClosed):
			gateway_webhook.send_gateway_reply(gateway, event, "too late")
		self.assertEqual(self.meta.posts("/messages"), [])

	def test_10_create_sync_delete_template_lifecycle(self):
		created = self._make_template("fs_lifecycle")
		self.assertTrue(created["success"], created)
		name = created["name"]
		self.assertTrue(frappe.db.exists("WhatsApp Templates", name))
		self.assertEqual(created["id"], "meta-tpl-1")
		tpl_posts = self.meta.posts("/message_templates")
		self.assertEqual(len(tpl_posts), 1)
		self.assertEqual(tpl_posts[0]["body"]["name"], "fs_lifecycle")

		listed = mgmt.list_templates(account=self.account.name)
		self.assertIn(name, [t["name"] for t in listed["templates"]])

		self.meta.template_list = {"data": [{"name": "fs_lifecycle", "status": "APPROVED", "id": "meta-tpl-1"}]}
		synced = mgmt.sync_templates(account=self.account.name)
		self.assertEqual((synced["success"], synced["updated"]), (True, 1), synced)
		self.assertEqual(frappe.db.get_value("WhatsApp Templates", name, "status"), "APPROVED")
		self.assertEqual(self.meta.calls[-1]["method"], "GET")

		deleted = mgmt.delete_template(name=name)
		self.assertTrue(deleted["success"], deleted)
		self.assertFalse(frappe.db.exists("WhatsApp Templates", name))
		self.assertTrue([c for c in self.meta.calls if c["method"] == "DELETE"])

	def test_11_send_template_message_posts_name_and_parameters(self):
		created = self._make_template("fs_send_tpl")
		self.assertTrue(created["success"], created)
		frappe.db.set_value(
			"WhatsApp Templates", created["name"], {"status": "APPROVED", "sample_values": "Ann"}
		)
		self.meta.calls.clear()
		result = mgmt.send_template_message(
			to="+1 555 123 4567", template_name="fs_send_tpl", body_params=json.dumps(["Ann"]), account=self.account.name
		)
		self.assertTrue(result["success"], result)
		# regression: the Link field holds the template document name, not the bare Meta name
		self.assertEqual(frappe.db.get_value("WhatsApp Message", result["name"], "template"), created["name"])
		posts = self.meta.posts("/messages")
		self.assertEqual(len(posts), 1)
		body = posts[0]["body"]
		self.assertEqual(body["type"], "template")
		self.assertEqual(body["to"], SENDER)
		self.assertEqual(body["template"]["name"], "fs_send_tpl")
		self.assertIn({"type": "text", "text": "Ann"}, body["template"]["components"][0]["parameters"])

	def test_11b_trusted_template_send_skips_user_permission_check(self):
		created = self._make_template("fs_trusted_tpl")
		frappe.db.set_value("WhatsApp Templates", created["name"], "status", "APPROVED")
		frappe.set_user(EXEC_USER)
		try:
			with self.assertRaises(frappe.PermissionError):
				bridge.send_template(self.account.name, SENDER, "fs_trusted_tpl")
			result = bridge.send_template(self.account.name, SENDER, "fs_trusted_tpl", trusted=True)
		finally:
			frappe.set_user("Administrator")
		self.assertTrue(result["success"], result)

	def test_12_send_text_message_window_policy(self):
		before = self._outgoing_count()
		closed = mgmt.send_text_message(gateway_name=self.gateway.name, to=SENDER, text="hello?")
		self.assertEqual((closed["success"], closed["error"]), (False, "window_closed"))
		self.assertEqual(self.meta.posts("/messages"), [])
		self.assertEqual(self._outgoing_count(), before)

		self._inbound("wamid.IN4")
		opened = mgmt.send_text_message(gateway_name=self.gateway.name, to=SENDER, text="now open")
		self.assertTrue(opened["success"], opened)
		self.assertEqual(len(self.meta.posts("/messages")), 1)
		self.assertEqual(self._outgoing_count(), before + 1)


class TestWhatsAppCoreWithoutBridge(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		if bridge.is_available():
			self.skipTest("frappe_whatsapp is installed; these assertions cover the core-only site")

	def test_core_modules_import_without_frappe_whatsapp(self):
		for module in (
			"huf.ai.gateway_adapters.whatsapp_bridge",
			"huf.ai.gateway_adapters.whatsapp",
			"huf.ai.whatsapp_management",
			"huf.ai.gateway_webhook",
		):
			importlib.import_module(module)
		self.assertIsNone(bridge.resolve_account(None))
		self.assertFalse(bridge.forward_status({"field": "messages", "value": {"statuses": []}}))

	def test_management_api_reports_bridge_unavailable(self):
		status = mgmt.get_setup_status()
		self.assertTrue(status["success"])
		self.assertFalse(status["bridge_available"])
		self.assertEqual(status["accounts"], [])
		for result in (
			mgmt.list_templates(),
			mgmt.create_template(payload={"template_name": "x_y", "category": "UTILITY", "language_code": "en"}),
			mgmt.sync_templates(),
			mgmt.delete_template(name="x"),
			mgmt.send_template_message(to="15551234567", template_name="x_y"),
			mgmt.send_text_message(gateway_name="G", to="15551234567", text="hi"),
			mgmt.list_messages(),
			mgmt.list_conversations(),
		):
			self.assertEqual(result, {"success": False, "error": "bridge_unavailable"})

	def test_adapter_direct_mode_send_with_patched_http(self):
		calls = []

		def fake_post(url, *, json, headers, timeout):
			calls.append((url, json, headers))
			return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"messages": [{"id": "wamid.D1"}]})

		adapter = WhatsAppGatewayAdapter(
			{
				"phone_number_id": PHONE_ID,
				"access_token": "tok",
				"webhook_verify_token": "v",
				"app_secret": APP_SECRET,
			},
			http_post=fake_post,
			gateway_context={"whatsapp_account": "ignored-without-bridge"},
		)
		delivery = adapter.send_reply(GatewayReply(conversation_id="+1 555 123 4567", text="direct"))
		self.assertEqual(delivery.provider_message_id, "wamid.D1")
		self.assertEqual(len(calls), 1)
		url, body, headers = calls[0]
		self.assertTrue(url.endswith(f"/{PHONE_ID}/messages"))
		self.assertEqual((body["to"], body["text"]["body"]), (SENDER, "direct"))
		self.assertEqual(headers["Authorization"], "Bearer tok")

		tpl = adapter.send_template("15551234567", "welcome", "en_US", ["Ann"])
		self.assertEqual(tpl.provider_message_id, "wamid.D1")
		self.assertEqual(calls[1][1]["template"]["name"], "welcome")
