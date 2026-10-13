# Copyright (c) 2026, Huf and contributors
# For license information, please see license.txt

"""Meta WhatsApp Cloud API adapter for Huf Gateway.

Integrates WhatsApp Cloud API with Huf's fail-closed Gateway ingress and routing,
leveraging frappe_whatsapp for document persistence and message tracking.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import frappe
from huf.ai.gateway_adapters import whatsapp_bridge as bridge
from huf.ai.gateway_adapters.adapter import GatewayAdapter
from huf.ai.gateway_adapters.types import (
	GatewayAttachment,
	GatewayCapabilities,
	GatewayCredentialField,
	GatewayCredentialSchema,
	GatewayInboundRequest,
	GatewayReply,
	NormalizedGatewayEvent,
	OutboundDelivery,
)

GRAPH_VERSION = "v21.0"
META_GRAPH_URL = f"https://graph.facebook.com/{GRAPH_VERSION}"
DEFAULT_TEMPLATE_LANGUAGE = "en_US"
_MEDIA_TYPES = ("image", "document", "audio", "video", "sticker")
_TEXTUAL_TYPES = ("text", "interactive", "button") + _MEDIA_TYPES


class WhatsAppWindowClosed(frappe.ValidationError):
	"""The 24h customer-service window is closed and no template fallback is configured."""


@dataclass(frozen=True)
class WhatsAppParsed:
	"""Everything actionable in one Meta webhook delivery (batched payloads, A5/A14)."""

	messages: tuple[NormalizedGatewayEvent, ...]
	statuses: tuple[dict, ...]
	template_updates: tuple[dict, ...]
	ignored: int


def _requests_post(url: str, *, json: dict, headers: dict, timeout: int) -> Any:
	import requests

	return requests.post(url, json=json, headers=headers, timeout=timeout)


class WhatsAppGatewayAdapter(GatewayAdapter):
	"""Authenticate Meta WhatsApp Cloud API webhooks and deliver window-aware replies."""

	provider_id = "whatsapp"
	credential_schema = GatewayCredentialSchema(
		(
			GatewayCredentialField("phone_number_id", "Phone Number ID", secret=False),
			GatewayCredentialField("access_token", "Meta Permanent/System Access Token"),
			GatewayCredentialField("webhook_verify_token", "Webhook Verify Token"),
			GatewayCredentialField("app_secret", "Meta App Secret (for HMAC signature verification)", required=True),
		)
	)
	capabilities = GatewayCapabilities(
		frozenset({"webhook"}),
		supports_text_reply=True,
		supports_thread_reply=False,  # A2: quoting never fragments the conversation
		supports_media_reply=False,  # GW-32: send_reply only sends text today
		max_outbound_messages_per_second=80,
	)

	def __init__(
		self,
		credentials: Mapping[str, str],
		*,
		http_post: Callable[..., Any] = _requests_post,
		gateway_context: Mapping[str, Any] | None = None,
	) -> None:
		missing = self.credential_schema.missing_required(credentials)
		if missing:
			raise ValueError(f"WhatsApp adapter is missing required credentials: {', '.join(missing)}")
		self._phone_number_id = str(credentials["phone_number_id"]).strip()
		self._access_token = str(credentials["access_token"]).strip()
		self._verify_token = str(credentials["webhook_verify_token"]).strip()
		self._app_secret = str(credentials.get("app_secret") or "").strip()
		self._http_post = http_post
		ctx = gateway_context or {}
		self._gateway_name = str(ctx.get("gateway_name") or "").strip()
		self._account = str(ctx.get("whatsapp_account") or "").strip()
		self._fallback_template = str(ctx.get("fallback_template") or "").strip()

	# ------------------------------------------------------------------ ingress
	def verify_url(self, request: GatewayInboundRequest) -> str:
		"""Return Meta hub.challenge if the verify token matches the GET request."""
		query = request.query or {}
		token = str(query.get("hub.verify_token") or query.get("hub_verify_token") or "")
		challenge = query.get("hub.challenge") or query.get("hub_challenge") or ""
		if token and hmac.compare_digest(token.encode("utf-8"), self._verify_token.encode("utf-8")):
			return challenge
		raise frappe.PermissionError("WhatsApp webhook verification token mismatch")

	def verify_inbound(self, request: GatewayInboundRequest) -> bool:
		"""Verify the Meta webhook signature (fail closed).

		GET: hub.verify_token must match. POST: X-Hub-Signature-256 must equal
		HMAC-SHA256(app_secret, body); a missing app_secret always fails.
		"""
		if request.method == "GET":
			query = request.query or {}
			token = str(query.get("hub.verify_token") or query.get("hub_verify_token") or "")
			return bool(token) and hmac.compare_digest(token.encode("utf-8"), self._verify_token.encode("utf-8"))

		if not self._app_secret:
			return False

		headers = request.headers or {}
		signature = headers.get("x-hub-signature-256") or headers.get("X-Hub-Signature-256")
		if not signature or not signature.startswith("sha256="):
			return False

		expected = hmac.new(self._app_secret.encode("utf-8"), request.body, "sha256").hexdigest()
		return hmac.compare_digest(signature[7:], expected)

	def parse_payload(self, request: GatewayInboundRequest) -> WhatsAppParsed:
		"""Parse every entry/change of a (possibly batched) payload. Never raises on benign content."""
		payload = self._payload(request)
		if payload is None:
			raise ValueError("Invalid JSON payload in WhatsApp request")

		messages: list[NormalizedGatewayEvent] = []
		statuses: list[dict] = []
		template_updates: list[dict] = []
		ignored = 0

		for entry in _as_list(payload.get("entry")):
			for change in _as_list(entry.get("changes") if isinstance(entry, dict) else None):
				if not isinstance(change, dict):
					ignored += 1
					continue
				value = change.get("value")
				if not isinstance(value, dict):
					ignored += 1
					continue
				field = change.get("field")
				if field == "message_template_status_update":
					template_updates.append(dict(value))
					continue
				items = _as_list(value.get("messages")) + _as_list(value.get("statuses"))
				metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
				phone_id = str(metadata.get("phone_number_id") or "")
				if phone_id and not self._phone_id_accepted(phone_id):
					ignored += max(len(items), 1)
					continue
				for status in _as_list(value.get("statuses")):
					if isinstance(status, dict):
						statuses.append(dict(status))
					else:
						ignored += 1
				if "messages" not in value:
					if not value.get("statuses"):
						ignored += 1  # account_update, errors-only, etc.
					continue
				names = _contact_names(value)
				for msg in _as_list(value.get("messages")):
					event = self._message_event(msg, metadata, names)
					if event is None:
						ignored += 1
					else:
						messages.append(event)

		return WhatsAppParsed(tuple(messages), tuple(statuses), tuple(template_updates), ignored)

	def normalize_inbound(self, request: GatewayInboundRequest) -> NormalizedGatewayEvent:
		"""Return the first message event; ValueError only if nothing actionable exists."""
		parsed = self.parse_payload(request)
		if parsed.messages:
			return parsed.messages[0]
		if parsed.statuses or parsed.template_updates:
			raise ValueError("WhatsApp payload carries only status or template events, no message")
		raise ValueError("WhatsApp payload contains no message, status or template events")

	def _phone_id_accepted(self, payload_phone_id: str) -> bool:
		if payload_phone_id != self._phone_number_id:
			return False
		if self._account and bridge.is_available():
			return bool(bridge.account_matches_phone_id(self._account, payload_phone_id))
		return True

	def _message_event(
		self, msg: Any, metadata: Mapping[str, Any], names: Mapping[str, str]
	) -> NormalizedGatewayEvent | None:
		if not isinstance(msg, dict):
			return None
		msg_type = msg.get("type") or "text"
		if msg_type not in _TEXTUAL_TYPES:
			return None
		provider_event_id = str(msg.get("id") or "").strip()
		sender = bridge.normalize_phone(msg.get("from"))
		if not provider_event_id or not sender:
			return None

		message_text = ""
		if msg_type == "text":
			message_text = (msg.get("text") or {}).get("body") or ""
		elif msg_type == "interactive":
			interactive = msg.get("interactive") or {}
			for key in ("button_reply", "list_reply"):
				if key in interactive:
					reply = interactive[key] or {}
					message_text = reply.get("title") or reply.get("id") or ""
					break
		elif msg_type == "button":
			message_text = (msg.get("button") or {}).get("text") or (msg.get("button") or {}).get("payload") or ""
		else:
			message_text = (msg.get(msg_type) or {}).get("caption") or f"[{msg_type} message]"
		if not message_text.strip():
			return None

		attachments = []
		if msg_type in _MEDIA_TYPES:
			media = msg.get(msg_type) or {}
			media_id = str(media.get("id") or "")
			if media_id:
				attachments.append(
					GatewayAttachment(
						mime_type=str(media.get("mime_type") or ""),
						filename=str(media.get("filename") or ""),
						file_id=media_id,
						kind=msg_type,
					)
				)

		context = msg.get("context") if isinstance(msg.get("context"), dict) else {}
		quoted = str(context.get("id") or "") or None
		return NormalizedGatewayEvent(
			provider_event_id=provider_event_id,
			sender_id=sender,
			conversation_id=sender,
			message_text=message_text,
			thread_id=None,  # A2: quoted id stays in raw_payload only
			is_room=False,
			raw_payload={"message": msg, "metadata": dict(metadata), "quoted_message_id": quoted},
			display_name=names.get(sender, ""),
			attachments=tuple(attachments),
		)

	# ------------------------------------------------------------------ egress
	def _bridge_mode(self) -> bool:
		return bool(self._account) and bridge.is_available()

	def send_reply(self, reply: GatewayReply) -> OutboundDelivery:
		"""Send a reply; window-aware (24h) with optional template fallback. One sender only (A1)."""
		if self._gateway_name:
			state = bridge.window_state(self._gateway_name, reply.conversation_id)
			if not state.get("open"):
				if self._fallback_template:
					return self.send_template(
						reply.conversation_id, self._fallback_template, DEFAULT_TEMPLATE_LANGUAGE, [], trusted=True
					)
				raise WhatsAppWindowClosed(
					f"WhatsApp 24-hour customer-service window is closed for {reply.conversation_id}. "
					"Free-form replies are not allowed; set a Fallback template on the Gateway "
					"or wait for the customer to message again."
				)

		if self._bridge_mode():
			result = bridge.send_text(
				self._account,
				reply.conversation_id,
				reply.text,
				reply_to=reply.reply_to_provider_message_id,
				trusted=True,
			)
			return _delivery_from_bridge(result)

		data: dict[str, Any] = {
			"messaging_product": "whatsapp",
			"recipient_type": "individual",
			"to": bridge.normalize_phone(reply.conversation_id),
			"type": "text",
			"text": {"preview_url": False, "body": reply.text},
		}
		if reply.reply_to_provider_message_id:
			data["context"] = {"message_id": reply.reply_to_provider_message_id}
		return self._graph_send(data)

	def send_template(
		self,
		to: str,
		template_name: str,
		language_code: str = DEFAULT_TEMPLATE_LANGUAGE,
		body_params: list | tuple | None = None,
		trusted: bool = False,
	) -> OutboundDelivery:
		"""Send an approved template (always allowed, window-independent)."""
		params = [str(p) for p in (body_params or [])]
		if self._bridge_mode():
			result = bridge.send_template(self._account, to, template_name, params, trusted=trusted)
			return _delivery_from_bridge(result)
		template: dict[str, Any] = {"name": template_name, "language": {"code": language_code or DEFAULT_TEMPLATE_LANGUAGE}}
		if params:
			template["components"] = [
				{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}
			]
		return self._graph_send(
			{
				"messaging_product": "whatsapp",
				"recipient_type": "individual",
				"to": bridge.normalize_phone(to),
				"type": "template",
				"template": template,
			}
		)

	def _graph_send(self, data: dict[str, Any]) -> OutboundDelivery:
		url = f"https://graph.facebook.com/{GRAPH_VERSION}/{self._phone_number_id}/messages"
		headers = {
			"Authorization": f"Bearer {self._access_token}",
			"Content-Type": "application/json",
		}
		response = self._http_post(url, json=data, headers=headers, timeout=15)
		if hasattr(response, "raise_for_status"):
			response.raise_for_status()
		body = response.json() if hasattr(response, "json") else response
		if not isinstance(body, Mapping) or not body.get("messages"):
			raise ValueError(f"WhatsApp API rejected outbound message: {body}")
		return OutboundDelivery(provider_message_id=str(body["messages"][0]["id"]), provider_response=dict(body))

	@staticmethod
	def _payload(request: GatewayInboundRequest) -> dict[str, Any] | None:
		try:
			payload = json.loads(request.body.decode("utf-8"))
		except (UnicodeDecodeError, json.JSONDecodeError):
			return None
		return payload if isinstance(payload, dict) else None


def _as_list(value: Any) -> list:
	return list(value) if isinstance(value, (list, tuple)) else []


def _contact_names(value: Mapping[str, Any]) -> dict[str, str]:
	names: dict[str, str] = {}
	for contact in _as_list(value.get("contacts")):
		if isinstance(contact, dict):
			wa_id = bridge.normalize_phone(contact.get("wa_id"))
			name = (contact.get("profile") or {}).get("name") if isinstance(contact.get("profile"), dict) else ""
			if wa_id and name:
				names[wa_id] = str(name)
	return names


def _delivery_from_bridge(result: Mapping[str, Any]) -> OutboundDelivery:
	message_id = str(result.get("message_id") or result.get("name") or "")
	return OutboundDelivery(provider_message_id=message_id, provider_response=dict(result))
