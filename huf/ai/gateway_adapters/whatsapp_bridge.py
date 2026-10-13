# Copyright (c) 2026, Huf and contributors
# For license information, please see license.txt

"""Optional bridge between the Huf WhatsApp gateway and ``frappe_whatsapp``.

This is the only Huf module that touches frappe_whatsapp doctypes. They are
addressed by DocType name behind :func:`is_available`; frappe_whatsapp is
never imported at module import time (only lazily, inside :func:`forward_status`).

Structured errors: functions that can fail for expected reasons return or
raise a dict-shaped error ``{"success": False, "error": "<code>", ...}``.
``resolve_account`` returns ``{"error": "no_account"}`` when no account exists.
"""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlparse

import frappe
import frappe.utils

from huf.ai.gateway_adapters import whatsapp_templates as wt

GRAPH_HOST = "https://graph.facebook.com"
# Newest Graph version that is >= ~3 months old (v25.0 released 2026-02-18; v26.0 is too new).
GRAPH_DEFAULT_VERSION = "v25.0"
WINDOW_HOURS = 24
_MB = 1024 * 1024
# Meta's inbound limits: documents 100 MB, images 5 MB, audio/video 16 MB, stickers 500 KB.
MEDIA_LIMITS_BY_KIND = {
	"document": 100 * _MB,
	"image": 5 * _MB,
	"audio": 16 * _MB,
	"video": 16 * _MB,
	"sticker": 500 * 1024,
}
DEFAULT_MAX_MEDIA_BYTES = MEDIA_LIMITS_BY_KIND["document"]  # absolute ceiling; per-kind limits apply
DEFAULT_MIME_PREFIXES = (
	"image/",
	"audio/",
	"video/",
	"application/pdf",
	"text/plain",
	"application/msword",
	"application/vnd.openxmlformats-officedocument.",
	"application/vnd.ms-excel",
	"application/vnd.ms-powerpoint",
)
# Never allowed even though a prefix above would match (active content).
_BLOCKED_MIME_PREFIXES = ("image/svg", "text/html", "application/xhtml")
# AUTHENTICATION is intentionally unsupported: Meta requires OTP button components.
TEMPLATE_CATEGORIES = ("MARKETING", "UTILITY")
FORWARD_RETRY_DELAY_SECONDS = 5
_TEMPLATE_NAME_RE = re.compile(r"^[a-z0-9_]+$")
_HTTP_TIMEOUT = 15
_MEDIA_HOST_SUFFIXES = ("fbcdn.net", "facebook.com", "whatsapp.net", "fbsbx.com")
_SECRET_QS_RE = re.compile(r"(?i)((?:access_)?token=)[^&\s\"'<>]+")
_URL_RE = re.compile(r"https?://[^\s\"'<>]+")


def _redact(text: Any) -> str:
	"""Strip access tokens and full URLs from text destined for errors/logs."""
	out = _SECRET_QS_RE.sub(r"\1[redacted]", str(text))
	return _URL_RE.sub("[url]", out)


def _test_hosts() -> set[str]:
	"""Extra ``host:port`` entries, honoured ONLY while running tests.

	``frappe.flags.whatsapp_test_media_hosts`` lets integration tests point media
	downloads at a local fake Graph server. It is ignored unless
	``frappe.flags.in_test`` is set, so production allowlists cannot be widened
	through it.
	"""
	if not getattr(frappe.flags, "in_test", False):
		return set()
	return {str(h).lower() for h in (getattr(frappe.flags, "whatsapp_test_media_hosts", None) or ())}


def graph_base_url(override: str | None = None) -> str:
	"""Graph API host; ``override`` (a credential) is honoured only while running tests."""
	if override and getattr(frappe.flags, "in_test", False):
		return str(override).strip().rstrip("/")
	return GRAPH_HOST


def _is_media_host(url: str) -> bool:
	parsed = urlparse(url)
	host = (parsed.hostname or "").lower()
	if host and parsed.netloc.lower() in _test_hosts():
		return parsed.scheme in ("http", "https")
	if parsed.scheme != "https" or not host:
		return False
	return any(host == suf or host.endswith("." + suf) for suf in _MEDIA_HOST_SUFFIXES)


class BridgeError(Exception):
	"""Structured bridge failure; ``payload`` is the error dict."""

	def __init__(self, payload: dict[str, Any]):
		super().__init__(payload.get("error") or "bridge_error")
		self.payload = payload


def _err(code: str, **extra: Any) -> dict[str, Any]:
	return {"success": False, "error": code, **extra}


def _default_get(*args: Any, **kwargs: Any) -> Any:
	import requests

	kwargs.setdefault("timeout", _HTTP_TIMEOUT)
	return requests.get(*args, **kwargs)


def is_available() -> bool:
	"""Return True when frappe_whatsapp's required DocTypes are installed."""
	try:
		return all(
			frappe.db.exists("DocType", dt)
			for dt in ("WhatsApp Message", "WhatsApp Templates", "WhatsApp Account")
		)
	except Exception:
		return False


def normalize_phone(raw: Any) -> str:
	"""Return digits only (no +, spaces, dashes, parens, leading ``00``)."""
	digits = re.sub(r"\D", "", str(raw or ""))
	if digits.startswith("00"):
		digits = digits[2:]
	return digits


def resolve_account(name: str | None = None) -> dict[str, Any] | None:
	"""Resolve a WhatsApp Account to a plain dict.

	With ``name=None`` the default outgoing account is used, else the first
	Active one. Returns ``None`` when the bridge is unavailable and
	``{"error": "no_account"}`` when no matching account exists. Never raises
	for a missing account.
	"""
	if not is_available():
		return None
	if not name:
		name = frappe.db.get_value("WhatsApp Account", {"is_default_outgoing": 1}, "name")
		if not name:
			name = frappe.db.get_value("WhatsApp Account", {"status": "Active"}, "name")
	if not name or not frappe.db.exists("WhatsApp Account", name):
		return {"error": "no_account"}
	doc = frappe.get_doc("WhatsApp Account", name)
	return {
		"name": doc.name,
		"phone_id": doc.get("phone_id"),
		"business_id": doc.get("business_id"),
		"app_id": doc.get("app_id"),
		"token": doc.get_password("token"),
		"url": (doc.get("url") or "https://graph.facebook.com").rstrip("/"),
		"version": doc.get("version") or GRAPH_DEFAULT_VERSION,
		"webhook_verify_token": doc.get("webhook_verify_token"),
		"status": doc.get("status"),
	}


