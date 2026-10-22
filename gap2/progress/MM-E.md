# MM-E: voice note out for voice note in

- Tests first: huf/ai/tests/test_whatsapp_voice_reply.py.
- Helper: huf/ai/whatsapp_voice_reply.py (TTS via handle_generate_audio, to_ogg_opus, adapter upload_media/send_media voice=True, text fallback on any failure).
- Gateway field `voice_reply_mode` (Off / Voice only / Text and voice) with validation; gateway_service._send_whatsapp_reply calls the helper.
- Runbook section 8 added.
