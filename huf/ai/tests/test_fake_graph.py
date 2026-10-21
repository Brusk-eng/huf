# Copyright (c) 2026, Huf and contributors
# For license information, please see license.txt

"""Unit tests for the in-process fake Graph API (no network, no frappe site needed)."""

from __future__ import annotations

import unittest

import requests

from huf.ai.tests.fake_graph import FakeGraph

MESSAGES_URL = "https://graph.facebook.com/v21.0/1234567890/messages"


class FakeGraphTest(unittest.TestCase):
	def test_post_messages_records_call_and_numbers_wamids(self):
		graph = FakeGraph()
		first = graph.post(
			MESSAGES_URL,
			json={"to": "15551234567", "type": "text"},
			headers={"Authorization": "Bearer test"},
			timeout=15,
		)
		second = graph.post(f"{MESSAGES_URL}?access_token=redacted", json={"to": "15557654321"})

		self.assertEqual(first.status_code, 200)
		self.assertEqual(first.json(), {"messages": [{"id": "wamid.FAKE1"}]})
		self.assertEqual(second.json()["messages"][0]["id"], "wamid.FAKE2")

		self.assertEqual(len(graph.calls), 2)
		self.assertEqual(graph.calls[0]["method"], "POST")
		self.assertEqual(graph.calls[0]["url"], MESSAGES_URL)
		self.assertEqual(graph.calls[0]["body"]["to"], "15551234567")
		self.assertEqual(graph.calls[0]["timeout"], 15)
		self.assertEqual(len(graph.sent_messages()), 2)

	def test_window_closed_returns_meta_error_131047(self):
		graph = FakeGraph(window_closed=True)
		response = graph.post(MESSAGES_URL, json={"to": "15551234567"})

		self.assertEqual(response.status_code, 400)
		error = response.json()["error"]
		self.assertEqual(error["code"], 131047)
		self.assertIn("Re-engagement", error["message"])
		with self.assertRaises(requests.HTTPError):
			response.raise_for_status()

	def test_rate_limited_returns_meta_error_131056(self):
		graph = FakeGraph(rate_limited=True)
		response = graph.post(MESSAGES_URL, json={"to": "15551234567"})

		self.assertEqual(response.status_code, 400)
		self.assertEqual(response.json()["error"]["code"], 131056)
		self.assertEqual(len(graph.sent_messages()), 1)

	def test_non_message_requests_always_succeed(self):
		graph = FakeGraph(window_closed=True, rate_limited=True)
		upload = graph.post(
			"https://graph.facebook.com/v21.0/1234567890/media", json={"messaging_product": "whatsapp"}
		)
		templates = graph.get("https://graph.facebook.com/v21.0/9876543210/message_templates")

		self.assertEqual(upload.json(), {"success": True})
		self.assertEqual(templates.json(), {"success": True})
		self.assertEqual(graph.sent_messages(), [])

		not_a_send = graph.post(f"{MESSAGES_URL}_status", json={})
		self.assertEqual(not_a_send.status_code, 200)

	def test_window_closed_wins_and_reset_clears_state(self):
		graph = FakeGraph(window_closed=True, rate_limited=True)
		self.assertEqual(graph.post(MESSAGES_URL, json={}).json()["error"]["code"], 131047)

		graph.window_closed = False
		self.assertEqual(graph.post(MESSAGES_URL, json={}).json()["error"]["code"], 131056)

		graph.rate_limited = False
		graph.reset()
		self.assertEqual(graph.calls, [])
		self.assertEqual(graph.post(MESSAGES_URL, json={}).json()["messages"][0]["id"], "wamid.FAKE1")


if __name__ == "__main__":
	unittest.main()