def _account_or_error(account_name: str | None) -> dict[str, Any]:
	if not is_available():
		raise BridgeError(_err("bridge_unavailable"))
	acct = resolve_account(account_name)
	if not acct or acct.get("error"):
		raise BridgeError(_err("no_account"))
	return acct


def account_matches_phone_id(account_name: str, phone_number_id: str) -> bool:
	"""True iff the account's phone_id equals the payload phone_number_id (A11)."""
	if not account_name or not phone_number_id or not is_available():
		return False
	try:
		phone_id = frappe.db.get_value("WhatsApp Account", account_name, "phone_id")
	except Exception:
		return False
	return bool(phone_id) and str(phone_id) == str(phone_number_id)


def message_time(raw_payload: Any) -> datetime | None:
	"""Return the provider's own message timestamp as a naive system-time datetime.

	Meta stamps ``messages[].timestamp`` in epoch seconds (UTC). ``received_at``
	is stored with ``now_datetime()`` (naive, system timezone), so the epoch is
	converted into the system timezone and made naive to be comparable.
	"""
	if isinstance(raw_payload, (str, bytes)):
		try:
			raw_payload = json.loads(raw_payload)
		except ValueError:
			return None
	message = raw_payload.get("message") if isinstance(raw_payload, dict) else None
	if not isinstance(message, dict):
		return None
	try:
		epoch = int(str(message.get("timestamp")).strip())
		utc = datetime.fromtimestamp(epoch, tz=timezone.utc)
	except (TypeError, ValueError, OverflowError, OSError):
		return None
	return frappe.utils.convert_utc_to_system_timezone(utc).replace(tzinfo=None)


def window_state(gateway_name: str, phone: str, now: datetime | None = None) -> dict[str, Any]:
	"""Compute the 24h customer-service window from signed Gateway Events only (A3).

	Only inbound events with ``verified_sender=1`` for the same gateway and
	normalised sender count. WhatsApp Message rows are never consulted.

	The window anchors on the customer's own message time (Meta redelivers old
	webhooks for days, so ``received_at`` alone would reopen a closed window),
	falling back to ``received_at`` when the payload carries no timestamp. The
	message time is capped at ``received_at`` so clock skew cannot extend it.
	"""
	now = now or frappe.utils.now_datetime()
	sender = normalize_phone(phone)
	rows = frappe.get_all(
		"Gateway Event",
		filters={
			"gateway": gateway_name,
			"sender_id": sender,
			"verified_sender": 1,
			"received_at": [">=", now - timedelta(hours=WINDOW_HOURS)],
		},
		fields=["received_at", "verified_sender", "sender_id", "raw_payload"],
		order_by="received_at desc",
		limit_page_length=50,
	)
	last = None
	for row in rows or []:
		if not row.get("verified_sender"):
			continue
		ts = row.get("received_at")
		if not ts:
			continue
		sent = message_time(row.get("raw_payload"))
		if sent is not None:
			ts = min(sent, ts)
		if last is None or ts > last:
			last = ts
	if last is None:
		return {"open": False, "last_inbound_at": None, "expires_at": None}
	expires = last + timedelta(hours=WINDOW_HOURS)
	return {"open": now < expires and last <= now, "last_inbound_at": last, "expires_at": expires}


def record_incoming(account_name: str, message: dict[str, Any], profile_name: str | None = None) -> str | None:
	"""Idempotently mirror a signed inbound message into ``WhatsApp Message``.

	Returns the doc name, or None when unavailable / no message id. Does not
	trigger an outgoing send (type Incoming).
	"""
	if not is_available():
		return None
	message_id = (message or {}).get("id") or (message or {}).get("message_id")
	if not message_id:
		return None
	existing = frappe.db.get_value("WhatsApp Message", {"message_id": message_id}, "name")
	if existing:
		return existing
	content_type = message.get("type") or message.get("content_type") or "text"
	body = ""
	if content_type == "text":
		body = (message.get("text") or {}).get("body", "") if isinstance(message.get("text"), dict) else message.get("text") or ""
	elif isinstance(message.get(content_type), dict):
		body = message[content_type].get("caption", "") or ""
	if not body:
		body = message.get("body") or message.get("message") or ""
	allowed = {"text", "document", "image", "video", "audio", "flow", "reaction", "location", "contact", "button", "interactive", "order"}
	doc = frappe.get_doc(
		{
			"doctype": "WhatsApp Message",
			"type": "Incoming",
			"from": normalize_phone(message.get("from")),
			"message": body,
			"message_id": message_id,
			"content_type": content_type if content_type in allowed else "text",
			"profile_name": profile_name,
			"whatsapp_account": account_name,
		}
	)
	doc.flags.ignore_mandatory = True
	doc.insert(ignore_permissions=True)  # signed ingress mirror, internal
	return doc.name


def _readback(doc: Any) -> dict[str, Any]:
	return {
		"success": True,
		"name": doc.name,
		"message_id": doc.get("message_id"),
		"status": doc.get("status"),
	}


