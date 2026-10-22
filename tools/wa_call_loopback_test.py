#!/usr/bin/env python3
"""No-Meta loopback proof that the aiortc answer path moves audio.

Peer A plays "Meta" (offerer, receives). Peer B is Huf's real AiortcMediaSession driven by run_call
with a recording fake Graph. Greeting clip in -> RTP/Opus frames out -> decoded on A.
Needs aiortc + ffmpeg (ffmpeg only to make the Opus test clip). Run:  python tools/wa_call_loopback_test.py
"""
from __future__ import annotations

import array
import asyncio
import importlib.util
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("flow", ROOT / "huf/ai/gateway_adapters/whatsapp_call_flow.py")
flow = importlib.util.module_from_spec(spec)
sys.modules["flow"] = flow
spec.loader.exec_module(flow)


class FakeGraph:
	def __init__(self):
		self.calls = []

	def pre_accept(self, cid, sdp):
		self.calls.append("pre_accept")

	def accept(self, cid, sdp):
		self.calls.append("accept")

	def terminate(self, cid, *a):
		self.calls.append("terminate")

	def reject(self, cid):
		self.calls.append("reject")


async def main() -> int:
	from aiortc import RTCPeerConnection, RTCSessionDescription

	clip = pathlib.Path(tempfile.mkdtemp()) / "greeting.ogg"
	subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
		"-c:a", "libopus", str(clip)], check=True)
	meta = RTCPeerConnection()
	meta.addTransceiver("audio", direction="sendrecv")
	got = {"frames": 0, "loud": 0}

	@meta.on("track")
	def _t(track):
		async def rd():
			try:
				while True:
					f = await track.recv()
					got["frames"] += 1
					if max(map(abs, array.array('h', bytes(f.planes[0])[:400]))) > 500:
						got["loud"] += 1
			except Exception as e:
				got['err'] = repr(e)
		asyncio.ensure_future(rd())

	await meta.setLocalDescription(await meta.createOffer())
	graph = FakeGraph()
	media = flow.AiortcMediaSession(flow.IceConfig(stun_urls=()))  # no STUN: loopback only
	answer_box = {}

	class G(FakeGraph):
		def pre_accept(self, cid, sdp):
			answer_box["sdp"] = sdp
			super().pre_accept(cid, sdp)

	graph = G()
	task = asyncio.ensure_future(flow.run_call("loop-1", meta.localDescription.sdp, graph=graph, media=media,
		source=flow.FilePlayback([flow.AudioClip("greeting", path=str(clip))]), sink=flow.InMemoryAudioSink(),
		max_seconds=15, connect_timeout=10))
	while "sdp" not in answer_box and not task.done():
		await asyncio.sleep(0.05)
	if "sdp" in answer_box:
		await meta.setRemoteDescription(RTCSessionDescription(sdp=answer_box["sdp"], type="answer"))
	trace = await task
	await asyncio.sleep(0.3)
	await meta.close()
	print("recv end:", got.get("err")); print("dur", round(trace.duration_s,2), trace.error); print("actions:", graph.calls, "state:", trace.state, "played:", trace.played)
	print("frames received by peer A:", got["frames"], "non-silent:", got["loud"])
	ok = trace.state == flow.COMPLETED and graph.calls == ["pre_accept", "accept", "terminate"] and got["loud"] > 20
	print("LOOPBACK", "PASS" if ok else "FAIL")
	return 0 if ok else 1


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
