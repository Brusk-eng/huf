# Copyright (c) 2026, Huf and contributors
# For license information, please see license.txt

"""WhatsApp integration tools for HUF agents.

Single consolidated tool that performs WhatsApp Cloud API actions via an
`action` parameter.

Actions: send_message, send_template, list_messages, get_account_info,
list_templates, create_template.

Privilege gating (A8)
---------------------
Two flags gate privileged behaviour. They are read server-side from the
active WhatsApp Integration Settings ``credentials`` rows and are off by
default. The LLM controls tool-call kwargs, so any ``allow_proactive`` or
``allow_whatsapp_management`` key supplied in a call is popped and discarded
in ``handle_action``. A setting counts only when its value is truthy: ``1``,
``"1"``, ``"true"`` or ``"yes"`` (case-insensitive). ``"false"``,
``"0"``, ``""`` and missing rows do not count. Any error while reading the
settings fails closed (flag off).

* ``allow_proactive``: permits free-form ``send_message`` to a number with no
  open 24h customer-service window (see below).
* ``allow_whatsapp_management``: permits ``create_template``, and also
  permits proactive free-form sends.

A free-form ``send_message`` is proactive unless ``gateway_name`` is given
and ``bridge.window_state(gateway_name, to)`` reports an open window. With
no gateway context, every free-form send is treated as proactive.
``send_template`` (approved templates) is always allowed. The tool never
uses ``ignore_permissions`` and never switches the session user.
"""

import json
from typing import Optional, Any

import frappe
import requests

from huf.ai.gateway_adapters import whatsapp_bridge as bridge

META_GRAPH_URL = f"https://graph.facebook.com/{bridge.GRAPH_DEFAULT_VERSION}"

_TRUTHY_STRINGS = {"1", "true", "yes"}


def _error(msg: str) -> str:
	return json.dumps({"success": False, "error": msg}, default=str)


def _success(data: Any) -> str:
	return json.dumps({"success": True, "data": data}, default=str)


def _refused(code: str, message: str) -> str:
	return json.dumps({"success": False, "error": code, "message": message}, default=str)


_PRIVILEGE_FLAGS = ("allow_proactive", "allow_whatsapp_management")


def _is_truthy(value: Any) -> bool:
	return str(value or "").strip().lower() in _TRUTHY_STRINGS


def _server_flag(name: str) -> bool:
	"""Read a privilege flag from the active WhatsApp Integration Settings.

	Fails closed: any error, or no active settings row, returns False.
	"""
	try:
		settings_list = frappe.get_all(
			"Integration Settings", filters={"service": "whatsapp", "is_active": 1}, limit=1
		)
		if not settings_list:
			return False
		doc = frappe.get_doc("Integration Settings", settings_list[0].name)
		for row in doc.credentials or []:
			if row.key == name:
				return _is_truthy(row.get_password("value") or row.value or "")
	except Exception:
		return False
	return False


def _get_whatsapp_credentials(kwargs: dict) -> tuple[str, str]:
	"""Get phone_number_id and access_token from arguments or Integration Settings."""
	phone_number_id = (kwargs.get("phone_number_id") or "").strip()
	access_token = (kwargs.get("access_token") or "").strip()

	if phone_number_id and access_token:
		return phone_number_id, access_token

	# Fallback to Integration Settings or WhatsApp Account DocType
	if frappe.db.exists("DocType", "WhatsApp Account"):
		accounts = frappe.get_all("WhatsApp Account", fields=["name", "phone_number_id"], limit=1)
		if accounts:
			doc = frappe.get_doc("WhatsApp Account", accounts[0].name)
			phone_number_id = phone_number_id or doc.phone_number_id or ""
			access_token = access_token or doc.get_password("token") or ""

	if not phone_number_id or not access_token:
		# Check Integration Settings
		settings_list = frappe.get_all("Integration Settings", filters={"service": "whatsapp", "is_active": 1}, limit=1)
		if settings_list:
			doc = frappe.get_doc("Integration Settings", settings_list[0].name)
			phone_number_id = phone_number_id or doc.get("phone_number_id") or ""
			for row in doc.credentials or []:
				if row.key == "access_token":
					access_token = access_token or row.get_password("value") or ""

	return phone_number_id, access_token