def send_text(
	account_name: str,
	to: str,
	text: str,
	reply_to: str | None = None,
	trusted: bool = False,
) -> dict[str, Any]:
	"""Send a free-form text via ``WhatsApp Message`` (it performs the one send, A1).

	``trusted=True`` is only for the gateway-internal reply path and skips
	permission checks; user-initiated calls must leave it False. Raises
	:class:`BridgeError` (``.payload`` is the error dict) on failure.
	"""
	_account_or_error(account_name)
	values = {
		"doctype": "WhatsApp Message",
		"type": "Outgoing",
		"to": normalize_phone(to),
		"message": text,
		"content_type": "text",
		"whatsapp_account": account_name,
	}
	if reply_to:
		values["is_reply"] = 1
		values["reply_to_message_id"] = reply_to
	try:
		doc = frappe.get_doc(values)
		doc.insert(ignore_permissions=bool(trusted))
		if trusted:
			# Gateway reply path runs in a background job: persist the outgoing row
			# now so Meta's status webhook can match its wamid straight away.
			frappe.db.commit()
	except frappe.PermissionError:
		raise
	except Exception as exc:
		raise BridgeError(_err("send_failed", message=_redact(exc))) from exc
	return _readback(doc)


def _template_docname(account_name: str, template_name: str, language_code: str | None = None) -> str:
	"""Map a Meta template name to the ``WhatsApp Templates`` document name.

	``WhatsApp Message.template`` is a Link to the document, whose name is
	``<template_name>-<language_code>`` and not the bare Meta name. With several
	languages for one template, ``language_code`` picks the matching row.
	"""
	if frappe.db.exists("WhatsApp Templates", template_name):
		return template_name
	for field in ("actual_name", "template_name"):
		filters = {"whatsapp_account": account_name, field: template_name}
		if language_code:
			name = frappe.db.get_value("WhatsApp Templates", {**filters, "language_code": language_code}, "name")
			if name:
				return name
		name = frappe.db.get_value("WhatsApp Templates", filters, "name")
		if name:
			return name
	return template_name


def template_language(account_name: str, template_name: str, preferred: str | None = None) -> str | None:
	"""Language code of the WhatsApp Templates row for ``template_name`` (None if not found)."""
	if not is_available():
		return None
	try:
		if frappe.db.exists("WhatsApp Templates", template_name):
			return frappe.db.get_value("WhatsApp Templates", template_name, "language_code") or None
		for field in ("actual_name", "template_name"):
			filters = {"whatsapp_account": account_name, field: template_name}
			if preferred:
				found = frappe.db.get_value("WhatsApp Templates", {**filters, "language_code": preferred}, "language_code")
				if found:
					return found
			found = frappe.db.get_value("WhatsApp Templates", filters, "language_code")
			if found:
				return found
	except Exception:
		return None
	return None


def _default_post(*args: Any, **kwargs: Any) -> Any:
	import requests

	kwargs.setdefault("timeout", _HTTP_TIMEOUT)
	return requests.post(*args, **kwargs)


def graph_error_message(resp: Any) -> str:
	"""Readable, token-free message from a Meta Graph error response."""
	try:
		err = (resp.json() or {}).get("error") or {}
	except Exception:
		err = {}
	if err:
		details = (err.get("error_data") or {}).get("details")
		parts = [f"({err.get('code')})" if err.get("code") is not None else "", str(err.get("message") or "")]
		if details:
			parts.append(str(details))
		return _redact(" ".join(p for p in parts if p).strip())
	return _redact(getattr(resp, "text", "") or f"HTTP {getattr(resp, 'status_code', '?')}")


def _graph_post_message(acct: dict[str, Any], data: dict[str, Any], http_post: Callable[..., Any] | None = None) -> str:
	"""POST a ``/messages`` body with the account credentials; returns the wamid or raises BridgeError."""
	http_post = http_post or _default_post
	url = f"{acct['url']}/{acct['version']}/{acct['phone_id']}/messages"
	try:
		resp = http_post(
			url,
			json=data,
			headers={"Authorization": f"Bearer {acct['token']}", "Content-Type": "application/json"},
			timeout=_HTTP_TIMEOUT,
		)
	except Exception as exc:
		raise BridgeError(_err("send_failed", message=_redact(exc))) from exc
	status = getattr(resp, "status_code", 200)
	if isinstance(status, int) and status >= 400:
		raise BridgeError(_err("send_failed", message=graph_error_message(resp)))
	try:
		return str(resp.json()["messages"][0]["id"])
	except Exception as exc:
		raise BridgeError(_err("send_failed", message="Unexpected response from WhatsApp.")) from exc


def _template_row(docname: str) -> dict[str, Any] | None:
	"""Header/body/button definition of a WhatsApp Templates document (None if unknown)."""
	if not frappe.db.exists("WhatsApp Templates", docname):
		return None
	row = frappe.db.get_value(
		"WhatsApp Templates",
		docname,
		["template", "header_type", "header", "sample", "actual_name", "template_name", "language_code", "status"],
		as_dict=True,
	)
	row["buttons"] = frappe.get_all(
		"WhatsApp Button",
		filters={"parent": docname, "parenttype": "WhatsApp Templates"},
		fields=["button_type", "button_label", "url_type", "website_url"],
		order_by="idx asc",
	)
	return row


def _mirror_template_send(acct: dict[str, Any], docname: str, to: str, wamid: str, values: dict[str, Any], trusted: bool) -> dict[str, Any]:
	"""Record a Graph-sent template as an Outgoing ``WhatsApp Message`` without sending again.

	``message_id`` is preset, which makes frappe_whatsapp skip its own send.
	"""
	doc_values = {
		"doctype": "WhatsApp Message",
		"type": "Outgoing",
		"to": normalize_phone(to),
		"use_template": 1,
		"template": docname,
		"whatsapp_account": acct["name"],
		"content_type": "text",
		"message_id": wamid,
		"status": "Success",
		**values,
	}
	try:
		doc = frappe.get_doc(doc_values)
		doc.insert(ignore_permissions=bool(trusted))
		if trusted:
			frappe.db.commit()
		return {"success": True, "name": doc.name, "message_id": wamid, "status": doc.get("status")}
	except Exception:
		# The template is already delivered; a failed audit row must not fail the send.
		return {"success": True, "name": None, "message_id": wamid, "status": "Success", "mirrored": False}


