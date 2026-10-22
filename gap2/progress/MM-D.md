# MM-D: send generated media / Huf Files over WhatsApp

- Tests first: huf/ai/tests/test_whatsapp_send_file.py (34 tests, ffmpeg helper tests skip when ffmpeg is absent).
- Implementation: huf/ai/whatsapp_send_file.py (ownership resolve, limits, ffmpeg ogg/opus, caption split),
  wired in huf/ai/tools/whatsapp.py `_send_media_file`; schema in tools/_registry.py.
- Left for voice-note-out gateway auto-reply: gateway_service reply path still sends text only; it can call
  `whatsapp_send_file.to_ogg_opus` + `bridge.upload_media(..., voice=True)` + `bridge.send_media(voice=True)`.