def handle_action(action: str, **kwargs: Any) -> str:
	"""Handle WhatsApp agent tool invocation."""
	action = (action or "").strip().lower()
	# Privilege flags are server-side only: discard anything the LLM supplied.
	for name in _PRIVILEGE_FLAGS:
		kwargs.pop(name, None)

	if action == "send_message":
		return _send_message(kwargs)
	elif action == "send_template":
		return _send_template(kwargs)
	elif action == "list_messages":
		return _list_messages(kwargs)
	elif action == "get_account_info":
		return _get_account_info(kwargs)
	elif action == "list_templates":
		return _list_templates(kwargs)
	elif action == "create_template":
		return _create_template(kwargs)
	else:
		return _error(
			f"Unknown action '{action}'. Supported actions: send_message, send_template, "
			"list_messages, get_account_info, list_templates, create_template"
		)


def _bridge_result(result: dict) -> str:
	"""Pass a bridge result dict through as the tool's JSON response."""
	if result.get("success"):
		return _success({k: v for k, v in result.items() if k != "success"})
	return json.dumps(result, default=str)


def _resolve_bridge_account(kwargs: dict) -> Optional[dict]:
	"""Return the resolved WhatsApp Account dict, or None when unavailable."""
	if not bridge.is_available():
		return None
	acct = bridge.resolve_account((kwargs.get("whatsapp_account") or "").strip() or None)
	if not acct or acct.get("error"):
		return None
	return acct


def _is_proactive_send(kwargs: dict, recipient: str) -> bool:
	"""A free-form send is proactive unless the gateway window is open (A8)."""
	gateway_name = (kwargs.get("gateway_name") or "").strip()
	if not gateway_name:
		return True
	try:
		state = bridge.window_state(gateway_name, recipient)
	except Exception:
		# Fail closed: an unreadable window is treated as no window.
		return True
	return not state.get("open")


def _send_message(kwargs: dict) -> str:
	recipient = bridge.normalize_phone(kwargs.get("to") or kwargs.get("recipient") or kwargs.get("phone_number") or "")
	message = (kwargs.get("message") or kwargs.get("text") or "").strip()

	if not recipient:
		return _error("Recipient phone number ('to') is required")
	if not message:
		return _error("Message text ('message') is required")

	if _is_proactive_send(kwargs, recipient) and not (
		_server_flag("allow_proactive") or _server_flag("allow_whatsapp_management")
	):
		return _refused(
			"proactive_send_not_permitted",
			"No open 24h window for this number. Enable allow_proactive to send a free-form message.",
		)

	acct = _resolve_bridge_account(kwargs)
	if acct:
		try:
			res = bridge.send_text(acct["name"], recipient, message)
		except bridge.BridgeError as e:
			return json.dumps(e.payload, default=str)
		except frappe.PermissionError:
			return _refused("permission_denied", "Not permitted to send WhatsApp messages")
		except Exception as e:
			return _error(f"Failed to send WhatsApp message: {str(e)}")
		return _success({"message_id": res.get("message_id"), "recipient": recipient, "status": "sent", "name": res.get("name")})

	phone_number_id, access_token = _get_whatsapp_credentials(kwargs)
	if not phone_number_id or not access_token:
		return _error("WhatsApp credentials (phone_number_id and access_token) not found")

	url = f"{META_GRAPH_URL}/{phone_number_id}/messages"
	headers = {
		"Authorization": f"Bearer {access_token}",
		"Content-Type": "application/json",
	}
	payload = {
		"messaging_product": "whatsapp",
		"recipient_type": "individual",
		"to": recipient,
		"type": "text",
		"text": {"preview_url": False, "body": message},
	}

	try:
		res = requests.post(url, json=payload, headers=headers, timeout=15)
		data = res.json()
		if res.status_code != 200 or "error" in data:
			return _error(f"WhatsApp API Error: {data.get('error', {}).get('message', res.text)}")

		msg_id = data.get("messages", [{}])[0].get("id")

		# Log in frappe_whatsapp if installed
		if frappe.db.exists("DocType", "WhatsApp Message"):
			try:
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Outgoing",
					"from": phone_number_id,
					"to": recipient,
					"message": message,
					"message_id": msg_id,
					"status": "Sent",
				}).insert(ignore_permissions=True)
			except Exception:
				pass

		return _success({"message_id": msg_id, "recipient": recipient, "status": "sent"})
	except Exception as e:
		return _error(f"Failed to send WhatsApp message: {str(e)}")