def send_template(
	account_name: str,
	to: str,
	template_name: str,
	body_params: list | None = None,
	header_params: list | None = None,
	trusted: bool = False,
	language_code: str | None = None,
	button_params: list | None = None,
	http_post: Callable[..., Any] | None = None,
) -> dict[str, Any]:
	"""Send an approved template.

	Plain body-only sends use an Outgoing ``WhatsApp Message`` (use_template=1),
	which frappe_whatsapp sends. Sends with header or button parameters (text
	header variable, media header link, dynamic URL suffix) need components that
	frappe_whatsapp cannot build without a reference document, so those are
	posted to Graph with the account credentials and mirrored into
	``WhatsApp Message`` without a second send.

	``header_params``: one text string or one ``{"type": image|document|video,
	"link": ..., "filename"?: ...}``. ``button_params``: ``[{"index": 0,
	"sub_type": "url", "text": "<suffix>"}]`` (a bare string is the URL suffix at
	its list position). Permission-checked unless ``trusted=True`` (gateway-internal
	reply path). Raises :class:`BridgeError` (``.payload`` is the error dict).
	"""
	acct = _account_or_error(account_name)
	errors: list[dict[str, str]] = []
	params = [wt.validate_param_text(f"body_params[{i}]", p, errors) for i, p in enumerate(body_params or [])]
	headers_n, header_errors = wt.normalize_header_params(header_params)
	buttons_n, button_errors = wt.normalize_button_params(button_params)
	errors += header_errors + button_errors
	docname = _template_docname(account_name, template_name, language_code)
	tpl = _template_row(docname)
	if tpl:
		errors += _check_send_against_template(tpl, params, headers_n, buttons_n)
	if errors:
		raise BridgeError(wt.validation_error(errors))

	header_type = (tpl or {}).get("header_type") or ""
	if (
		tpl
		and header_type in wt.MEDIA_HEADER_TYPES
		and not headers_n
		and not (header_type == "IMAGE" and wt.is_http_url(str(tpl.get("sample") or "")))
	):
		raise BridgeError(
			wt.validation_error(
				[wt._e("header_params", "required", f"This template has an {header_type} header; provide a media link.")]
			)
		)

	if headers_n or buttons_n or (tpl and _has_dynamic_url(tpl)):
		if not trusted and not frappe.has_permission("WhatsApp Message", "create"):
			raise frappe.PermissionError("Not permitted to send WhatsApp messages")
		for item in headers_n:
			if isinstance(item, dict) and not item.get("type"):
				item["type"] = header_type.lower() if header_type else "image"
		template_payload: dict[str, Any] = {
			"name": (tpl or {}).get("actual_name") or (tpl or {}).get("template_name") or template_name,
			"language": {"code": (tpl or {}).get("language_code") or language_code or "en_US"},
		}
		components = wt.build_template_components(params, headers_n, buttons_n, header_type, always_body=bool(params))
		if components:
			template_payload["components"] = components
		wamid = _graph_post_message(
			acct,
			{
				"messaging_product": "whatsapp",
				"recipient_type": "individual",
				"to": normalize_phone(to),
				"type": "template",
				"template": template_payload,
			},
			http_post,
		)
		mirror: dict[str, Any] = {"template_parameters": ",".join(params)}
		if params:
			mirror["body_param"] = json.dumps({str(i + 1): p for i, p in enumerate(params)})
		media = next((h for h in headers_n if isinstance(h, dict)), None)
		if media:
			mirror["attach"] = media["link"]
		elif headers_n:
			mirror["template_header_parameters"] = str(headers_n[0])
		return _mirror_template_send(acct, docname, to, wamid, mirror, trusted)

	values: dict[str, Any] = {
		"doctype": "WhatsApp Message",
		"type": "Outgoing",
		"to": normalize_phone(to),
		"use_template": 1,
		"template": docname,
		"whatsapp_account": account_name,
		"content_type": "text",
		"template_parameters": ",".join(params),
		# frappe_whatsapp builds the Meta payload from body_param (JSON dict values)
		"body_param": json.dumps({str(i + 1): p for i, p in enumerate(params)}),
	}
	try:
		doc = frappe.get_doc(values)
		doc.insert(ignore_permissions=bool(trusted))
		if trusted:
			# Gateway reply path runs in a background job: persist the outgoing row
			# now so Meta's status webhook can match its wamid straight away.
			frappe.db.commit()
	except frappe.PermissionError:
		raise
	except Exception as exc:
		raise BridgeError(_err("send_failed", message=_redact(exc))) from exc
	return _readback(doc)


def _has_dynamic_url(tpl: dict[str, Any]) -> bool:
	return any(b.get("button_type") == "Visit Website" and b.get("url_type") == "Dynamic" for b in tpl.get("buttons") or [])


def _check_send_against_template(
	tpl: dict[str, Any], params: list[str], headers_n: list[Any], buttons_n: list[dict[str, Any]]
) -> list[dict[str, str]]:
	errors: list[dict[str, str]] = []
	expected = len(wt.placeholders(tpl.get("template") or ""))
	if expected != len(params):
		errors.append(wt._e("body_params", "param_count_mismatch", f"This template takes {expected} body parameter(s); got {len(params)}."))
	header_type = tpl.get("header_type") or ""
	header_vars = len(wt.placeholders(tpl.get("header") or "")) if header_type == "TEXT" else 0
	if header_type == "TEXT":
		if header_vars and not headers_n:
			errors.append(wt._e("header_params", "required", "This template header has a variable; provide one header parameter."))
		elif not header_vars and headers_n:
			errors.append(wt._e("header_params", "not_expected", "This template header takes no parameter."))
		elif headers_n and isinstance(headers_n[0], dict):
			errors.append(wt._e("header_params", "invalid_type", "This template header needs text, not media."))
	elif header_type in wt.MEDIA_HEADER_TYPES:
		if headers_n and not isinstance(headers_n[0], dict):
			errors.append(wt._e("header_params", "invalid_type", f"This template has an {header_type} header; provide a media link."))
		elif headers_n and headers_n[0].get("type") and headers_n[0]["type"] != header_type.lower():
			errors.append(wt._e("header_params", "invalid_type", f"This template has an {header_type} header."))
	elif headers_n:
		errors.append(wt._e("header_params", "not_expected", "This template has no header."))
	buttons = tpl.get("buttons") or []
	dynamic = {i for i, b in enumerate(buttons) if b.get("button_type") == "Visit Website" and b.get("url_type") == "Dynamic"}
	given = set()
	for bp in buttons_n:
		idx = bp["index"]
		if bp["sub_type"] == "url":
			if idx not in dynamic:
				errors.append(wt._e("button_params", "unexpected", f"Button {idx} is not a dynamic URL button."))
			given.add(idx)
		elif idx >= len(buttons) or buttons[idx].get("button_type") != "Quick Reply":
			errors.append(wt._e("button_params", "unexpected", f"Button {idx} is not a quick-reply button."))
	for idx in sorted(dynamic - given):
		errors.append(wt._e("button_params", "required", f"Button {idx} is a dynamic URL button and needs its URL suffix."))
	return errors


