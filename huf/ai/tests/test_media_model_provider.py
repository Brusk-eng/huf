# Copyright (c) 2026, Tridz Technologies Pvt Ltd and Contributors
# See license.txt

"""
MM-F: image generation and TTS must use the *model's own* AI Provider
(API key + LiteLLM name normalisation), not the agent's chat provider.

No network: litellm is mocked.

Run with:
    bench --site <site> run-tests --app huf --module huf.ai.tests.test_media_model_provider
"""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase


class _LazyModule:
	def __init__(self, module_path):
		self._module_path = module_path

	def __getattr__(self, name):
		import importlib

		return getattr(importlib.import_module(self._module_path), name)


media = _LazyModule("huf.ai.handlers.media")


class _Doc:
	def __init__(self, **kw):
		self.__dict__.update(kw)

	def get(self, field, default=None):
		return self.__dict__.get(field, default)


def _provider(name, key):
	doc = MagicMock()
	doc.provider_name = name
	doc.get_password.return_value = key
	return doc


def _router(agent, models=None, providers=None):
	models = models or {}
	providers = providers or {}

	def _get_doc(doctype, name=None, *a, **k):
		if doctype == "Agent":
			return agent
		if doctype == "AI Model":
			return models[name]
		if doctype == "AI Provider":
			return providers[name]
		return MagicMock()

	return _get_doc


def _agent(**kw):
	base = dict(
		name="WA Agent",
		agent_name="WA Agent",
		provider="Anthropic",
		image_generation_model=None,
		tts_model=None,
		tts_voice=None,
	)
	base.update(kw)
	return _Doc(**base)


PROVIDERS = {
	"Anthropic": _provider("Anthropic", "key-anthropic"),
	"Google": _provider("Google", "key-google"),
	"OpenAI": _provider("OpenAI", "key-openai"),
}


class TestImageModelProvider(IntegrationTestCase):
	async def _call(self, agent, models, providers=None):
		fake = MagicMock()
		fake.image_generation.return_value = MagicMock(data=[])
		with (
			patch("frappe.get_doc", side_effect=_router(agent, models, providers or PROVIDERS)),
			patch("huf.ai.agent_access.assert_agent_access"),
			patch.dict("sys.modules", {"litellm": fake}),
		):
			result = await media.handle_generate_image(prompt="a cat", agent_name="WA Agent")
		return fake, result

	async def test_cross_provider_image_model_uses_its_provider_key(self):
		agent = _agent(image_generation_model="gemini-img")
		models = {"gemini-img": _Doc(model_name="gemini-2.5-flash-image", provider="Google")}
		fake, _ = await self._call(agent, models)
		kwargs = fake.image_generation.call_args.kwargs
		self.assertEqual(kwargs["api_key"], "key-google")
		self.assertTrue(kwargs["model"].startswith("gemini/"), kwargs["model"])

	async def test_fallback_to_agent_provider_when_unset(self):
		agent = _agent(provider="OpenAI")
		fake, _ = await self._call(agent, {})
		kwargs = fake.image_generation.call_args.kwargs
		self.assertEqual(kwargs["api_key"], "key-openai")

	async def test_missing_key_on_image_model_provider_names_provider(self):
		providers = dict(PROVIDERS, Google=_provider("Google", None))
		agent = _agent(image_generation_model="gemini-img")
		models = {"gemini-img": _Doc(model_name="gemini-2.5-flash-image", provider="Google")}
		fake, result = await self._call(agent, models, providers)
		self.assertFalse(result["success"])
		self.assertIn("Google", result["error"])
		self.assertIn("API key", result["error"])
		fake.image_generation.assert_not_called()


class TestTtsModelProvider(IntegrationTestCase):
	def _resolve(self, agent, models, providers=None):
		with patch("frappe.get_doc", side_effect=_router(agent, models, providers or PROVIDERS)):
			return media._resolve_tts_config(agent)

	def test_cross_provider_tts_model_uses_its_provider_key(self):
		agent = _agent(tts_model="oa-tts")
		models = {"oa-tts": _Doc(model_name="tts-1", provider="OpenAI")}
		cfg = self._resolve(agent, models)
		self.assertEqual(cfg["api_key"], "key-openai")
		self.assertEqual(cfg["provider_name"], "openai")
		self.assertEqual(cfg["source"], "agent_config")

	def test_fallback_to_agent_provider_when_unset(self):
		agent = _agent(provider="OpenAI")
		cfg = self._resolve(agent, {})
		self.assertEqual(cfg["api_key"], "key-openai")
		self.assertEqual(cfg["source"], "provider_default")

	def test_missing_key_on_tts_provider_names_provider(self):
		providers = dict(PROVIDERS, OpenAI=_provider("OpenAI", None))
		agent = _agent(tts_model="oa-tts")
		models = {"oa-tts": _Doc(model_name="tts-1", provider="OpenAI")}
		with self.assertRaises(ValueError) as cm:
			self._resolve(agent, models, providers)
		self.assertIn("OpenAI", str(cm.exception))
