# wa_call_spike

Spike for WhatsApp Calling (see docs/WHATSAPP_CALLING_DESIGN.md).

    python3 -m venv v && v/bin/pip install aiortc aiohttp   # needs ffmpeg
    v/bin/python tools/wa_call_spike.py --selftest           # local, no Meta
    WA_APP_SECRET=.. WA_VERIFY_TOKEN=.. v/bin/python tools/wa_call_spike.py --audio hello.ogg --dry-run
    WA_PHONE_ID=.. WA_TOKEN=.. WA_APP_SECRET=.. WA_VERIFY_TOKEN=.. WA_GRAPH_VERSION=<current> \
      v/bin/python tools/wa_call_spike.py --audio hello.ogg --seconds 10   # LIVE: owner go-ahead only

Webhook path: /webhook on --port (default 8088); expose with a public HTTPS URL and open a UDP range for ICE.
