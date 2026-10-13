# Copyright (c) 2026, Huf and contributors
# For license information, please see license.txt

"""Whitelisted WhatsApp management API for the Huf console.

Every function delegates to :mod:`huf.ai.gateway_adapters.whatsapp_bridge`,
the only Huf module that touches frappe_whatsapp doctypes. Expected failures
are returned as ``{"success": False, "error": "<code>", ...}``; bridge errors
raised as :class:`BridgeError` are converted the same way. Permission failures
raise ``frappe.PermissionError`` and are never converted into a success dict.

No function here uses ``ignore_permissions`` or ``set_user``. Mutating calls
are POST-only.
"""

from __future__ import annotations

import functools
import json
import re
from typing import Any
from urllib.parse import quote

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from huf.ai.gateway_adapters import whatsapp_bridge as bridge
from huf.ai.gateway_adapters.whatsapp_bridge import BridgeError

ADMIN_ROLES = ("System Manager", "Huf Admin")
WHATSAPP_PROVIDER = "WhatsApp"
CALLBACK_WARNING = (
	"Meta allows one callback URL per app. Use the webhook URL shown above as the callback, "
	"not the WhatsApp Business app connector's own webhook route. Using the connector's route "
	"bypasses Huf's signature verification."
)
_TEMPLATE_NAME_RE = re.compile(r"^[a-z0-9_]+$")
_MIN_PHONE_DIGITS = 7
_MAX_PHONE_DIGITS = 15
_MAX_LIMIT = 200


def _bridge_guard(fn):
	"""Convert BridgeError into the standard error dict."""

	@functools.wraps(fn)
	def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
		try:
			return fn(*args, **kwargs)
		except BridgeError as exc:
			return dict(exc.payload, success=False)

	return wrapper


def _has_doctype_permission(doctype: str, ptype: str) -> None:
	if not frappe.has_permission(doctype, ptype):
		raise frappe.PermissionError(_("Not permitted to {0} {1}").format(ptype, doctype))


def _require_admin() -> None:
	roles = set(frappe.get_roles() or [])
	if not roles.intersection(ADMIN_ROLES):
		raise frappe.PermissionError(_("System Manager or Huf Admin role required"))


def _clamp_limit(limit: Any) -> int:
	try:
		value = int(limit)
	except (TypeError, ValueError):
		value = 50
	return max(1, min(value, _MAX_LIMIT))


def _parse_json_arg(value: Any, label: str) -> Any:
	if isinstance(value, str):
		value = value.strip()
		if not value:
			return None
		try:
			return json.loads(value)
		except ValueError as exc:
			raise ValueError(f"{label} must be valid JSON") from exc
	return value


def _validate_recipient(to: Any) -> str | dict[str, Any]:
	"""Return the normalised digits-only number, or an error dict."""
	if not to or not str(to).strip():
		return {"success": False, "error": "invalid_recipient", "message": "Recipient number is required."}
	phone = bridge.normalize_phone(to)
	if not (_MIN_PHONE_DIGITS <= len(phone) <= _MAX_PHONE_DIGITS):
		return {
			"success": False,
			"error": "invalid_recipient",
			"message": "Recipient must be a phone number with 7 to 15 digits.",
		}
	return phone


def _validate_template_name(template_name: Any) -> dict[str, Any] | None:
	if not template_name or not _TEMPLATE_NAME_RE.match(str(template_name)):
		return {
			"success": False,
			"error": "invalid_name",
			"message": "Template name must match ^[a-z0-9_]+$",
		}
	return None


def _resolve_account_name(account: str | None) -> str | dict[str, Any]:
	"""Return the resolved WhatsApp Account name, or an error dict."""
	if not bridge.is_available():
		return {"success": False, "error": "bridge_unavailable"}
	acct = bridge.resolve_account(account)
	if not acct or acct.get("error"):
		return {"success": False, "error": "no_account"}
	return acct["name"]


def _gateway_whatsapp_account(gateway_name: str) -> str | None:
	try:
		return frappe.db.get_value("Gateway", gateway_name, "whatsapp_account")
	except Exception:
		return None


