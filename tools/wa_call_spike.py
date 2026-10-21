#!/usr/bin/env python3
"""WhatsApp Business Calling API spike (T10): answer one inbound call, play an
audio file, hang up, log timings.  Standalone; NOT part of the huf package.

Modes
  serve     aiohttp server for the Meta `calls` webhook (GET challenge, POST
            with X-Hub-Signature-256), answers inbound calls with a file.
  --selftest  two local aiortc peers + fake Graph, no Meta, no network.
  --dry-run   with `serve`: never call Graph, only log what would be sent.

Env (no secrets in file): WA_PHONE_ID, WA_TOKEN, WA_APP_SECRET,
WA_VERIFY_TOKEN, WA_GRAPH_VERSION (default v21.0 -- unverified, set current).

Requires: pip install aiortc aiohttp ; ffmpeg on PATH (MediaPlayer uses it).
Never run live without the owner's go-ahead; see docs/WHATSAPP_CALLING_DESIGN.md.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Awaitable, Callable, Optional

try:
    import aiohttp
    from aiohttp import web
    from aiortc import RTCPeerConnection, RTCSessionDescription
    from aiortc.contrib.media import MediaPlayer, MediaBlackhole
    from aiortc.mediastreams import MediaStreamError, MediaStreamTrack
    from av import AudioFrame
    _IMPORT_ERROR: Optional[Exception] = None
except ImportError as exc:  # pragma: no cover
    _IMPORT_ERROR = exc
    MediaStreamTrack = object  # type: ignore[assignment,misc]

log = logging.getLogger("wa_call_spike")

# post(path, body) -> response json.  Injectable for tests / dry-run.
PostFn = Callable[[str, dict], Awaitable[dict]]


def need_deps() -> None:
    if _IMPORT_ERROR:
        sys.exit(f"Missing dependency ({_IMPORT_ERROR}). Run: pip install aiortc aiohttp  (and install ffmpeg)")


def verify_signature(app_secret: str, raw: bytes, header: str) -> bool:
    """X-Hub-Signature-256: 'sha256=<hex hmac of raw body>'."""
    if not header or not header.startswith("sha256="):
        return False
    want = hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, header[7:])


class GatedAudioTrack(MediaStreamTrack):  # type: ignore[misc]
    """Sends silence until open_gate(), then relays the file. The file (ffmpeg)
    is not read at all before the gate opens, so nothing is consumed/clipped."""

    kind = "audio"

    def __init__(self, player: Any):
        super().__init__()
        self._player = player
        self._open = asyncio.Event()
        self._pts = 0
        self.first_real_frame_at: Optional[float] = None
        self.finished = asyncio.Event()
        self.real_frames = 0

    def open_gate(self) -> None:
        self._open.set()

    async def recv(self):
        if self.readyState != "live":
            raise MediaStreamError
        if not self._open.is_set():
            await asyncio.sleep(0.02)
            frame = AudioFrame(format="s16", layout="stereo", samples=960)
            for p in frame.planes:
                p.update(bytes(p.buffer_size))
            frame.sample_rate = 48000
        else:
            try:
                frame = await self._player.audio.recv()
            except MediaStreamError:
                self.finished.set()
                raise
            if self.first_real_frame_at is None:
                self.first_real_frame_at = time.monotonic()
            self.real_frames += 1
        frame.pts = self._pts
        self._pts += frame.samples
        frame.time_base = __import__("fractions").Fraction(1, 48000)
        return frame


class CallHandler:
    """One handler per process; one CallSession per call_id."""

    def __init__(self, post: PostFn, audio_file: str, play_seconds: float,
                 connect_timeout: float = 20.0, max_calls: int = 1):
        self.post = post
        self.audio_file = audio_file
        self.play_seconds = play_seconds
        self.connect_timeout = connect_timeout
        self.max_calls = max_calls
        self.sessions: dict[str, dict] = {}
        self.timings: dict[str, dict[str, float]] = {}

    def _mark(self, call_id: str, key: str) -> None:
        self.timings.setdefault(call_id, {})[key] = time.monotonic()

    def report(self, call_id: str) -> dict:
        t = self.timings.get(call_id, {})
        t0 = t.get("webhook")
        return {k: round(v - t0, 3) for k, v in t.items()} if t0 else {}

    async def _action(self, call_id: str, action: str, sdp: Optional[str] = None) -> dict:
        body: dict[str, Any] = {"messaging_product": "whatsapp", "call_id": call_id, "action": action}
        if sdp:
            body["session"] = {"sdp_type": "answer", "sdp": sdp}
        return await self.post("calls", body)

    async def on_connect(self, call: dict) -> None:
        call_id = call["id"]
        self._mark(call_id, "webhook")
        # Admission hook: real impl = same policy as messages (STOP/blocked -> reject).
        if len(self.sessions) >= self.max_calls:
            log.warning("call %s rejected: at max concurrent calls", call_id)
            await self._action(call_id, "reject")
            return
        offer = call["session"]["sdp"]
        pc = RTCPeerConnection()  # add RTCConfiguration(iceServers=...) for TURN
        player = MediaPlayer(self.audio_file)
        gate = GatedAudioTrack(player)
        connected = asyncio.Event()
        sess = {"pc": pc, "player": player, "gate": gate, "done": asyncio.Event()}
        self.sessions[call_id] = sess

        @pc.on("connectionstatechange")
        async def _state():
            log.info("call %s pc state=%s", call_id, pc.connectionState)
            if pc.connectionState == "connected":
                self._mark(call_id, "dtls_connected")
                connected.set()
            elif pc.connectionState in ("failed", "closed"):
                sess["done"].set()

        @pc.on("track")
        def _on_track(track):
            async def _drain():
                n = 0
                try:
                    while True:
                        await track.recv()
                        n += 1
                        if n == 1:
                            self._mark(call_id, "first_rtp_in")
                except MediaStreamError:
                    pass
            asyncio.ensure_future(_drain())

        try:
            await pc.setRemoteDescription(RTCSessionDescription(sdp=offer, type="offer"))
            pc.addTrack(gate)
            await pc.setLocalDescription(await pc.createAnswer())  # waits for ICE gathering
            answer = pc.localDescription.sdp
            self._mark(call_id, "answer_built")
            await self._action(call_id, "pre_accept", answer)
            self._mark(call_id, "pre_accept_ok")
            try:
                await asyncio.wait_for(connected.wait(), self.connect_timeout)
            except asyncio.TimeoutError:
                log.error("call %s: ICE/DTLS not connected in %.0fs -> terminate", call_id, self.connect_timeout)
                await self._action(call_id, "terminate")
                return
            await self._action(call_id, "accept", answer)  # SAME sdp as pre_accept
            self._mark(call_id, "accept_ok")
            gate.open_gate()  # media only after accept returned 200
            self._mark(call_id, "media_started")
            try:
                await asyncio.wait_for(gate.finished.wait(), self.play_seconds)
            except asyncio.TimeoutError:
                pass
            if gate.first_real_frame_at:
                self.timings[call_id]["first_rtp_out"] = gate.first_real_frame_at
            if not sess["done"].is_set():  # user did not already hang up
                await self._action(call_id, "terminate")
                self._mark(call_id, "terminate_sent")
        except Exception:
            log.exception("call %s failed", call_id)
            try:
                await self._action(call_id, "terminate")
            except Exception:
                pass
        finally:
            await self._cleanup(call_id)
            log.info("call %s timings (s since webhook): %s", call_id, json.dumps(self.report(call_id)))

    async def on_terminate(self, call: dict) -> None:
        call_id = call["id"]
        self._mark(call_id, "terminate_webhook")
        log.info("call %s terminate webhook status=%s duration=%s errors=%s",
                 call_id, call.get("status"), call.get("duration"), call.get("errors"))
        s = self.sessions.get(call_id)
        if s:
            s["done"].set()
            s["gate"].finished.set()

    async def _cleanup(self, call_id: str) -> None:
        s = self.sessions.pop(call_id, None)
        if not s:
            return
        s["gate"].stop()
        try:
            await s["pc"].close()
        except Exception:
            pass
        try:
            if s["player"].audio:
                s["player"].audio.stop()
        except Exception:
            pass

    async def dispatch(self, payload: dict) -> None:
        for entry in payload.get("entry", []):
            for ch in entry.get("changes", []):
                if ch.get("field") != "calls":
                    continue
                for call in ch.get("value", {}).get("calls", []):
                    ev = call.get("event")
                    if ev == "connect" and call.get("direction") == "USER_INITIATED":
                        asyncio.ensure_future(self.on_connect(call))
                    elif ev == "terminate":
                        await self.on_terminate(call)
                    else:
                        log.info("ignored calls event %s dir=%s", ev, call.get("direction"))


def make_graph_post(phone_id: str, token: str, version: str) -> PostFn:
    url = f"https://graph.facebook.com/{version}/{phone_id}/calls"

    async def _post(path: str, body: dict) -> dict:
        t0 = time.monotonic()
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=body, headers={"Authorization": f"Bearer {token}"}) as r:
                data = await r.json(content_type=None)
                log.info("POST %s action=%s -> %s in %.2fs", path, body.get("action"), r.status, time.monotonic() - t0)
                if r.status >= 300:
                    raise RuntimeError(f"graph {r.status}: {data}")
                return data
    return _post


def make_dry_post() -> PostFn:
    async def _post(path: str, body: dict) -> dict:
        shown = json.loads(json.dumps(body))
        if "session" in shown:
            shown["session"]["sdp"] = f"<{len(shown['session']['sdp'])} bytes sdp>"
        log.info("[dry-run] would POST /%s %s", path, json.dumps(shown))
        return {"success": True}
    return _post


def build_app(handler: CallHandler, app_secret: str, verify_token: str) -> "web.Application":
    async def verify(req: web.Request) -> web.Response:
        q = req.query
        if q.get("hub.mode") == "subscribe" and hmac.compare_digest(q.get("hub.verify_token", ""), verify_token):
            return web.Response(text=q.get("hub.challenge", ""))
        return web.Response(status=403)

    async def hook(req: web.Request) -> web.Response:
        raw = await req.read()
        if not verify_signature(app_secret, raw, req.headers.get("X-Hub-Signature-256", "")):
            return web.Response(status=401)
        await handler.dispatch(json.loads(raw))
        return web.Response(text="ok")  # return fast; work continues in background

    app = web.Application()
    app.add_routes([web.get("/webhook", verify), web.post("/webhook", hook)])
    return app


# ---------------------------------------------------------------- selftest
def _make_test_audio(path: str, seconds: int = 3) -> None:
    ff = shutil.which("ffmpeg")
    if not ff:
        sys.exit("selftest needs ffmpeg")
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"sine=frequency=440:duration={seconds}", "-ar", "48000", path], check=True)


async def selftest() -> int:
    need_deps()
    tmp = tempfile.mkdtemp(prefix="wacall-")
    wav = os.path.join(tmp, "tone.wav")
    _make_test_audio(wav, 3)

    caller = RTCPeerConnection()  # plays the role of Meta/WhatsApp
    caller.addTransceiver("audio", direction="recvonly")
    received = {"frames": 0, "at_accept": None}
    sink = MediaBlackhole()

    @caller.on("track")
    def _t(track):
        async def _count():
            while True:
                try:
                    await track.recv()
                except MediaStreamError:
                    return
                received["frames"] += 1
        asyncio.ensure_future(_count())

    await caller.setLocalDescription(await caller.createOffer())
    calls: list[str] = []

    async def fake_graph(path: str, body: dict) -> dict:
        calls.append(body["action"])
        if body["action"] == "pre_accept":
            await caller.setRemoteDescription(RTCSessionDescription(sdp=body["session"]["sdp"], type="answer"))
        if body["action"] == "accept":
            received["at_accept"] = received["frames"]
        return {"success": True}

    h = CallHandler(fake_graph, wav, play_seconds=6, connect_timeout=15)
    payload = {"entry": [{"changes": [{"field": "calls", "value": {"calls": [{
        "id": "selftest-1", "event": "connect", "direction": "USER_INITIATED",
        "session": {"sdp_type": "offer", "sdp": caller.localDescription.sdp}}]}}]}]}
    await h.dispatch(payload)
    for _ in range(300):
        await asyncio.sleep(0.1)
        if "terminate" in calls:
            break
    await asyncio.sleep(0.3)
    await caller.close()
    await sink.stop()

    ok = (calls[:3] == ["pre_accept", "accept", "terminate"]
          and received["frames"] > 20
          and received["at_accept"] is not None)
    # Frames before accept are the gated silence; real audio frames counted on sender side.
    real = None
    print(json.dumps({"calls": calls, "frames_received": received["frames"],
                      "frames_at_accept": received["at_accept"],
                      "timings_s": h.report("selftest-1")}, indent=2))
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true", help="local two-peer test, no Meta")
    ap.add_argument("--dry-run", action="store_true", help="serve but never POST to Graph")
    ap.add_argument("--audio", help="audio file to play (ogg/opus/wav/mp3)")
    ap.add_argument("--seconds", type=float, default=10.0, help="max seconds to play before terminate")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8088)
    ap.add_argument("--connect-timeout", type=float, default=20.0)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    need_deps()
    if a.selftest:
        return asyncio.run(selftest())
    if not a.audio:
        ap.error("--audio is required unless --selftest")
    app_secret = os.environ.get("WA_APP_SECRET", "")
    verify_token = os.environ.get("WA_VERIFY_TOKEN", "")
    if not app_secret or not verify_token:
        sys.exit("set WA_APP_SECRET and WA_VERIFY_TOKEN")
    if a.dry_run:
        post = make_dry_post()
    else:
        pid, tok = os.environ.get("WA_PHONE_ID"), os.environ.get("WA_TOKEN")
        if not pid or not tok:
            sys.exit("set WA_PHONE_ID and WA_TOKEN (or use --dry-run)")
        post = make_graph_post(pid, tok, os.environ.get("WA_GRAPH_VERSION", "v21.0"))
    handler = CallHandler(post, a.audio, a.seconds, a.connect_timeout)
    web.run_app(build_app(handler, app_secret, verify_token), host=a.host, port=a.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
