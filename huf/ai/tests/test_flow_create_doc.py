"""``doc.create`` node: insert a document of an allow-listed doctype as the run's user."""

import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from huf.ai import flow_create_doc as cd
from huf.ai import flow_engine
from huf.ai.graph.executor import GraphContext, PinnedVersion

FIELDS = [
	NS(fieldname="description", fieldtype="Text Editor", read_only=0),
	NS(fieldname="priority", fieldtype="Select", read_only=0),
	NS(fieldname="status", fieldtype="Select", read_only=0),
	NS(fieldname="locked", fieldtype="Data", read_only=1),
	NS(fieldname="items", fieldtype="Table", read_only=0),
	NS(fieldname="section", fieldtype="Section Break", read_only=0),
]


class TestPureRules(unittest.TestCase):
	def test_default_allowlist_is_todo(self):
		self.assertEqual(cd.allowed_doctypes({}), ["ToDo"])

	def test_configured_allowlist(self):
		self.assertEqual(cd.allowed_doctypes({cd.CONF_KEY: ["ToDo", "Note"]}), ["ToDo", "Note"])
		self.assertEqual(cd.allowed_doctypes({cd.CONF_KEY: "bad"}), ["ToDo"])

	def test_check_doctype(self):
		cd.check_doctype("ToDo", ["ToDo"])
		for bad in ("User", "", None, 5):
			with self.assertRaises(cd.CreateDocError, msg=repr(bad)):
				cd.check_doctype(bad, ["ToDo"])

	def test_validate_fields_ok(self):
		out = cd.validate_fields(FIELDS, {"description": "hi", "priority": "High", "status": None})
		self.assertEqual(out, {"description": "hi", "priority": "High", "status": None})

	def test_validate_fields_rejects(self):
		for bad in (
			{"nope": 1},
			{"locked": "x"},
			{"items": []},
			{"section": "x"},
			{"name": "x"},
			{"owner": "x"},
			{"docstatus": 1},
			{"doctype": "User"},
			{"description": {"a": 1}},
			{"description": ["a"]},
			{"description": "x" * (cd.MAX_FIELD_CHARS + 1)},
		):
			with self.assertRaises(cd.CreateDocError, msg=repr(bad)):
				cd.validate_fields(FIELDS, bad)

	def test_fields_must_be_object(self):
		with self.assertRaises(cd.CreateDocError):
			cd.validate_fields(FIELDS, ["a"])

	def test_registered(self):
		self.assertIn("doc.create", flow_engine._NODE_EXECUTORS)


class _Run:
	flow_id = "f"
	name = "FR-1"


def _settings(data=None, max_writes=5):
	graph = {"schema_version": "1.0.0", "profile": "flow", "entry": "n", "nodes": [], "contract": {"limits": {"max_writes": max_writes}}}
	return flow_engine.FlowRunContext(context=GraphContext(data or {}), version=PinnedVersion.pin(graph))


def _run(config, data=None, max_writes=5, settings=None):
	node = {"id": "n", "type": "doc.create", "config": config}
	return flow_engine._exec_doc_create(_Run(), node, config, settings or _settings(data, max_writes))


class TestDocCreateNode(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.marker = f"_flowtest {frappe.generate_hash(length=8)}"

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete("ToDo", {"description": ["like", "_flowtest %"]})
		frappe.db.commit()

	def test_creates_todo_and_returns_name(self):
		r = _run({"doctype": "ToDo", "fields": {"description": {"$from": "input.text"}}}, {"text": self.marker})
		self.assertEqual(r["status"], "success", r)
		name = r["output"]["name"]
		self.assertEqual(r["output"]["doctype"], "ToDo")
		self.assertEqual(frappe.db.get_value("ToDo", name, "description"), self.marker)
		self.assertEqual(frappe.db.get_value("ToDo", name, "owner"), "Administrator")

	def test_template_in_fields(self):
		cfg = {"doctype": "ToDo", "fields": {"description": {"$template": "_flowtest order #{o}", "vars": {"o": {"$from": "input.o"}}}}}
		r = _run(cfg, {"o": "77"})
		self.assertEqual(frappe.db.get_value("ToDo", r["output"]["name"], "description"), "_flowtest order #77")

	def test_doctype_not_allowlisted_fails(self):
		r = _run({"doctype": "User", "fields": {"email": "a@b.c"}})
		self.assertEqual(r["status"], "failed")
		self.assertIn("allow", r["error"])

	def test_site_config_allowlist_extends(self):
		with patch.object(frappe, "conf", frappe._dict({cd.CONF_KEY: ["Note"]})):
			r = _run({"doctype": "ToDo", "fields": {"description": self.marker}})
		self.assertEqual(r["status"], "failed")

	def test_bad_field_fails_without_inserting(self):
		before = frappe.db.count("ToDo")
		r = _run({"doctype": "ToDo", "fields": {"description": self.marker, "bogus": 1}})
		self.assertEqual(r["status"], "failed")
		self.assertEqual(frappe.db.count("ToDo"), before)

	def test_missing_config_fails(self):
		self.assertEqual(_run({"fields": {}})["status"], "failed")
		self.assertEqual(_run({"doctype": "ToDo"})["status"], "failed")
		self.assertEqual(_run({"doctype": "ToDo", "fields": {}, "ignore_permissions": True})["status"], "failed")

	def test_max_writes_is_enforced_per_run(self):
		settings = _settings(max_writes=1)
		cfg = {"doctype": "ToDo", "fields": {"description": self.marker}}
		self.assertEqual(_run(cfg, settings=settings)["status"], "success")
		second = _run(cfg, settings=settings)
		self.assertEqual(second["status"], "failed")
		self.assertIn("max_writes", second["error"])

	def test_zero_max_writes_blocks(self):
		self.assertEqual(_run({"doctype": "ToDo", "fields": {"description": self.marker}}, max_writes=0)["status"], "failed")

	def test_runs_with_session_user_permissions_no_ignore(self):
		# Flow Definition is allow-listed on purpose; a Huf User cannot create one, so the node must fail.
		email = "flow-createdoc-test@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc({"doctype": "User", "email": email, "first_name": "FlowCD", "send_welcome_email": 0, "roles": [{"role": "Huf User"}]}).insert(ignore_permissions=True)
		frappe.set_user(email)
		try:
			with patch.object(frappe, "conf", frappe._dict({cd.CONF_KEY: ["Flow Definition"]})):
				r = _run({"doctype": "Flow Definition", "fields": {"flow_id": "_flowtest-denied"}})
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(r["status"], "failed")
		self.assertIn("ermission", r["error"])
		self.assertFalse(frappe.db.exists("Flow Definition", "_flowtest-denied"))


if __name__ == "__main__":
	unittest.main()
