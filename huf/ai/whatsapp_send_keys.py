# Copyright (c) 2026, Huf and contributors
# For license information, please see license.txt

"""Idempotent agent/tool WhatsApp sends (WP-9, A-001).

A send is identified by a key: the caller's explicit ``idempotency_key``, else a hash of the run, the
SDK tool call id (when there is one), the action and the payload. The first call claims the key in the
database (unique row, committed before anything is sent), sends, and stores the original result and
wamid. A repeat returns that result with ``deduplicated: true`` and sends nothing. A failed or refused
send releases its claim so a retry is evaluated afresh (policy such as STOP still applies to it). A claim
still ``Pending`` is an unfinished send of unknown outcome: the repeat is refused (``send_in_progress``)
rather than risk a second message. Keys expire after :data:`TTL_HOURS` (:data:`PENDING_TTL_MINUTES` for an
unfinished claim) and are purged daily.

Because the key includes the tool call id, two distinct tool calls with the same text are both sent.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

import frappe
from frappe.utils import add_to_date, now_datetime

DOCTYPE = "WhatsApp Send Key"
TTL_HOURS = 24
PENDING_TTL_MINUTES = 15
MAX_KEY_LENGTH = 200

# Run/identity plumbing that is not part of what the message says.
_NON_PAYLOAD = {
	"gateway_name", "agent_run", "agent_run_id", "conversation", "conversation_id", "agent_name",
	"call_id", "idempotency_key", "ignore_permissions",
}


class InvalidKey(ValueError):
	pass


def explicit_key(kwargs: dict) -> str:
	"""The caller's ``idempotency_key`` ('' when absent); raises :class:`InvalidKey` when unusable."""
	raw = kwargs.get("idempotency_key")
	if raw is None:
		return ""
	value = str(raw).strip()
	if len(value) > MAX_KEY_LENGTH:
		raise InvalidKey(f"idempotency_key must be at most {MAX_KEY_LENGTH} characters")
	return value


def derive_key(gateway: str, run_id: str | None, call_id: str | None, action: str, kwargs: dict, explicit: str = "") -> str:
	"""Stable key for one send. Scoped to the gateway; an explicit key ignores run, call and payload."""
	if explicit:
		material: Any = ["explicit", explicit]
	else:
		payload = {k: v for k, v in kwargs.items() if k not in _NON_PAYLOAD}
		for field in ("to", "recipient", "phone_number"):
			if payload.get(field):
				from huf.ai.gateway_adapters import whatsapp_bridge as bridge

				payload[field] = bridge.normalize_phone(payload[field])
		material = ["auto", run_id or "", call_id or "", action, payload]
	blob = json.dumps([gateway, material], sort_keys=True, default=str, ensure_ascii=False)
	return hashlib.sha256(blob.encode()).hexdigest()


def _claim(gateway: str, key: str, action: str = "") -> bool:
	"""Insert the Pending claim and commit it. False when a live claim already exists."""
	_drop_if_expired(key)
	try:
		frappe.get_doc({
			"doctype": DOCTYPE, "key_hash": key, "gateway": gateway, "action": action, "status": "Pending",
			"claimed_at": now_datetime(), "expires_at": add_to_date(now_datetime(), minutes=PENDING_TTL_MINUTES),
		}).insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		return False
	frappe.db.commit()
	return True


def _drop_if_expired(key: str) -> None:
	expires = frappe.db.get_value(DOCTYPE, key, "expires_at")
	if expires and expires < now_datetime():
		frappe.db.delete(DOCTYPE, {"name": key})


def _release(key: str) -> None:
	frappe.db.delete(DOCTYPE, {"name": key})
	frappe.db.commit()


def _message_id(result: dict) -> str:
	data = result.get("data") if isinstance(result.get("data"), dict) else result
	return str((data or {}).get("message_id") or "")


def _finish(key: str, raw: str, result: dict) -> None:
	frappe.db.set_value(DOCTYPE, key, {
		"status": "Sent", "result": raw, "message_id": _message_id(result),
		"expires_at": add_to_date(now_datetime(), hours=TTL_HOURS),
	}, update_modified=False)
	frappe.db.commit()


def run_once(gateway: str, key: str, action: str, send: Callable[[], str]) -> str:
	"""Run ``send`` (returns the tool's JSON) at most once per ``key``."""
	if not _claim(gateway, key, action):
		row = frappe.db.get_value(DOCTYPE, key, ["status", "result"], as_dict=True)
		if row and row.status == "Sent" and row.result:
			original = json.loads(row.result)
			original["deduplicated"] = True
			if isinstance(original.get("data"), dict):
				original["data"]["deduplicated"] = True
			return json.dumps(original, default=str)
		return json.dumps({
			"success": False, "error": "send_in_progress",
			"message": "An identical send is already in progress or its outcome is unknown; it was not sent again.",
		})
	try:
		raw = send()
	except BaseException:
		_release(key)
		raise
	try:
		result = json.loads(raw)
	except ValueError:
		result = {}
	if isinstance(result, dict) and result.get("success"):
		_finish(key, raw, result)
	else:
		_release(key)
	return raw


def purge_expired() -> None:
	frappe.db.delete(DOCTYPE, {"expires_at": ("<", now_datetime())})