def _credential_presence(integration_settings: str | None) -> dict[str, bool]:
	"""Report which credential keys are set, never their values."""
	presence = {"app_secret": False, "webhook_verify_token": False}
	if not integration_settings:
		return presence
	settings = frappe.get_doc("Integration Settings", integration_settings)
	for row in settings.credentials or []:
		if row.key in presence:
			presence[row.key] = bool(row.get_password("value") or "")
	return presence


def _webhook_url(gateway_name: str) -> str:
	return (
		f"{frappe.utils.get_url()}/api/method/huf.ai.gateway_webhook.handle_gateway_webhook"
		f"?gateway_name={quote(gateway_name, safe='')}"
	)


def _gateway_status(gateway: dict[str, Any]) -> dict[str, Any]:
	presence = _credential_presence(gateway.get("integration_settings"))
	failing: list[str] = []
	if not presence["app_secret"]:
		failing.append("app_secret_missing")
	if not presence["webhook_verify_token"]:
		failing.append("webhook_verify_token_missing")
	return {
		"name": gateway["name"],
		"enabled": bool(gateway.get("is_enabled")),
		"whatsapp_account": gateway.get("whatsapp_account"),
		"app_secret_present": presence["app_secret"],
		"app_secret_missing": not presence["app_secret"],
		"verify_token_present": presence["webhook_verify_token"],
		"webhook_url": _webhook_url(gateway["name"]),
		"callback_warning": CALLBACK_WARNING,
		"failing_checks": failing,
	}


@frappe.whitelist()
@_bridge_guard
def get_setup_status(gateway_name: str | None = None) -> dict[str, Any]:
	"""Setup checks for the WhatsApp gateway: bridge, accounts, and per-gateway credentials."""
	_require_admin()
	available = bridge.is_available()
	accounts: list[dict[str, Any]] = []
	if available:
		accounts = frappe.get_all(
			"WhatsApp Account", fields=["name", "phone_id", "status"], limit_page_length=100
		)

	filters: dict[str, Any] = {"provider": WHATSAPP_PROVIDER}
	if gateway_name:
		filters["name"] = gateway_name
	gateways_raw = frappe.get_all(
		"Gateway",
		filters=filters,
		fields=["name", "is_enabled", "integration_settings"],
		limit_page_length=100,
	)
	gateways = []
	for row in gateways_raw or []:
		row = dict(row)
		row["whatsapp_account"] = _gateway_whatsapp_account(row["name"])
		gateways.append(_gateway_status(row))

	return {
		"success": True,
		"bridge_available": available,
		"accounts": accounts,
		"gateways": gateways,
		"callback_warning": CALLBACK_WARNING,
	}


@frappe.whitelist()
@_bridge_guard
def list_templates(account: str | None = None, status: str | None = None) -> dict[str, Any]:
	"""List WhatsApp Templates, optionally filtered by account or status."""
	if not bridge.is_available():
		return {"success": False, "error": "bridge_unavailable"}
	_has_doctype_permission("WhatsApp Templates", "read")
	templates = bridge.list_templates(account, status)
	return {"success": True, "templates": templates}


@frappe.whitelist(methods=["POST"])
@_bridge_guard
def create_template(account: str | None = None, payload: Any = None) -> dict[str, Any]:
	"""Create a WhatsApp Template; its doctype hook pushes it to Meta."""
	if not bridge.is_available():
		return {"success": False, "error": "bridge_unavailable"}
	parsed = _parse_json_arg(payload, "payload")
	if not isinstance(parsed, dict):
		return {"success": False, "error": "invalid_payload", "message": "payload must be an object"}
	name_error = _validate_template_name(parsed.get("template_name") or parsed.get("name"))
	if name_error:
		return name_error
	account_name = _resolve_account_name(account)
	if isinstance(account_name, dict):
		return account_name
	return bridge.create_template(account_name, parsed)


