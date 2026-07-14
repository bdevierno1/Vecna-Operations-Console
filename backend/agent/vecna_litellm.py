"""
Strands LiteLLMModel calls: acompletion(**client_args, **litellm_request).
The formatted request is merged second, so it can overwrite api_key (e.g. via model params).

We merge so client_args always win — fixes OpenRouter auth falling through to OpenAI without a key.

We also wrap litellm.main.acompletion so any call for an openrouter/ model gets api_key and
base URL if they were dropped (some LiteLLM / OpenAI-client paths ignore kwargs).
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any

import litellm.main as litellm_main
from strands.models.litellm import LiteLLMModel
from strands.types.streaming import StreamEvent

_orig_acompletion = litellm_main.acompletion
_orig_completion = litellm_main.completion


def _openrouter_secret() -> str:
    return (os.environ.get("OPENROUTER_API_KEY") or "").strip() or (
        os.environ.get("OPENAI_API_KEY") or ""
    ).strip()


def _inject_openrouter_litellm_globals(key: str) -> None:
    import litellm as L

    L.openrouter_key = key
    L.api_key = key
    if not (os.environ.get("OPENAI_API_KEY") or "").strip():
        os.environ["OPENAI_API_KEY"] = key


def _should_inject_openrouter(kwargs: dict[str, Any], args: tuple[Any, ...]) -> bool:
    if kwargs.get("custom_llm_provider") == "openrouter":
        return True
    model = kwargs.get("model")
    if model is None and args:
        model = args[0]
    return isinstance(model, str) and model.startswith("openrouter/")


def _vecna_completion(*args: Any, **kwargs: Any):
    """acompletion() delegates to sync completion() in an executor; patch both."""
    if _should_inject_openrouter(kwargs, args):
        key = _openrouter_secret()
        if key:
            if not kwargs.get("api_key"):
                kwargs["api_key"] = key
            _inject_openrouter_litellm_globals(key)
        base = os.environ.get("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1")
        kwargs.setdefault("api_base", base)
        kwargs.setdefault("base_url", base)
        kwargs.setdefault("custom_llm_provider", "openrouter")
    return _orig_completion(*args, **kwargs)


async def _vecna_acompletion(*args: Any, **kwargs: Any):
    if _should_inject_openrouter(kwargs, args):
        key = _openrouter_secret()
        if key:
            if not kwargs.get("api_key"):
                kwargs["api_key"] = key
            _inject_openrouter_litellm_globals(key)
        base = os.environ.get("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1")
        kwargs.setdefault("api_base", base)
        kwargs.setdefault("base_url", base)
        kwargs.setdefault("custom_llm_provider", "openrouter")
    return await _orig_acompletion(*args, **kwargs)


litellm_main.completion = _vecna_completion
litellm_main.acompletion = _vecna_acompletion
import litellm as _litellm_pkg  # noqa: E402  (intentional: must follow litellm_main patching)

_litellm_pkg.completion = _vecna_completion
_litellm_pkg.acompletion = _vecna_acompletion


class VecnaLiteLLMModel(LiteLLMModel):
    async def _handle_streaming_response(
        self, litellm_request: dict[str, Any]
    ) -> AsyncGenerator[StreamEvent, None]:
        merged = {**litellm_request, **self.client_args}
        saved = self.client_args
        self.client_args = {}
        try:
            async for ev in LiteLLMModel._handle_streaming_response(self, merged):
                yield ev
        finally:
            self.client_args = saved

    async def _handle_non_streaming_response(
        self, litellm_request: dict[str, Any]
    ) -> AsyncGenerator[StreamEvent, None]:
        merged = {**litellm_request, **self.client_args}
        saved = self.client_args
        self.client_args = {}
        try:
            async for ev in LiteLLMModel._handle_non_streaming_response(self, merged):
                yield ev
        finally:
            self.client_args = saved
