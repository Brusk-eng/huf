# Copyright (c) 2026, Huf and contributors
# For license information, please see license.txt

"""A WhatsApp gateway routed to a Flow, and a Flow sending WhatsApp.

There is no ``trigger.whatsapp`` node. The only gateway trigger is a Gateway route/default whose target is
a Flow: ``process_gateway_event`` then starts a Flow Run (``trigger_type == "Gateway"``) whose payload is
``{"gateway_event", "message"}``. Documented gap: that payload carries no sender id or conversation id, so a
Flow cannot address a reply to the sender without looking the Gateway Event up itself.

The send half drives the real ``huf.ai.tools.whatsapp`` tool from a ``tool.call`` node, so the free-form 24h
window / proactive gate of the tool applies exactly as it does for an agent. Only Meta (HTTP) is faked.
Needs frappe_whatsapp (skipped otherwise).
"""

from __future__ import annotations

import json

import frappe

from huf.ai import flow_api
from huf.ai.flow_engine import run_flow
from huf.ai.gateway_service import process_gateway_event
from huf.ai.tests import test_whatsapp_full_stack as fs
from huf.ai.tests.factories import make_agent_tool_function

LIMITS = {
	"max_nodes": 20,
	"max_rows": 1000,
	"max_output_bytes": 100_000,
	"max_parallel_calls": 1,
	"max_foreach_iterations": 1,
	"max_external_calls": 5,
	"max_writes": 0,
	"max_wall_time_ms": 5000,
	"fail_closed": True,
}