@frappe.whitelist(methods=["POST"])
@_bridge_guard
def delete_template(name: str | None = None) -> dict[str, Any]:
	"""Delete a WhatsApp Template by document name."""
	if not bridge.is_available():
		return {"success": False, "error": "bridge_unavailable"}
	if not name or not str(name).strip():
		return {"success": False, "error": "invalid_name", "message": "Template name is required."}
	return bridge.delete_template(name)


@frappe.whitelist(methods=["POST"])
@_bridge_guard
def sync_templates(account: str | None = None) -> dict[str, Any]:
	"""Sync template statuses for one WhatsApp Account from Meta."""
	if not bridge.is_available():
		return {"success": False, "error": "bridge_unavailable"}
	account_name = _resolve_account_name(account)
	if isinstance(account_name, dict):
		return account_name
	return bridge.sync_templates(account_name)


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=60, seconds=60)
@_bridge_guard
def send_template_message(
	to: str | None = None,
	template_name: str | None = None,
	body_params: Any = None,
	header_params: Any = None,
	account: str | None = None,
) -> dict[str, Any]:
	"""Send an approved template to a number. Allowed outside the 24h window."""
	if not bridge.is_available():
		return {"success": False, "error": "bridge_unavailable"}
	phone = _validate_recipient(to)
	if isinstance(phone, dict):
		return phone
	name_error = _validate_template_name(template_name)
	if name_error:
		return name_error
	try:
		body = _parse_json_arg(body_params, "body_params") or []
		header = _parse_json_arg(header_params, "header_params") or []
	except ValueError as exc:
		return {"success": False, "error": "invalid_params", "message": str(exc)}
	if not isinstance(body, list) or not isinstance(header, list):
		return {"success": False, "error": "invalid_params", "message": "Params must be lists"}
	account_name = _resolve_account_name(account)
	if isinstance(account_name, dict):
		return account_name
	return bridge.send_template(account_name, phone, template_name, body, header)


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=60, seconds=60)
@_bridge_guard
def send_text_message(gateway_name: str | None = None, to: str | None = None, text: str | None = None) -> dict[str, Any]:
	"""Send a free-form text, only while the 24h customer-service window is open."""
	if not bridge.is_available():
		return {"success": False, "error": "bridge_unavailable"}
	if not gateway_name:
		return {"success": False, "error": "invalid_gateway", "message": "gateway_name is required."}
	if not frappe.db.exists("Gateway", gateway_name):
		return {"success": False, "error": "not_found", "message": "Gateway not found."}
	_has_doctype_permission("Gateway", "read")
	phone = _validate_recipient(to)
	if isinstance(phone, dict):
		return phone
	if not text or not str(text).strip():
		return {"success": False, "error": "invalid_text", "message": "Message text is required."}

	state = bridge.window_state(gateway_name, phone)
	if not state.get("open"):
		return {
			"success": False,
			"error": "window_closed",
			"message": "The 24-hour customer-service window is closed for this number.",
			"hint": "Send an approved template with send_template_message instead.",
			"expires_at": state.get("expires_at"),
		}

	account_name = _resolve_account_name(_gateway_whatsapp_account(gateway_name))
	if isinstance(account_name, dict):
		return account_name
	return bridge.send_text(account_name, phone, str(text))


@frappe.whitelist()
@_bridge_guard
def list_messages(account: str | None = None, phone: str | None = None, limit: int = 50) -> dict[str, Any]:
	"""List recent WhatsApp Messages, newest first."""
	if not bridge.is_available():
		return {"success": False, "error": "bridge_unavailable"}
	_has_doctype_permission("WhatsApp Message", "read")
	return bridge.list_messages(account, phone, _clamp_limit(limit))


@frappe.whitelist()
@_bridge_guard
def list_conversations(account: str | None = None, limit: int = 50) -> dict[str, Any]:
	"""Group recent WhatsApp Messages by counterparty number."""
	if not bridge.is_available():
		return {"success": False, "error": "bridge_unavailable"}
	_has_doctype_permission("WhatsApp Message", "read")
	return bridge.list_conversations(account, _clamp_limit(limit))
