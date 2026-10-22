# MM-BC progress
- Tests first: huf/ai/tests/test_gateway_media_to_agent.py
- Impl: huf/ai/media_prep.py (shared), gateway_service.py (files= + single reply), agent_chat.py uses shared classify/format/cap
- Defaults: 12k chars/doc, 24k chars total docs (~6k tokens), 8k transcript cap
