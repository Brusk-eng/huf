# Copyright (c) 2026, Huf and contributors
# For license information, please see license.txt

"""In-process stand-in for the Meta WhatsApp Graph API, for unit tests.

:class:`FakeGraph` mimics the slice of ``requests`` the WhatsApp gateway uses
(``post`` / ``get`` / ``request``), so tests can pass it to code as a drop-in
HTTP client without patching anything globally::

    graph = FakeGraph()
    adapter = WhatsAppAdapter(..., http_post=graph.post)
    adapter.send(...)  # no network
    graph.sent_messages()  # what was sent

A successful ``POST .../<phone_id>/messages`` answers
``{"messages": [{"id": "wamid.FAKE<n>"}]}`` with ``n`` increasing per send.
Two failure modes can be enabled per instance:

* ``window_closed=True`` -> Meta error 131047 (re-engagement message required)
* ``rate_limited=True`` -> Meta error 131056 (pair rate limit hit)

When both are set, ``window_closed`` wins. Requests to any endpoint other than
``/messages`` always succeed, so the same fake also stands in for media uploads
and template reads.
"""

from __future__ import annotations

import json as jsonlib
from typing import Any

import requests

_ERROR_MESSAGES = {
	131047: "Re-engagement message",
	131056: "Pair rate limit hit",
}


class FakeResponse:
	"""Minimal ``requests.Response`` lookalike."""

	def __init__(self, payload: dict[str, Any], status_code: int = 200, url: str | None = None):
		self._payload = payload
		self.status_code = status_code
		self.url = url
		self.headers = {"content-type": "application/json"}
		self.text = jsonlib.dumps(payload)

	def json(self) -> dict[str, Any]:
		return self._payload

	def raise_for_status(self) -> None:
		if self.status_code >= 400:
			message = f"{self.status_code} Client Error for url: {self.url}"
			raise requests.HTTPError(message, response=self)


class FakeGraph:
	"""Records outbound Graph calls and answers them locally."""

	def __init__(self, *, window_closed: bool = False, rate_limited: bool = False):
		self.window_closed = window_closed
		self.rate_limited = rate_limited
		self.calls: list[dict[str, Any]] = []
		self._message_seq = 0

	def post(
		self,
		url: str,
		*,
		json: Any = None,
		data: Any = None,
		headers: dict[str, str] | None = None,
		timeout: float | None = None,
		params: dict[str, Any] | None = None,
		**kwargs: Any,
	) -> FakeResponse:
		return self.request(
			"POST", url, json=json, data=data, headers=headers, timeout=timeout, params=params, **kwargs
		)

	def get(
		self,
		url: str,
		*,
		headers: dict[str, str] | None = None,
		timeout: float | None = None,
		params: dict[str, Any] | None = None,
		**kwargs: Any,
	) -> FakeResponse:
		return self.request("GET", url, headers=headers, timeout=timeout, params=params, **kwargs)

	def request(
		self,
		method: str,
		url: str,
		*,
		json: Any = None,
		data: Any = None,
		headers: dict[str, str] | None = None,
		timeout: float | None = None,
		params: dict[str, Any] | None = None,
		**kwargs: Any,
	) -> FakeResponse:
		method = str(method).upper()
		body = json if json is not None else data
		if isinstance(body, (str, bytes)):
			try:
				body = jsonlib.loads(body)
			except ValueError:
				pass
		self.calls.append(
			{
				"method": method,
				"url": url,
				"body": body,
				"headers": dict(headers or {}),
				"params": dict(params or {}),
				"timeout": timeout,
			}
		)
		if method == "POST" and _is_messages_url(url):
			if self.window_closed:
				return _error_response(131047, url)
			if self.rate_limited:
				return _error_response(131056, url)
			self._message_seq += 1
			return FakeResponse({"messages": [{"id": f"wamid.FAKE{self._message_seq}"}]}, url=url)
		return FakeResponse({"success": True}, url=url)

	def sent_messages(self) -> list[dict[str, Any]]:
		"""Recorded outbound message sends (``POST .../messages`` only)."""
		return [c for c in self.calls if c["method"] == "POST" and _is_messages_url(c["url"])]

	def reset(self) -> None:
		"""Forget recorded calls and restart message numbering."""
		self.calls.clear()
		self._message_seq = 0


def _is_messages_url(url: str) -> bool:
	path = str(url).split("?", 1)[0].rstrip("/")
	return path.endswith("/messages")


def _error_response(code: int, url: str) -> FakeResponse:
	payload = {
		"error": {
			"message": _ERROR_MESSAGES[code],
			"type": "OAuthException",
			"code": code,
			"fbtrace_id": "FAKE",
		}
	}
	return FakeResponse(payload, status_code=400, url=url)
