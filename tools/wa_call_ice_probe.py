#!/usr/bin/env python3
"""No-Meta ICE/NAT probe for the WhatsApp calling media path. Sends only STUN (and, with --turn-test,
one TURN Allocate). Nothing is sent to Meta.

  python tools/wa_call_ice_probe.py                 # STUN mapping + outbound UDP + aioice gather
  python tools/wa_call_ice_probe.py --turn-test     # also try a TURN allocation over TCP/TLS 443
                                                    # (public Open Relay creds, or --turn-url/--turn-user/--turn-pass)
Reading the result:
  endpoint-independent mapping (same srflx port from both servers)  -> direct UDP via ICE should work
  symmetric / different ports                                       -> use TURN relay (TCP/TLS) fallback
  no STUN reply                                                     -> outbound UDP blocked; TURN over TCP/443 only
Needs: pip install aiortc (provides aioice).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import socket
import struct

STUN_SERVERS = [("stun.l.google.com", 19302), ("stun.cloudflare.com", 3478)]
MAGIC = 0x2112A442
# Open Relay Project's published public credentials (free tier, rate limited). Override via flags/env.
OPENRELAY = ("openrelay.metered.ca", 443, "openrelayproject", "openrelayproject")


def stun_binding(sock: socket.socket, server: tuple[str, int], timeout: float = 3.0):
	addr = socket.getaddrinfo(server[0], server[1], socket.AF_INET, socket.SOCK_DGRAM)[0][4]
	tid = os.urandom(12)
	sock.settimeout(timeout)
	sock.sendto(struct.pack("!HHI", 0x0001, 0, MAGIC) + tid, addr)
	try:
		data, _ = sock.recvfrom(2048)
	except socket.timeout:
		return None
	pos = 20
	while pos + 4 <= len(data):
		t, ln = struct.unpack("!HH", data[pos:pos + 4])
		v = data[pos + 4:pos + 4 + ln]
		if t in (0x0020, 0x0001) and ln >= 8 and v[1] == 1:
			port, ip = struct.unpack("!H4s", v[2:8])
			if t == 0x0020:
				port ^= MAGIC >> 16
				ip = bytes(a ^ b for a, b in zip(ip, struct.pack("!I", MAGIC)))
			return socket.inet_ntoa(ip), port
		pos += 4 + ln + (-ln % 4)
	return None


def local_ip() -> str:
	s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
	try:
		s.connect(("8.8.8.8", 80))
		return s.getsockname()[0]
	finally:
		s.close()


def probe_nat() -> str:
	sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
	sock.bind((local_ip(), 0))  # the interface that routes to the STUN servers, not every interface
	lport = sock.getsockname()[1]
	print(f"local {local_ip()}:{lport}")
	maps = []
	for srv in STUN_SERVERS:
		m = stun_binding(sock, srv)
		print(f"  {srv[0]}:{srv[1]} -> {('%s:%d' % m) if m else 'NO REPLY (outbound UDP or reply blocked)'}")
		maps.append(m)
	sock.close()
	ok = [m for m in maps if m]
	if not ok:
		verdict = "NO_UDP"
	elif len(ok) < 2:
		verdict = "PARTIAL (one server answered; cannot classify NAT)"
	elif ok[0] == ok[1]:
		verdict = "ENDPOINT_INDEPENDENT (cone-like; direct UDP ICE should work)" + (
			"; srflx port == local port (port-preserving or no NAT)" if ok[0][1] == lport else "")
	else:
		verdict = (
			"ADDRESS/PORT-DEPENDENT (symmetric-like: mapped port differs per destination). Fine against an ICE-lite "
			"peer with a public candidate (our outbound checks create the mapping and replies come back to it); "
			"would fail peer-to-peer hole punching, then use TURN"
		)
	print("outbound UDP + reply:", "OK" if ok else "FAILED")
	print("NAT mapping:", verdict)
	return verdict


async def gather(stun=None, turn=None, relay_only=False):
	from aioice import Connection, TransportPolicy

	kw = {}
	if stun:
		kw["stun_server"] = stun
	if turn:
		host, port, user, pw, transport, ssl = turn
		kw.update(turn_server=(host, port), turn_username=user, turn_password=pw, turn_transport=transport, turn_ssl=ssl)
	conn = Connection(ice_controlling=True, transport_policy=TransportPolicy.RELAY if relay_only else TransportPolicy.ALL, **kw)
	try:
		await asyncio.wait_for(conn.gather_candidates(), 15)
		return [(c.type, c.transport, f"{c.host}:{c.port}") for c in conn.local_candidates]
	finally:
		await conn.close()


async def main(args) -> None:
	print("== STUN / NAT mapping ==")
	probe_nat()
	print("== aioice candidate gathering (STUN only) ==")
	for t, tr, a in await gather(stun=STUN_SERVERS[0]):
		print(f"  {t:6} {tr} {a}")
	if args.turn_test:
		host, port, user, pw = OPENRELAY
		if args.turn_url:
			host, port = args.turn_url.rsplit(":", 1)[0], int(args.turn_url.rsplit(":", 1)[1])
			user, pw = args.turn_user or os.environ.get("TURN_USER", ""), args.turn_pass or os.environ.get("TURN_PASS", "")
		print(f"== TURN allocate to {host}:{port} (credential not printed) ==")
		try:
			socket.create_connection((host, port), 5).close()
			print("  tcp connect: OK")
		except OSError as exc:
			print(f"  tcp connect: FAILED ({exc.__class__.__name__}); server unreachable from here")
		for label, transport, ssl in (("turn+tcp", "tcp", False), ("turns (TLS)", "tcp", True)):
			try:
				cands = await gather(turn=(host, port, user, pw, transport, ssl), relay_only=True)
				relay = [c for c in cands if c[0] == "relay"]
				print(f"  {label}: {'ALLOCATED ' + relay[0][2] if relay else 'no relay candidate'}")
			except Exception as exc:  # noqa: BLE001
				print(f"  {label}: FAILED {type(exc).__name__}: {str(exc)[:120]}")


if __name__ == "__main__":
	ap = argparse.ArgumentParser()
	ap.add_argument("--turn-test", action="store_true", help="try a TURN allocation (default: Open Relay public creds)")
	ap.add_argument("--turn-url", help="host:port of your TURN server (creds via --turn-user/--turn-pass or TURN_USER/TURN_PASS)")
	ap.add_argument("--turn-user")
	ap.add_argument("--turn-pass")
	asyncio.run(main(ap.parse_args()))