def _send_template(kwargs: dict) -> str:
	"""Send a pre-approved WhatsApp template message.

	``parameters``, when supplied, is a plain list of strings that fill the
	template's body placeholders in order (``{{1}}``, ``{{2}}``, ...) -- the
	shape the GW-36 template-selection UI builds from its structured
	parameter rows, rather than requiring a caller to hand-construct Meta's
	full ``components`` array.
	"""
	phone_number_id, access_token = _get_whatsapp_credentials(kwargs)
	recipient = bridge.normalize_phone(kwargs.get("to") or kwargs.get("recipient") or "")
	template_name = (kwargs.get("template_name") or "").strip()
	language_code = (kwargs.get("language_code") or "en").strip()
	parameters = kwargs.get("parameters") or []

	if not recipient or not template_name:
		return _error("Recipient ('to') and 'template_name' are required")
	if not phone_number_id or not access_token:
		return _error("WhatsApp credentials not found")
	if not isinstance(parameters, list):
		return _error("'parameters' must be a list of strings")

	url = f"{META_GRAPH_URL}/{phone_number_id}/messages"
	headers = {
		"Authorization": f"Bearer {access_token}",
		"Content-Type": "application/json",
	}
	template: dict[str, Any] = {
		"name": template_name,
		"language": {"code": language_code},
	}
	if parameters:
		template["components"] = [
			{
				"type": "body",
				"parameters": [{"type": "text", "text": str(value)} for value in parameters],
			}
		]
	payload = {
		"messaging_product": "whatsapp",
		"recipient_type": "individual",
		"to": recipient,
		"type": "template",
		"template": template,
	}

	try:
		res = requests.post(url, json=payload, headers=headers, timeout=15)
		data = res.json()
		if res.status_code != 200 or "error" in data:
			return _error(f"WhatsApp API Error: {data.get('error', {}).get('message', res.text)}")

		return _success(data)
	except Exception as e:
		return _error(f"Failed to send WhatsApp template: {str(e)}")


def _list_messages(kwargs: dict) -> str:
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		return _error("frappe_whatsapp app is not installed or enabled")

	limit = int(kwargs.get("limit") or 20)
	filters = {}
	if kwargs.get("to"):
		filters["to"] = kwargs["to"]
	if kwargs.get("from"):
		filters["from"] = kwargs["from"]

	try:
		messages = frappe.get_all(
			"WhatsApp Message",
			filters=filters,
			fields=["name", "type", "from", "to", "message", "status", "creation"],
			order_by="creation desc",
			limit=limit,
		)
		return _success(messages)
	except Exception as e:
		return _error(f"Failed to fetch WhatsApp messages: {str(e)}")


def _get_account_info(kwargs: dict) -> str:
	phone_number_id, access_token = _get_whatsapp_credentials(kwargs)
	if not phone_number_id or not access_token:
		return _error("WhatsApp credentials not found")

	url = f"{META_GRAPH_URL}/{phone_number_id}"
	headers = {"Authorization": f"Bearer {access_token}"}

	try:
		res = requests.get(url, headers=headers, timeout=10)
		data = res.json()
		if res.status_code != 200 or "error" in data:
			return _error(f"WhatsApp API Error: {data.get('error', {}).get('message', res.text)}")
		return _success(data)
	except Exception as e:
		return _error(f"Failed to fetch WhatsApp account info: {str(e)}")


def _list_templates(kwargs: dict) -> str:
	"""List WhatsApp Templates via the bridge (read-only)."""
	if not bridge.is_available():
		return _error("bridge_unavailable")
	account_name = (kwargs.get("whatsapp_account") or "").strip() or None
	status = (kwargs.get("status") or "").strip() or None
	try:
		rows = bridge.list_templates(account_name, status)
	except frappe.PermissionError:
		return _refused("permission_denied", "Not permitted to read WhatsApp Templates")
	except Exception as e:
		return _error(f"Failed to list WhatsApp templates: {str(e)}")
	return _success(rows)


def _create_template(kwargs: dict) -> str:
	"""Create a WhatsApp template via the bridge (management; A8 gated).

	Requires ``allow_whatsapp_management`` (or ``allow_proactive``) enabled in
	the server-side Integration Settings. Accepted fields: ``template_name``,
	``category`` (MARKETING/UTILITY/AUTHENTICATION), ``language_code``, ``body``.
	"""
	if not (_server_flag("allow_whatsapp_management") or _server_flag("allow_proactive")):
		return _refused(
			"management_not_permitted",
			"Template creation is disabled. Enable allow_whatsapp_management in the tool config.",
		)
	if not bridge.is_available():
		return _error("bridge_unavailable")
	acct = _resolve_bridge_account(kwargs)
	if not acct:
		return _error("no_account")

	payload = {
		key: kwargs[key]
		for key in ("template_name", "category", "language_code", "body")
		if kwargs.get(key) is not None
	}
	try:
		result = bridge.create_template(acct["name"], payload)
	except bridge.BridgeError as e:
		return json.dumps(e.payload, default=str)
	except frappe.PermissionError:
		return _refused("permission_denied", "Not permitted to create WhatsApp Templates")
	except Exception as e:
		return _error(f"Failed to create WhatsApp template: {str(e)}")
	return _bridge_result(result)