def send_media(
	account_name: str,
	to: str,
	kind: str,
	link: str | None = None,
	media_id: str | None = None,
	caption: str | None = None,
	filename: str | None = None,
	reply_to: str | None = None,
	trusted: bool = False,
) -> dict[str, Any]:
	"""Send an image/document/audio/video as an Outgoing ``WhatsApp Message`` (it performs the send).

	frappe_whatsapp's ``send_outgoing`` takes the media from ``attach`` (a public
	link) and the caption from ``message``. It cannot send an uploaded media id or
	a document filename, so those are rejected / ignored here. The caller is
	responsible for the 24h window check; free-form media is subject to it.
	"""
	_account_or_error(account_name)
	media, errors = wt.validate_media_request(kind, link, media_id, caption, filename)
	if not errors and media["media_id"]:
		errors.append(wt._e("media_id", "unsupported_in_bridge", "Bridge mode sends media by link; media_id needs direct mode."))
	if errors:
		raise BridgeError(wt.validation_error(errors))
	values = {
		"doctype": "WhatsApp Message",
		"type": "Outgoing",
		"to": normalize_phone(to),
		"message": media["caption"],
		"content_type": media["kind"],
		"attach": media["link"],
		"whatsapp_account": account_name,
	}
	if reply_to:
		values["is_reply"] = 1
		values["reply_to_message_id"] = reply_to
	try:
		doc = frappe.get_doc(values)
		doc.insert(ignore_permissions=bool(trusted))
		if trusted:
			frappe.db.commit()
	except frappe.PermissionError:
		raise
	except Exception as exc:
		raise BridgeError(_err("send_failed", message=_redact(exc))) from exc
	return _readback(doc)


