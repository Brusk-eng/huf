"""Unit tests for the optional frappe_whatsapp bridge (mock-based)."""

import sys
import unittest
from datetime import datetime, timedelta
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from huf.ai.gateway_adapters import whatsapp_bridge as wb

NOW = datetime(2026, 1, 10, 12, 0, 0)
ACCT = {
	"name": "ACC",
	"phone_id": "111",
	"business_id": "BIZ",
	"app_id": "APP",
	"token": "tok",
	"url": "https://graph.facebook.com",
	"version": "v21.0",
	"webhook_verify_token": "v",
	"status": "Active",
}


class FakeResp:
	def __init__(self, json_data=None, content=b""):
		self._j = json_data or {}
		self.content = content

	def raise_for_status(self):
		pass

	def json(self):
		return self._j


class FakeDoc(SimpleNamespace):
	def get(self, key, default=None):
		return getattr(self, key, default)

	def get_password(self, field):
		return "secret"

	def insert(self, **kwargs):
		self.insert_kwargs = kwargs
		self.name = "WAM-1"
		self.message_id = "wamid.X"
		self.status = "Success"
		return self


def avail(value=True):
	return patch.object(wb, "is_available", return_value=value)


class TestBridge(unittest.TestCase):
	def test_constants_and_import_without_app(self):
		self.assertEqual(wb.GRAPH_DEFAULT_VERSION, "v21.0")
		# The bench may load frappe_whatsapp itself; the bridge must never import it at module level.
		import ast, inspect

		tree = ast.parse(inspect.getsource(wb))
		top_level = [
			(getattr(n, "module", None) or "") + "|" + ",".join(a.name for a in n.names)
			for n in tree.body
			if isinstance(n, (ast.Import, ast.ImportFrom))
		]
		self.assertFalse([i for i in top_level if "frappe_whatsapp" in i])

	def test_is_available(self):
		with patch.object(frappe.db, "exists", side_effect=[True, True, True]):
			self.assertTrue(wb.is_available())
		with patch.object(frappe.db, "exists", side_effect=[True, False, True]):
			self.assertFalse(wb.is_available())

	def test_normalize_phone_table(self):
		for raw, want in [
			("+1 (415) 555-2671", "14155552671"),
			("0044 20 7946 0958", "442079460958"),
			("91-98765 43210", "919876543210"),
			("", ""),
			(None, ""),
			("abc", ""),
			(14155552671, "14155552671"),
		]:
			self.assertEqual(wb.normalize_phone(raw), want)

	def test_resolve_account(self):
		doc = FakeDoc(name="ACC", phone_id="111", business_id="B", app_id="A", url="", version="", webhook_verify_token="v", status="Active")
		with avail(), patch.object(frappe.db, "get_value", return_value="ACC"), patch.object(
			frappe.db, "exists", return_value=True
		), patch.object(frappe, "get_doc", return_value=doc):
			acct = wb.resolve_account()
		self.assertEqual(acct["token"], "secret")
		self.assertEqual(acct["version"], "v21.0")
		self.assertEqual(acct["url"], "https://graph.facebook.com")

	def test_resolve_account_none_and_unavailable(self):
		with avail(), patch.object(frappe.db, "get_value", return_value=None):
			self.assertEqual(wb.resolve_account(), {"error": "no_account"})
		with avail(False):
			self.assertIsNone(wb.resolve_account())

	def test_account_matches_phone_id(self):
		with avail(), patch.object(frappe.db, "get_value", return_value="111"):
			self.assertTrue(wb.account_matches_phone_id("ACC", "111"))
			self.assertFalse(wb.account_matches_phone_id("ACC", "222"))
			self.assertFalse(wb.account_matches_phone_id("ACC", ""))
		with avail(False):
			self.assertFalse(wb.account_matches_phone_id("ACC", "111"))

	def _window(self, rows):
		with patch.object(frappe, "get_all", return_value=rows) as ga:
			res = wb.window_state("GW", "+1 415", now=NOW)
		return res, ga

	def test_window_boundaries(self):
		res, ga = self._window([{"received_at": NOW - timedelta(hours=23, minutes=59), "verified_sender": 1}])
		self.assertTrue(res["open"])
		self.assertEqual(ga.call_args.args[0], "Gateway Event")
		self.assertEqual(ga.call_args.kwargs["filters"]["sender_id"], "1415")
		res, _ = self._window([{"received_at": NOW - timedelta(hours=24, minutes=1), "verified_sender": 1}])
		self.assertFalse(res["open"])
		res, _ = self._window([{"received_at": NOW - timedelta(hours=1), "verified_sender": 0}])
		self.assertFalse(res["open"])
		self.assertIsNone(res["last_inbound_at"])
		res, _ = self._window([])
		self.assertFalse(res["open"])

	def test_record_incoming_idempotent(self):
		msg = {"id": "wamid.1", "from": "+1 415", "type": "text", "text": {"body": "hi"}}
		with avail(False):
			self.assertIsNone(wb.record_incoming("ACC", msg))
		with avail(), patch.object(frappe.db, "get_value", return_value="WAM-9"), patch.object(frappe, "get_doc") as gd:
			self.assertEqual(wb.record_incoming("ACC", msg), "WAM-9")
			gd.assert_not_called()
		doc = MagicMock()
		doc.name = "WAM-2"
		with avail(), patch.object(frappe.db, "get_value", return_value=None), patch.object(frappe, "get_doc", return_value=doc) as gd:
			self.assertEqual(wb.record_incoming("ACC", msg, "Bob"), "WAM-2")
			values = gd.call_args.args[0]
			self.assertEqual(values["type"], "Incoming")
			self.assertEqual(values["from"], "1415")
			self.assertEqual(values["message"], "hi")

	def test_send_text(self):
		doc = FakeDoc()
		with avail(), patch.object(wb, "resolve_account", return_value=ACCT), patch.object(frappe, "get_doc", return_value=doc) as gd:
			res = wb.send_text("ACC", "+1 415", "yo", reply_to="w1")
		self.assertEqual(res["message_id"], "wamid.X")
		self.assertEqual(doc.insert_kwargs, {"ignore_permissions": False})
		self.assertEqual(gd.call_args.args[0]["reply_to_message_id"], "w1")
		doc2 = FakeDoc()
		with avail(), patch.object(wb, "resolve_account", return_value=ACCT), patch.object(frappe, "get_doc", return_value=doc2):
			wb.send_text("ACC", "1", "x", trusted=True)
		self.assertEqual(doc2.insert_kwargs, {"ignore_permissions": True})

	def test_send_text_errors(self):
		with avail(False):
			with self.assertRaises(wb.BridgeError) as cm:
				wb.send_text("ACC", "1", "x")
			self.assertEqual(cm.exception.payload["error"], "bridge_unavailable")
		with avail(), patch.object(wb, "resolve_account", return_value={"error": "no_account"}):
			with self.assertRaises(wb.BridgeError) as cm:
				wb.send_text("ACC", "1", "x")
			self.assertEqual(cm.exception.payload["error"], "no_account")
		bad = FakeDoc()
		bad.insert = MagicMock(side_effect=RuntimeError("boom"))
		with avail(), patch.object(wb, "resolve_account", return_value=ACCT), patch.object(frappe, "get_doc", return_value=bad):
			with self.assertRaises(wb.BridgeError) as cm:
				wb.send_text("ACC", "1", "x")
			self.assertEqual(cm.exception.payload["error"], "send_failed")

	def test_send_template(self):
		doc = FakeDoc()
		with avail(), patch.object(wb, "resolve_account", return_value=ACCT), patch.object(frappe, "get_doc", return_value=doc) as gd:
			wb.send_template("ACC", "+1 415", "hello_tpl", ["a", "b"], ["h"])
		v = gd.call_args.args[0]
		self.assertEqual(v["use_template"], 1)
		self.assertEqual(v["template"], "hello_tpl")
		self.assertEqual(v["template_parameters"], "a,b")
		self.assertEqual(v["template_header_parameters"], "h")
		self.assertEqual(doc.insert_kwargs, {})

	def test_list_templates(self):
		with avail(False):
			self.assertEqual(wb.list_templates(), [])
		with avail(), patch.object(frappe, "get_list", return_value=[{"name": "t"}]) as gl:
			self.assertEqual(wb.list_templates("ACC", "APPROVED"), [{"name": "t"}])
			self.assertEqual(gl.call_args.kwargs["filters"], {"whatsapp_account": "ACC", "status": "APPROVED"})

	def test_create_template(self):
		good = {"template_name": "promo_1", "category": "marketing", "language_code": "en_US", "template": "Hi {{1}}"}
		with avail(False):
			self.assertEqual(wb.create_template("ACC", good)["error"], "bridge_unavailable")
		with avail(), patch.object(frappe, "has_permission", return_value=False):
			with self.assertRaises(frappe.PermissionError):
				wb.create_template("ACC", good)
		with avail(), patch.object(frappe, "has_permission", return_value=True):
			self.assertEqual(wb.create_template("ACC", {**good, "template_name": "Bad Name"})["error"], "invalid_name")
			self.assertEqual(wb.create_template("ACC", {**good, "category": "X"})["error"], "invalid_category")
			self.assertEqual(wb.create_template("ACC", {**good, "language_code": ""})["error"], "invalid_language")
			doc = FakeDoc(status="PENDING", id="1")
			with patch.object(frappe, "get_doc", return_value=doc) as gd:
				res = wb.create_template("ACC", good)
			self.assertTrue(res["success"])
			self.assertEqual(gd.call_args.args[0]["category"], "MARKETING")

	def test_delete_template(self):
		with avail(False):
			self.assertEqual(wb.delete_template("T")["error"], "bridge_unavailable")
		with avail(), patch.object(frappe, "has_permission", return_value=False):
			with self.assertRaises(frappe.PermissionError):
				wb.delete_template("T")
		with avail(), patch.object(frappe, "has_permission", return_value=True), patch.object(
			frappe.db, "exists", return_value=True
		), patch.object(frappe, "delete_doc") as dd:
			self.assertTrue(wb.delete_template("T")["success"])
			dd.assert_called_once_with("WhatsApp Templates", "T")

	def test_sync_templates(self):
		calls = []

		def http_get(url, **kw):
			calls.append((url, kw))
			return FakeResp({"data": [{"name": "promo", "status": "APPROVED", "id": "9"}, {"name": "zz", "status": "X", "id": "8"}]})

		def get_value(dt, filt, field):
			return "T1" if filt.get("actual_name") == "promo" else None

		with avail(), patch.object(frappe, "has_permission", return_value=True), patch.object(
			wb, "resolve_account", return_value=ACCT
		), patch.object(frappe.db, "get_value", side_effect=get_value), patch.object(frappe.db, "set_value") as sv:
			res = wb.sync_templates("ACC", http_get=http_get)
		self.assertEqual(res, {"success": True, "seen": 2, "updated": 1})
		self.assertEqual(calls[0][0], "https://graph.facebook.com/v21.0/BIZ/message_templates")
		self.assertEqual(calls[0][1]["headers"]["Authorization"], "Bearer tok")
		sv.assert_called_once_with("WhatsApp Templates", "T1", {"status": "APPROVED", "id": "9"})
		with avail(False):
			self.assertEqual(wb.sync_templates("ACC")["error"], "bridge_unavailable")

	def test_forward_status(self):
		mod = ModuleType("frappe_whatsapp.utils.webhook")
		mod.update_status = MagicMock()
		msg_change = {"field": "messages", "value": {"statuses": [{"id": "w1"}]}}
		tpl_change = {"field": "message_template_status_update", "value": {}}
		with avail(), patch.dict(sys.modules, {"frappe_whatsapp.utils.webhook": mod}):
			with patch.object(frappe.db, "exists", return_value=False):
				self.assertFalse(wb.forward_status(msg_change))
			mod.update_status.assert_not_called()
			with patch.object(frappe.db, "exists", return_value=True):
				self.assertTrue(wb.forward_status(msg_change))
			self.assertTrue(wb.forward_status(tpl_change))
			mod.update_status.side_effect = RuntimeError("x")
			self.assertFalse(wb.forward_status(tpl_change))
		with avail(False):
			self.assertFalse(wb.forward_status(tpl_change))
		with avail():  # module missing -> ImportError swallowed
			with patch.object(wb.importlib, "import_module", side_effect=ImportError):
				self.assertFalse(wb.forward_status(tpl_change))

	def _media_get(self, meta, content=b"data"):
		def http_get(url, **kw):
			if url.endswith("/M1"):
				return FakeResp(meta)
			return FakeResp(content=content)

		return http_get

	def test_download_media(self):
		meta = {"mime_type": "image/jpeg", "url": "https://cdn/x", "file_size": 4, "filename": "../a b.jpg"}
		with patch.object(wb, "resolve_account", return_value=ACCT):
			res = wb.download_media("ACC", "M1", http_get=self._media_get(meta))
			self.assertEqual(res["content"], b"data")
			self.assertEqual(res["mime_type"], "image/jpeg")
			self.assertNotIn("/", res["filename"])
			with self.assertRaises(ValueError):
				wb.download_media("ACC", "M1", http_get=self._media_get({**meta, "mime_type": "application/x-msdownload"}))
			with self.assertRaises(ValueError):
				wb.download_media("ACC", "M1", max_bytes=2, http_get=self._media_get(meta))
			with self.assertRaises(ValueError):
				wb.download_media("ACC", "M1", max_bytes=2, http_get=self._media_get({**meta, "file_size": None}))
		with patch.object(wb, "resolve_account", return_value={"error": "no_account"}):
			with self.assertRaises(ValueError):
				wb.download_media("ACC", "M1", http_get=self._media_get(meta))

	def test_list_messages_and_conversations(self):
		with avail(False):
			self.assertEqual(wb.list_messages(), {"success": False, "error": "bridge_unavailable"})
			self.assertEqual(wb.list_conversations(), {"success": False, "error": "bridge_unavailable"})
		rows = [
			{"type": "Incoming", "from": "111", "to": "", "profile_name": "Al"},
			{"type": "Outgoing", "from": "", "to": "111"},
			{"type": "Outgoing", "from": "", "to": "222"},
		]
		with avail(), patch.object(frappe, "get_list", return_value=rows) as gl:
			res = wb.list_messages("ACC", "+111", 10)
			self.assertEqual(len(res["messages"]), 3)
			self.assertEqual(gl.call_args.kwargs["or_filters"], {"to": "111", "from": "111"})
			convs = wb.list_conversations("ACC", 10)["conversations"]
			self.assertEqual({c["phone"]: c["count"] for c in convs}, {"111": 2, "222": 1})
			self.assertEqual(convs[0]["profile_name"], "Al")


if __name__ == "__main__":
	unittest.main()
