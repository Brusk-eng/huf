"""Runtime bridge between Gateway routing and provider-native adapters.

The module is intentionally lazy about provider imports: the Gateway foundation
can remain independently reviewable while regional adapter packages are merged
on top of the Adapter SDK.  A configured gateway still fails closed if its
adapter package or required credentials are unavailable.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from huf.ai.gateway_adapters.provider_ids import provider_to_service_id
from huf.ai.gateway_adapters.registered import get_adapter_class


EXEMPT_AUTH_ROUTES: frozenset[str] = frozenset(
	{
		# Bot Framework (and every other native-adapter) webhook: Teams posts
		# ``Authorization: Bearer <token>``; verification happens inside
		# ``TeamsGatewayAdapter.verify_inbound`` after this route is reached.
		"/api/method/huf.ai.gateway_webhook.handle_gateway_webhook",
		# Teams *Outgoing Webhook*: posts ``Authorization: HMAC <base64>``,
		# verified inside ``handle_teams_outgoing_webhook`` itself.
		"/api/method/huf.ai.tools.teams_webhook.handle_teams_outgoing_webhook",
	}
)


def exempt_gateway_webhook_auth() -> None:
	"""``auth_hooks`` entry: let allowlisted webhook routes reach their own auth.

	Frappe's core ``validate_auth`` (``frappe/auth.py``) splits the
	``Authorization`` header and, whenever it has exactly two parts (a scheme
	and a token — e.g. ``Bearer <token>`` or ``HMAC <token>``), first tries
	OAuth/API-key auth and then, if neither matched, raises
	``AuthenticationError`` unless ``frappe.session.user`` is already a real
	user. That check runs *before* Frappe ever looks at ``allow_guest=True``
	on the target whitelisted method, so any Gateway webhook that authenticates
	itself with a custom two-part ``Authorization`` scheme (rather than
	Frappe's own token/basic/oauth schemes) is rejected at 401 by core before
	its own signature/HMAC verification ever runs.

	This hook runs inside ``validate_auth`` (via ``validate_auth_via_hooks``),
	strictly before that terminal guest check. For routes on the
	``EXEMPT_AUTH_ROUTES`` allowlist only, it authenticates the request as a
	deliberately unprivileged, disabled technical user so the terminal check
	passes and control reaches the whitelisted method's own verification
	(HMAC/Bearer/signature). The route's handler still fails closed on a bad
	or missing signature; this hook only gets it past the framework-level
	guest check, it grants no document permissions of its own.
	"""
	request = frappe.request
	if request is None or request.method != "POST":
		return
	if request.path not in EXEMPT_AUTH_ROUTES:
		return

	authorization = frappe.get_request_header("Authorization") or ""
	if len(authorization.split(" ")) != 2:
		return

	if frappe.session.user not in ("", "Guest"):
		return

	frappe.set_user(_gateway_webhook_auth_user())


def _gateway_webhook_auth_user() -> str:
	"""Return (creating once) the disabled technical user used only to satisfy
	Frappe's terminal guest check for allowlisted webhook routes.

	It is created ``enabled=0`` and with no roles: it cannot log in, and
	nothing about it grants any Gateway/Integration document permission —
	those doctypes are exempt from generic /api/resource access entirely
	(see the ``has_permission``/``permission_query_conditions`` hooks in
	``huf/hooks.py``). Its only job is to be a non-"Guest", non-empty
	``frappe.session.user`` value.
	"""
	technical_user = "gateway-webhook@huf.system"
	if not frappe.db.exists("User", technical_user):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": technical_user,
				"first_name": "Gateway Webhook (system)",
				"user_type": "System User",
				"enabled": 0,
				"send_welcome_email": 0,
				"roles": [],
			}
		).insert(ignore_permissions=True, ignore_if_duplicate=True)
	return technical_user


# Doctypes in the Gateway/Integration family. None of them have a per-row
# owner or "shared with" concept the way Agent/Agent Run do: a Gateway Access
# Entry, Integration Credential, etc. are account-wide configuration, and the
# app only ever reads/writes them through its own bespoke whitelisted methods
# (gateway pairing, adapter routing, the Integrations UI's own controllers),
# never through a generic list/get. Rather than invent row-level semantics
# these doctypes were never designed for, generic ``/api/resource/<DocType>``
# access is blocked outright for everyone except System Manager, matching how
# the app itself never talks to them that way.
GATEWAY_INTEGRATION_DOCTYPES: frozenset[str] = frozenset(
	{
		"Gateway",
		"Gateway Access Entry",
		"Gateway Event",
		"Gateway Binding",
		"Integration Settings",
		"Integration Service",
		"Integration Credential",
	}
)


def has_permission_gateway_family(doc, ptype: str = "read", user: str | None = None) -> bool:
	"""``has_permission`` hook: only System Manager may touch these doctypes.

	Registered for every doctype in ``GATEWAY_INTEGRATION_DOCTYPES``. These
	records carry credentials and routing/permission data for every configured
	Gateway and Integration; the app itself never reads or writes them through
	generic ``/api/resource`` access, only through its own whitelisted
	pairing/adapter/API surface (which runs with ``ignore_permissions=True``
	where appropriate and does its own checks). A user with a generic DocType
	read/write role permission on these doctypes, but who is not a System
	Manager, must not gain list/get/save access to them just because the
	doctype exists — hence this hook rather than leaving them to the
	role-permission default.
	"""
	user = user or frappe.session.user
	return bool(frappe.db.get_value("Has Role", {"parent": user, "role": "System Manager"}, "name"))


def get_permission_query_conditions_gateway_family(user: str | None = None) -> str:
	"""``permission_query_conditions`` hook mirroring ``has_permission_gateway_family``.

	Without this, a non-System-Manager user with a generic read role on one of
	these doctypes could still see rows show up in a ``/api/resource`` list
	call (list views apply ``permission_query_conditions``, not
	``has_permission``, to filter rows) even though ``has_permission`` would
	refuse a ``get`` on any individual one.
	"""
	user = user or frappe.session.user
	if frappe.db.get_value("Has Role", {"parent": user, "role": "System Manager"}, "name"):
		return ""
	return "1=0"


def _adapter_class_for_provider(provider: str):
	"""Resolve a ``Gateway.provider`` display value to its adapter class.

	Routes through the shared ``provider_to_service_id`` transform and the
	``GatewayAdapterRegistry`` (via ``get_adapter_class``, which imports the
	adapter's module lazily on first use) rather than a hardcoded map.
	"""
	try:
		return get_adapter_class(provider_to_service_id(provider))
	except KeyError as exc:
		raise frappe.ValidationError(_("No installed Gateway Adapter supports this channel.")) from exc
	except (ImportError, AttributeError) as exc:
		raise frappe.ValidationError(
			_("The Gateway Adapter package for this channel is not installed.")
		) from exc


def _gateway_credentials(gateway) -> dict[str, str]:
	if not gateway.integration_settings:
		raise frappe.ValidationError(_("This gateway needs a connected integration for its credentials."))
	settings = frappe.get_doc("Integration Settings", gateway.integration_settings)
	credentials = {}
	for row in settings.credentials or []:
		if row.key:
			credentials[row.key] = row.get_password("value") or ""
	return credentials


def get_gateway_adapter(gateway):
	"""Instantiate the configured adapter without exposing stored credentials."""
	adapter_class = _adapter_class_for_provider(gateway.provider)
	credentials = _gateway_credentials(gateway)
	if gateway.provider == "WhatsApp":
		return adapter_class(
			credentials,
			gateway_context={
				"gateway_name": gateway.name,
				"whatsapp_account": (gateway.get("whatsapp_account") or "").strip(),
				"fallback_template": (gateway.get("fallback_template") or "").strip(),
			},
		)
	return adapter_class(credentials)


def _inbound_request():
	from huf.ai.gateway_adapters.types import GatewayInboundRequest

	request = frappe.request
	if not request:
		raise frappe.ValidationError(_("A provider HTTP request is required."))
	return GatewayInboundRequest(
		body=request.get_data(),
		headers=dict(request.headers),
		query=dict(request.args),
		method=request.method,
	)


def _event_context(event) -> dict[str, Any]:
	return {
		"sender_id": event.sender_id,
		"conversation_id": event.conversation_id,
		"thread_id": event.thread_id,
		"message_text": event.message_text,
		"is_room": event.is_room,
		"mentioned": event.mentioned,
		"display_name": getattr(event, "display_name", "") or "",
	}


def _text_response(value: str) -> None:
	"""Return a provider challenge as raw text rather than Frappe API JSON."""
	frappe.local.response.update(
		{"type": "txt", "doctype": "gateway-callback", "result": value}
	)


def _ingest_rate_gate() -> None:
	"""Count one request against the per-gateway ingest rate limit (100/60s).

	This used to be a decorator on ``handle_gateway_webhook``, which counted
	every POST. Providers that batch and interleave delivery receipts
	(WhatsApp) must not have status-only traffic consume the ingest budget, so
	the same ``rate_limit`` is now applied explicitly: up front for
	single-event providers (unchanged), and only for forged requests or
	payloads that actually carry messages for ``parse_payload`` providers.
	"""


_ingest_rate_gate = rate_limit(key="gateway_name", limit=100, seconds=60)(_ingest_rate_gate)


def _supports_batch_parse(adapter) -> bool:
	# Class-level lookup so duck-typed/mock adapters do not accidentally qualify.
	return callable(getattr(type(adapter), "parse_payload", None))


def _handle_batched_payload(gateway, adapter, request) -> dict:
	"""Fan out one verified, possibly batched, provider delivery (WhatsApp)."""
	from huf.ai.gateway_adapters import whatsapp_bridge as bridge
	from huf.ai.gateway_service import ingest_gateway_event

	try:
		parsed = adapter.parse_payload(request)
	except ValueError:
		frappe.local.response.http_status_code = 400
		return {"success": False, "error": "Invalid payload"}

	if parsed.messages:
		_ingest_rate_gate()

	ingested = duplicates = 0
	results: list[dict] = []
	first_error = None
	account_name = None
	for event in parsed.messages:
		try:
			result = ingest_gateway_event(
				gateway.name,
				event.provider_event_id,
				_event_context(event),
				verified_sender=True,
				raw_payload=dict(event.raw_payload),
			)
		except Exception as exc:  # keep fanning out; re-raised below so the provider retries (deduped)
			frappe.log_error(frappe.get_traceback(), "WhatsApp gateway ingest failed")
			first_error = first_error or exc
			continue
		results.append(result)
		if result.get("duplicate"):
			duplicates += 1
			continue
		ingested += 1
		if result.get("status") == "Rejected":
			continue
		try:  # best-effort mirror into frappe_whatsapp (A4)
			if account_name is None:
				account_name = (getattr(gateway, "whatsapp_account", "") or "").strip()
				if not account_name:
					resolved = bridge.resolve_account(None)
					account_name = (resolved or {}).get("name") or ""
			message = (event.raw_payload or {}).get("message")
			if account_name and isinstance(message, dict):
				bridge.record_incoming(account_name, message, getattr(event, "display_name", "") or None)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "WhatsApp record_incoming failed")

	forwarded = 0
	changes = [{"field": "messages", "value": {"statuses": [status]}} for status in parsed.statuses]
	changes += [{"field": "message_template_status_update", "value": dict(update)} for update in parsed.template_updates]
	for change in changes:
		try:
			if bridge.forward_status(change):
				forwarded += 1
		except Exception:
			pass  # best effort, never fail the ack

	if first_error is not None:
		raise first_error
	return {
		"success": True,
		"messages": len(parsed.messages),
		"ingested": ingested,
		"duplicates": duplicates,
		"statuses": len(parsed.statuses),
		"template_updates": len(parsed.template_updates),
		"forwarded": forwarded,
		"ignored": parsed.ignored,
		"results": results,
	}


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def handle_gateway_webhook() -> dict | None:
	"""Verify a native provider callback, then hand only normalized data to Gateway.

	The URL contains a Gateway document name, never a credential.  Native
	verification happens before persistence or queueing, and no provider payload
	is normalized until that verification succeeds.

	gateway_name is read directly from the query string rather than taken as
	a function argument: every provider here posts application/json, and
	frappe.app.make_form_dict replaces frappe.form_dict wholesale with the
	parsed JSON body whenever Content-Type is JSON, so a query-string kwarg
	never actually reaches this function on a real webhook call.
	"""
	from huf.ai.gateway_service import ingest_gateway_event

	gateway_name = frappe.request.args.get("gateway_name") if frappe.request is not None else None
	if not gateway_name:
		frappe.local.response.http_status_code = 400
		return {"success": False, "error": "Missing gateway_name"}

	try:
		gateway = frappe.get_doc("Gateway", gateway_name)
	except frappe.DoesNotExistError:
		frappe.local.response.http_status_code = 404
		return {"success": False, "error": "Unknown gateway"}
	if not gateway.is_enabled:
		frappe.local.response.http_status_code = 403
		return {"success": False, "error": "Gateway is disabled"}

	adapter = get_gateway_adapter(gateway)
	request = _inbound_request()
	if request.method == "GET" and hasattr(adapter, "verify_url"):
		try:
			challenge = adapter.verify_url(request) or ""
		except frappe.PermissionError:
			frappe.local.response.http_status_code = 403
			return {"success": False, "error": "Webhook verification failed"}
		_text_response(challenge)
		return None
	batched = _supports_batch_parse(adapter)
	if not batched:
		_ingest_rate_gate()
	if not adapter.verify_inbound(request):
		if batched:
			_ingest_rate_gate()  # forged/unsigned traffic still consumes the budget
		frappe.local.response.http_status_code = 401
		return {"success": False, "error": "Provider verification failed"}
	if batched:
		return _handle_batched_payload(gateway, adapter, request)

	event = adapter.normalize_inbound(request)
	result = ingest_gateway_event(
		gateway.name,
		event.provider_event_id,
		_event_context(event),
		verified_sender=True,
		raw_payload=dict(event.raw_payload),
	)
	return {"success": True, **result}


def send_gateway_reply(gateway, event, text: str):
	"""Deliver one completed Agent response through the same verified gateway."""
	from huf.ai.gateway_adapters.types import GatewayReply

	adapter = get_gateway_adapter(gateway)
	if gateway.provider == "WhatsApp":
		# A2: WhatsApp never fragments conversations by thread; the reply quotes
		# the inbound wamid, which is the event's provider_event_id.
		return adapter.send_reply(
			GatewayReply(
				conversation_id=event.conversation_id,
				text=text,
				thread_id=None,
				reply_to_provider_message_id=event.provider_event_id or None,
			)
		)
	return adapter.send_reply(
		GatewayReply(
			conversation_id=event.conversation_id,
			text=text,
			thread_id=event.thread_id or None,
			reply_to_provider_message_id=event.thread_id or None,
		)
	)