def list_templates(account_name: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
	"""List WhatsApp Templates (empty list when the bridge is unavailable)."""
	if not is_available():
		return []
	filters: dict[str, Any] = {}
	if account_name:
		filters["whatsapp_account"] = account_name
	if status:
		filters["status"] = status
	return frappe.get_list(
		"WhatsApp Templates",
		filters=filters,
		fields=[
			"name", "template_name", "actual_name", "category", "language_code", "status", "id",
			"whatsapp_account", "template", "header_type", "header", "footer", "sample_values",
		],
		order_by="modified desc",
		limit_page_length=500,
	)


def _check_sample_reference(sample: str) -> dict[str, str] | None:
	"""Media header sample must be fetchable by frappe_whatsapp: public http(s) URL or an existing File."""
	if wt.is_http_url(sample):
		parsed = urlparse(sample)
		if getattr(frappe.flags, "in_test", False):
			return None
		if parsed.scheme != "https" or _is_private_host(parsed.hostname or ""):
			return wt._e("sample", "invalid_sample", "Sample URL must be a public https URL.")
		return None
	if not frappe.db.exists("File", {"file_url": sample}):
		return wt._e("sample", "sample_not_found", "No uploaded file matches this sample path.")
	return None


def _is_private_host(host: str) -> bool:
	import ipaddress
	import socket

	host = host.strip("[]").lower()
	if host in ("localhost", "") or host.endswith(".localhost") or host.endswith(".local"):
		return True
	try:
		addrs = {ai[4][0] for ai in socket.getaddrinfo(host, None)}
	except OSError:
		return False  # unresolved here; the fetch will fail and be reported by frappe_whatsapp
	for addr in addrs:
		ip = ipaddress.ip_address(addr)
		if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
			return True
	return False


def _language_link(language_code: str, given: Any) -> str | None:
	"""``WhatsApp Templates.language`` (Link to Language) for a Meta language code."""
	if given:
		return str(given)
	for candidate in (language_code.replace("_", "-"), language_code, language_code.split("_")[0]):
		if frappe.db.exists("Language", candidate):
			return candidate
	return None


_TEMPLATE_PASSTHROUGH = ("field_names", "for_doctype")


def template_buttons(names: list[str]) -> dict[str, list[dict[str, Any]]]:
	"""Button rows (``WhatsApp Button``) of the given templates, keyed by template name."""
	if not names or not is_available():
		return {}
	rows = frappe.get_all(
		"WhatsApp Button",
		filters={"parent": ["in", list(names)], "parenttype": "WhatsApp Templates"},
		fields=["parent", "button_type", "button_label", "url_type", "website_url", "phone_number"],
		order_by="idx asc",
	)
	out: dict[str, list[dict[str, Any]]] = {}
	for row in rows:
		out.setdefault(row.pop("parent"), []).append(row)
	return out


def create_template(account_name: str, payload: dict[str, Any]) -> dict[str, Any]:
	"""Create a WhatsApp Templates row (its after_insert pushes to Meta). Permission-checked (A8).

	Supports an optional header (TEXT with at most one variable, or IMAGE/DOCUMENT
	with a sample file), footer and up to 3 quick-reply / 2 call-to-action
	buttons, validated against Meta limits first. Field mapping follows the
	``WhatsApp Templates`` / ``WhatsApp Button`` doctypes: ``header_type``,
	``header``, ``sample``, ``footer``, ``sample_values`` and ``buttons`` child
	rows. Validation failures return ``{"error": "validation_failed", "errors": [...]}``.
	"""
	if not is_available():
		return _err("bridge_unavailable")
	if not frappe.has_permission("WhatsApp Templates", "create"):
		raise frappe.PermissionError("Not permitted to create WhatsApp Templates")
	name = str(payload.get("template_name") or payload.get("name") or "")
	category = str(payload.get("category") or "").upper()
	language_code = str(payload.get("language_code") or "")
	if not _TEMPLATE_NAME_RE.match(name):
		return _err("invalid_name", message="Name must match ^[a-z0-9_]+$")
	if category not in TEMPLATE_CATEGORIES:
		return _err("invalid_category", message="Category must be one of " + ", ".join(TEMPLATE_CATEGORIES))
	if not language_code:
		return _err("invalid_language", message="language_code is required")
	if len(name) > 512:
		return wt.validation_error([wt._e("template_name", "too_long", "Template name is longer than 512 characters.")])
	spec, errors = wt.validate_template_spec(payload)
	if spec["header_type"] in wt.MEDIA_HEADER_TYPES and spec["header_sample"] and not errors:
		problem = _check_sample_reference(spec["header_sample"])
		if problem:
			errors.append(problem)
	language = _language_link(language_code, payload.get("language"))
	if not language:
		errors.append(wt._e("language_code", "unknown_language", f"Language {language_code} is not installed on this site."))
	if errors:
		return wt.validation_error(errors)
	values: dict[str, Any] = {k: payload[k] for k in _TEMPLATE_PASSTHROUGH if payload.get(k)}
	values.update(
		{
			"doctype": "WhatsApp Templates",
			"template_name": name,
			"category": category,
			"language": language,
			"language_code": language_code,
			"whatsapp_account": account_name,
			"template": spec["body"],
			"sample_values": ",".join(spec["sample_values"]),
		}
	)
	if spec["header_type"]:
		values["header_type"] = spec["header_type"]
		if spec["header_type"] == "TEXT":
			values["header"] = spec["header_text"]
		if spec["header_sample"]:
			values["sample"] = spec["header_sample"]
	if spec["footer"]:
		values["footer"] = spec["footer"]
	rows = wt.button_rows(spec)
	if rows:
		values["buttons"] = rows
	doc = None
	try:
		doc = frappe.get_doc(values)
		doc.insert()
	except frappe.PermissionError:
		raise
	except Exception as exc:
		# frappe_whatsapp pushes to Meta in after_insert, so a Meta rejection leaves a
		# local row that never existed on Meta. Drop it (no hooks) so it cannot linger as PENDING.
		orphan = getattr(doc, "name", None)
		if orphan and not doc.get("id") and frappe.db.exists("WhatsApp Templates", orphan):
			frappe.db.delete("WhatsApp Templates", {"name": orphan})
		return _err("create_failed", message=_redact(exc))
	return {"success": True, "name": doc.name, "status": doc.get("status"), "id": doc.get("id")}


def delete_template(name: str) -> dict[str, Any]:
	"""Delete a template (frappe_whatsapp on_trash removes it on Meta). Permission-checked."""
	if not is_available():
		return _err("bridge_unavailable")
	if not frappe.has_permission("WhatsApp Templates", "delete"):
		raise frappe.PermissionError("Not permitted to delete WhatsApp Templates")
	if not frappe.db.exists("WhatsApp Templates", name):
		return _err("not_found")
	# frappe_whatsapp's on_trash deletes the template on Meta *before* Frappe's link check
	# rejects the local delete, which would leave Meta and Huf out of sync. Check first.
	if frappe.db.exists("WhatsApp Message", {"template": name}):
		return _err(
			"template_in_use",
			message="This template has been used in sent messages and cannot be deleted.",
		)
	try:
		frappe.delete_doc("WhatsApp Templates", name)
	except frappe.PermissionError:
		raise
	except Exception as exc:
		return _err("delete_failed", message=_redact(exc))
	return {"success": True, "name": name}


_META_BUTTON_TYPES = {
	"URL": "Visit Website",
	"PHONE_NUMBER": "Call Phone",
	"QUICK_REPLY": "Quick Reply",
	"FLOW": "Flow",
	"MPM": "Multi-Product Message",
	"CATALOG": "Catalog",
}


def _local_template(account_name: str, name: str, language: str) -> str | None:
	"""Docname of the local row for a Meta template (name + language), or None."""
	langs = [language, language.replace("-", "_")] if language else [None]
	for field in ("actual_name", "template_name"):
		for lang in langs:
			filters: dict[str, Any] = {"whatsapp_account": account_name, field: name}
			if lang:
				filters["language_code"] = lang
			found = frappe.db.get_value("WhatsApp Templates", filters, "name")
			if found:
				return found
		if language:  # legacy rows without a language code still match by name
			found = frappe.db.get_value(
				"WhatsApp Templates",
				{"whatsapp_account": account_name, field: name, "language_code": ["in", ["", None]]},
				"name",
			)
			if found:
				return found
	return None


def _import_template(account_name: str, tpl: dict[str, Any]) -> str:
	"""Insert a ``WhatsApp Templates`` row for a template that already exists on Meta.

	Mirrors frappe_whatsapp's own ``fetch()``: the row is written with ``db_insert`` so the
	``after_insert`` hook (which would POST the template to Meta) never runs.
	"""
	name, language = str(tpl.get("name")), str(tpl.get("language") or "")
	doc = frappe.new_doc("WhatsApp Templates")
	doc.template_name = name
	doc.actual_name = name
	doc.language_code = language
	doc.language = _language_link(language, None) if language else None
	doc.status = tpl.get("status")
	doc.category = tpl.get("category")
	doc.id = tpl.get("id")
	doc.whatsapp_account = account_name
	doc.name = f"{name}-{language}"
	for comp in tpl.get("components") or []:
		ctype = str(comp.get("type") or "").upper()
		if ctype == "HEADER":
			fmt = str(comp.get("format") or "").upper()
			if fmt in ("TEXT", "IMAGE", "DOCUMENT"):
				doc.header_type = fmt
			if fmt == "TEXT":
				doc.header = comp.get("text")
		elif ctype == "FOOTER":
			doc.footer = comp.get("text")
		elif ctype == "BODY":
			doc.template = comp.get("text")
			body_text = (comp.get("example") or {}).get("body_text")
			if body_text and body_text[0]:
				doc.sample_values = ",".join(str(v) for v in body_text[0])
		elif ctype == "BUTTONS":
			for i, btn in enumerate(comp.get("buttons") or [], start=1):
				btype = _META_BUTTON_TYPES.get(btn.get("type"))
				if not btype:
					continue
				row: dict[str, Any] = {"button_type": btype, "button_label": btn.get("text"), "sequence": i}
				if btn.get("type") == "URL":
					row["website_url"] = btn.get("url")
					row["url_type"] = "Dynamic" if "{{" in str(btn.get("url") or "") else "Static"
					if btn.get("example"):
						row["example_url"] = ",".join(btn["example"])
				elif btn.get("type") == "PHONE_NUMBER":
					row["phone_number"] = btn.get("phone_number")
				doc.append("buttons", row)
	now = frappe.utils.now()
	user = frappe.session.user
	doc.owner = doc.modified_by = user
	doc.creation = doc.modified = now
	doc.docstatus = 0
	doc.db_insert()
	for child in doc.get("buttons"):
		child.parent, child.parenttype, child.parentfield = doc.name, doc.doctype, "buttons"
		child.owner = child.modified_by = user
		child.creation = child.modified = now
		child.db_insert()
	return doc.name


def sync_templates(account_name: str, http_get: Callable[..., Any] | None = None) -> dict[str, Any]:
	"""Account-scoped template sync (A15); does not call frappe_whatsapp ``fetch()``.

	Templates that exist on Meta but not locally are IMPORTED (inserted without the
	frappe_whatsapp ``after_insert`` push, so nothing is POSTed to Meta); existing rows only
	get status/category/id refreshed. Returns ``imported``/``updated``/``unchanged`` counts (``skipped``: no language, or the docname belongs to another account).
	"""
	if not is_available():
		return _err("bridge_unavailable")
	if not frappe.has_permission("WhatsApp Templates", "write"):
		raise frappe.PermissionError("Not permitted to sync WhatsApp Templates")
	acct = resolve_account(account_name)
	if not acct or acct.get("error"):
		return _err("no_account")
	http_get = http_get or _default_get
	url = f"{acct['url']}/{acct['version']}/{acct['business_id']}/message_templates"
	headers = {"Authorization": f"Bearer {acct['token']}"}
	imported = updated = unchanged = skipped = seen = 0
	params: dict[str, Any] | None = {"limit": 100}
	try:
		while url:
			resp = http_get(url, headers=headers, params=params, timeout=_HTTP_TIMEOUT)
			resp.raise_for_status()
			data = resp.json()
			for tpl in data.get("data", []):
				seen += 1
				if not tpl.get("name"):
					continue
				name = _local_template(account_name, tpl["name"], str(tpl.get("language") or ""))
				if not name:
					language = str(tpl.get("language") or "")
					# docname is <name>-<language> and unique site-wide: skip what cannot be named or collides
					if not language or frappe.db.exists("WhatsApp Templates", f"{tpl['name']}-{language}"):
						skipped += 1
						continue
					_import_template(account_name, tpl)
					imported += 1
					continue
				current = frappe.db.get_value("WhatsApp Templates", name, ["status", "category", "id"], as_dict=True) or {}
				changes = {
					k: tpl.get(k)
					for k in ("status", "category", "id")
					if tpl.get(k) is not None and tpl.get(k) != current.get(k)
				}
				if changes:
					frappe.db.set_value("WhatsApp Templates", name, changes)
					updated += 1
				else:
					unchanged += 1
			next_url = (data.get("paging") or {}).get("next")
			if next_url:
				nxt, base = urlparse(str(next_url)), urlparse(acct["url"])
				if nxt.scheme != "https" or (nxt.hostname or "").lower() != (base.hostname or "").lower():
					return _err("sync_failed", message="Unexpected paging URL host")
			url = next_url
			params = None
	except Exception as exc:
		return _err("sync_failed", message=_redact(exc))
	return {"success": True, "seen": seen, "imported": imported, "updated": updated, "unchanged": unchanged, "skipped": skipped}


def forward_status(change: dict[str, Any], _retry: bool = False) -> bool:
	"""Forward a status ``change`` to frappe_whatsapp's ``update_status`` (A4).

	Lazy import; never raises. Returns True only if a forward actually happened.
	A message status whose wamid is not (yet) known is not dropped: one delayed
	retry is enqueued (the outgoing row may still be uncommitted when Meta's
	webhook arrives). ``_retry`` marks that retry so it never loops.
	"""
	try:
		if not is_available():
			return False
		if change.get("field") == "messages":
			statuses = (change.get("value") or {}).get("statuses") or []
			if not statuses or not frappe.db.exists("WhatsApp Message", {"message_id": statuses[0].get("id")}):
				if statuses and not _retry:
					_enqueue_forward_retry(change)
				return False
		webhook = importlib.import_module("frappe_whatsapp.utils.webhook")
		webhook.update_status(change)
		return True
	except Exception:
		return False


def _enqueue_forward_retry(change: dict[str, Any]) -> None:
	try:
		frappe.enqueue(
			"huf.ai.gateway_adapters.whatsapp_bridge.retry_forward_status",
			queue="short",
			enqueue_after_commit=True,
			change=change,
		)
	except Exception:
		pass  # best effort


def retry_forward_status(change: dict[str, Any]) -> bool:
	"""Background job: wait briefly for the outgoing row to be committed, then forward once."""
	import time

	time.sleep(FORWARD_RETRY_DELAY_SECONDS)
	return forward_status(change, _retry=True)


def media_limit_for_mime(mime: str) -> int:
	"""Meta's inbound size limit for the media kind a MIME type belongs to."""
	mime = (mime or "").lower()
	if mime.startswith("image/"):
		return MEDIA_LIMITS_BY_KIND["image"]
	if mime.startswith("audio/"):
		return MEDIA_LIMITS_BY_KIND["audio"]
	if mime.startswith("video/"):
		return MEDIA_LIMITS_BY_KIND["video"]
	return MEDIA_LIMITS_BY_KIND["document"]


def download_media(
	account_name: str,
	media_id: str,
	max_bytes: int | None = None,
	allowed_mime_prefixes: tuple[str, ...] = DEFAULT_MIME_PREFIXES,
	http_get: Callable[..., Any] | None = None,
	credentials: dict[str, Any] | None = None,
	kind: str | None = None,
) -> dict[str, Any]:
	"""Fetch media by id at call time. Raises ValueError on caps/mime/failure.

	The size cap is ``max_bytes`` when given, else Meta's per-kind limit (``kind``
	when known, e.g. sticker, otherwise derived from the MIME type); declared and
	streamed sizes are both enforced.

	``credentials`` (``token``, optional ``url``/``version``) serves direct-mode
	gateways that have no WhatsApp Account; otherwise the account is resolved.
	"""
	if credentials and credentials.get("token"):
		acct = {
			"token": credentials["token"],
			"url": str(credentials.get("url") or GRAPH_HOST).rstrip("/"),
			"version": credentials.get("version") or GRAPH_DEFAULT_VERSION,
		}
	else:
		acct = resolve_account(account_name)
	if not acct or acct.get("error"):
		raise ValueError("no_account")
	http_get = http_get or _default_get
	headers = {"Authorization": f"Bearer {acct['token']}"}
	meta_resp = http_get(
		f"{acct['url']}/{acct['version']}/{quote(str(media_id), safe='')}",
		headers=headers,
		timeout=_HTTP_TIMEOUT,
		allow_redirects=False,
	)
	meta_resp.raise_for_status()
	meta = meta_resp.json()
	mime = str(meta.get("mime_type") or "").split(";")[0].strip().lower()
	if (
		not mime
		or mime.startswith(_BLOCKED_MIME_PREFIXES)
		or not any(mime.startswith(p) for p in allowed_mime_prefixes)
	):
		raise ValueError(f"mime_not_allowed: {mime or 'unknown'}")
	if max_bytes is None:
		max_bytes = MEDIA_LIMITS_BY_KIND.get(str(kind or "").lower()) or media_limit_for_mime(mime)
	declared = meta.get("file_size")
	if declared is not None and int(declared) > max_bytes:
		raise ValueError("media_too_large")
	media_url = meta.get("url")
	if not media_url:
		raise ValueError("media_url_missing")
	if not _is_media_host(str(media_url)):
		raise ValueError("media_url_host_not_allowed")
	resp = http_get(media_url, headers=headers, timeout=_HTTP_TIMEOUT, allow_redirects=False, stream=True)
	try:
		resp.raise_for_status()
		if hasattr(resp, "iter_content"):
			buf = bytearray()
			for chunk in resp.iter_content(chunk_size=64 * 1024):
				buf.extend(chunk)
				if len(buf) > max_bytes:
					raise ValueError("media_too_large")
			content = bytes(buf)
		else:
			content = resp.content
			if len(content) > max_bytes:
				raise ValueError("media_too_large")
	finally:
		close = getattr(resp, "close", None)
		if callable(close):
			close()
	filename = re.sub(r"[^A-Za-z0-9._-]", "_", str(meta.get("filename") or media_id))[:100] or "media"
	return {"content": content, "mime_type": mime, "filename": filename}


def list_messages(account_name: str | None = None, phone: str | None = None, limit: int = 50) -> dict[str, Any]:
	"""List WhatsApp Messages (newest first); bridge_unavailable error if absent (A15)."""
	if not is_available():
		return _err("bridge_unavailable")
	filters: dict[str, Any] = {}
	if account_name:
		filters["whatsapp_account"] = account_name
	if phone:
		p = normalize_phone(phone)
		or_filters = {"to": p, "from": p}
		rows = frappe.get_list(
			"WhatsApp Message",
			filters=filters,
			or_filters=or_filters,
			fields=_MSG_FIELDS,
			order_by="creation desc",
			limit_page_length=limit,
		)
		return {"success": True, "messages": rows}
	rows = frappe.get_list(
		"WhatsApp Message", filters=filters, fields=_MSG_FIELDS, order_by="creation desc", limit_page_length=limit
	)
	return {"success": True, "messages": rows}


_MSG_FIELDS = ["name", "type", "to", "from", "message", "message_id", "content_type", "status", "creation", "whatsapp_account", "profile_name"]


def list_conversations(account_name: str | None = None, limit: int = 50) -> dict[str, Any]:
	"""Group recent messages by counterparty number (latest message each)."""
	if not is_available():
		return _err("bridge_unavailable")
	filters = {"whatsapp_account": account_name} if account_name else {}
	rows = frappe.get_list(
		"WhatsApp Message",
		filters=filters,
		fields=_MSG_FIELDS,
		order_by="creation desc",
		limit_page_length=max(limit * 20, 200),
	)
	convs: dict[str, dict[str, Any]] = {}
	for row in rows:
		other = normalize_phone(row.get("from") if row.get("type") == "Incoming" else row.get("to"))
		if not other:
			continue
		conv = convs.setdefault(other, {"phone": other, "last_message": row, "count": 0, "profile_name": None})
		conv["count"] += 1
		if row.get("profile_name") and not conv["profile_name"]:
			conv["profile_name"] = row["profile_name"]
	return {"success": True, "conversations": list(convs.values())[:limit]}