class TestFlowWhatsAppGatewayTrigger(fs.TestWhatsAppMultiGateway):
	# Inherit the two-gateway fixture only, not its tests.
	for _name in [n for n in dir(fs.TestWhatsAppMultiGateway) if n.startswith("test_")]:
		locals()[_name] = None
	del _name

	def setUp(self):
		super().setUp()
		self.tool = make_agent_tool_function(
			tool_name=f"_test_wa_flow_{frappe.generate_hash(length=8)}",
			types="App Provided",
			description="Test tool: WhatsApp actions.",
			function_path="huf.ai.tools.whatsapp.handle_action",
			params=json.dumps(
				{
					"type": "object",
					"properties": {k: {"type": "string"} for k in ("action", "gateway_name", "to", "message", "template_name")},
					"required": ["action"],
				}
			),
			is_read_only=0,
		)

	def _grant_flow_access(self):
		"""Flow Run create/write needs Huf User; the default "Huf Gateway User" role alone does not have it."""
		frappe.get_doc("User", fs.EXEC_USER).add_roles("Huf User")

	def _inbound(self, label, wamid, text="hello"):
		gateway, phone, waba, secret = (
			(self.gateway_a, self.PHONE_A, self.WABA_A, self.SECRET_A)
			if label == "A"
			else (self.gateway_b, self.PHONE_B, self.WABA_B, self.SECRET_B)
		)
		return self._post(gateway, self._inbound_payload(phone, waba, wamid, text), secret)

	def _flow(self, tool_input):
		flow_id = f"_test-wa-flow-{frappe.generate_hash(length=8)}"
		definition = {
			"schema_version": "1.0.0",
			"profile": "flow",
			"fingerprint": "0" * 64,
			"entry": "start",
			"nodes": [
				{"id": "start", "type": "trigger.webhook", "config": {"method": "POST"}, "next": "send"},
				{"id": "send", "type": "tool.call", "config": {"tool_id": self.tool.tool_name, "input": tool_input}},
			],
			"contract": {
				"input_schema": {"type": "object"},
				"output_schema": {"type": "object"},
				"applies_when": [],
				"permission_envelope": {"read": [], "write": [], "http": "none", "code": "none"},
				"limits": LIMITS,
			},
		}
		flow_api.save_flow_definition(flow_id, definition)
		return flow_id

	def _route_gateway_a_to(self, flow_id):
		frappe.db.set_value(
			"Gateway", self.gateway_a.name, {"default_target_type": "Flow", "default_flow": flow_id, "default_agent": None}
		)

	def _text_posts(self):
		return [
			p
			for p in self.meta.posts("/messages")
			if isinstance(p["body"], dict) and p["body"].get("type") in ("text", "template")
		]

	def _flow_run_for(self, wamid):
		event = frappe.get_doc("Gateway Event", self._events(self.gateway_a, wamid)[0])
		return event, frappe.get_doc("Flow Run", event.flow_run)

	def test_inbound_whatsapp_message_starts_a_flow_run(self):
		self._grant_flow_access()
		flow_id = self._flow({"action": "list_templates"})
		self._route_gateway_a_to(flow_id)
		self.assertTrue(self._inbound("A", "wamid.FLOW1", "start the flow")["success"])
		result = process_gateway_event(self._events(self.gateway_a, "wamid.FLOW1")[0])
		frappe.set_user("Administrator")
		self.assertEqual(result["status"], "Queued")
		event, flow_run = self._flow_run_for("wamid.FLOW1")
		self.assertEqual((event.target_type, event.target_flow), ("Flow", flow_id))
		self.assertEqual(flow_run.flow_id, flow_id)
		self.assertEqual(flow_run.trigger_type, "Gateway")
		payload = json.loads(flow_run.trigger_payload)
		self.assertEqual(payload, {"gateway_event": event.name, "message": "start the flow"})
		# documented gap: nothing in the payload identifies the sender or conversation
		self.assertNotIn("sender_id", payload)
		self.assertEqual(self.agent_calls, [], "a Flow-routed event must not run an agent")

	def test_flow_route_needs_flow_run_permission_for_the_execution_user(self):
		# Known gap: Gateway.validate only demands the Huf Gateway User role, which cannot create a Flow Run,
		# so a Flow route fails (the event is marked Failed, nothing sent) until the execution user also has Huf User.
		flow_id = self._flow({"action": "list_templates"})
		self._route_gateway_a_to(flow_id)
		self._inbound("A", "wamid.NOPERM")
		name = self._events(self.gateway_a, "wamid.NOPERM")[0]
		with self.assertRaises(frappe.PermissionError):
			process_gateway_event(name)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.count("Flow Run", {"flow_id": flow_id}), 0)

	def test_other_gateway_does_not_start_the_flow(self):
		self._grant_flow_access()
		flow_id = self._flow({"action": "list_templates"})
		self._route_gateway_a_to(flow_id)
		self._inbound("B", "wamid.FLOWB", "hello")
		process_gateway_event(self._events(self.gateway_b, "wamid.FLOWB")[0])
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.count("Flow Run", {"flow_id": flow_id}), 0)
		self.assertEqual(len(self.agent_calls), 1)  # B still routes to its agent

	def test_duplicate_wamid_starts_one_flow_run(self):
		self._grant_flow_access()
		flow_id = self._flow({"action": "list_templates"})
		self._route_gateway_a_to(flow_id)
		self._inbound("A", "wamid.DUPF")
		self.assertTrue(self._inbound("A", "wamid.DUPF").get("duplicates"))
		process_gateway_event(self._events(self.gateway_a, "wamid.DUPF")[0])
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.count("Flow Run", {"flow_id": flow_id}), 1)

	def _run_triggered_flow(self, wamid, tool_input):
		self._grant_flow_access()
		flow_id = self._flow(tool_input)
		self._route_gateway_a_to(flow_id)
		self._inbound("A", wamid)
		process_gateway_event(self._events(self.gateway_a, wamid)[0])
		frappe.set_user("Administrator")
		_event, flow_run = self._flow_run_for(wamid)
		run_flow(flow_run.name)
		flow_run.reload()
		return flow_run

	def _tool_output(self, flow_run):
		runs = frappe.get_all("Agent Run", filters={"flow_run": flow_run.name}, pluck="name")
		calls = frappe.get_all(
			"Agent Tool Call", filters={"agent_run": ["in", runs or [""]]}, fields=["tool_result", "error_message"]
		)
		return json.dumps(calls, default=str) + str(flow_run.last_error)

	def test_flow_free_form_send_is_allowed_while_the_window_is_open(self):
		# the inbound message that triggers the flow opens the 24h window of gateway A
		flow_run = self._run_triggered_flow(
			"wamid.W1",
			{"action": "send_message", "gateway_name": self.gateway_a.name, "to": fs.SENDER, "message": "from the flow"},
		)
		self.assertEqual(flow_run.status, "Success", flow_run.last_error)
		posts = self._text_posts()
		self.assertEqual(len(posts), 1, self.meta.calls)
		self.assertTrue(posts[0]["url"].endswith(f"/{self.PHONE_A}/messages"))
		self.assertEqual(posts[0]["body"]["text"]["body"], "from the flow")

	def test_flow_free_form_send_is_refused_when_the_window_is_closed(self):
		flow_run = self._run_triggered_flow(
			"wamid.W2",
			# gateway B never heard from this customer: its window is closed
			{"action": "send_message", "gateway_name": self.gateway_b.name, "to": fs.SENDER, "message": "too late"},
		)
		self.assertEqual(self._text_posts(), [], self.meta.calls)
		self.assertIn("proactive_send_not_permitted", self._tool_output(flow_run))

	def test_flow_cannot_send_proactively_from_gateway_a_to_a_stranger(self):
		flow_run = self._run_triggered_flow(
			"wamid.W3",
			{"action": "send_message", "gateway_name": self.gateway_a.name, "to": "15559990000", "message": "hi stranger"},
		)
		self.assertEqual(self._text_posts(), [], self.meta.calls)
		self.assertIn("proactive_send_not_permitted", self._tool_output(flow_run))
