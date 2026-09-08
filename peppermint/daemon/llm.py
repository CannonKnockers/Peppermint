"""The connection to Ollama."""

from __future__ import annotations

import logging
import asyncio
from contextlib import suppress

import ollama

from peppermint import config

log = logging.getLogger("peppermint.llm")


class LLMError(Exception):
    """The model server did not answer."""


class LLM:
    def __init__(self, model: str | None = None, host: str | None = None):
        self.model = model or config.MODEL
        self.host = host or config.OLLAMA_HOST
        self.client = ollama.Client(host=self.host, timeout=config.MODEL_TIMEOUT_S)
        self._checked = False

    def available_models(self) -> list[str]:
        try:
            data = self.client.list()
        except Exception as exc:
            raise LLMError(f"Ollama does not answer at {self.host}: {exc}") from exc
        names = []
        for item in getattr(data, "models", data.get("models", []) if isinstance(data, dict) else []):
            name = getattr(item, "model", None) or (item.get("model") if isinstance(item, dict) else None)
            if name:
                names.append(name)
        return names

    def ensure_model(self) -> str:
        """Pick the configured model, or the fallback if it is missing."""
        if self._checked:
            return self.model
        names = self.available_models()
        if self.model in names:
            self._checked = True
            return self.model
        stem = self.model.split(":")[0]
        for name in names:
            if name.split(":")[0] == stem:
                log.warning("Model %s is missing. Peppermint uses %s.", self.model, name)
                self.model = name
                self._checked = True
                return self.model
        if config.FALLBACK_MODEL in names:
            log.warning("Model %s is missing. Peppermint uses the fallback %s.", self.model, config.FALLBACK_MODEL)
            self.model = config.FALLBACK_MODEL
            self._checked = True
            return self.model
        raise LLMError(
            f"The model `{self.model}` is not on this computer. "
            f"Run: ollama pull {self.model}. Models found: {names or 'none'}."
        )

    def chat(self, messages: list[dict], tools: list[dict] | None = None, cancelled=None):
        """Send one turn. Returns the message object from Ollama."""
        self.ensure_model()
        if cancelled is not None:
            return asyncio.run(self._chat_cancellable(messages, tools, cancelled))
        try:
            reply = self.client.chat(
                model=self.model,
                messages=messages,
                tools=tools or None,
                think=config.THINK,
                keep_alive=config.KEEP_ALIVE,
                options=config.LLM_OPTIONS,
            )
        except Exception as exc:
            raise LLMError(f"The model call failed: {exc}") from exc
        return reply.message


    async def _chat_cancellable(self, messages, tools, cancelled):
        """Release the worker and HTTP request when the user presses Stop."""
        async with ollama.AsyncClient(host=self.host, timeout=config.MODEL_TIMEOUT_S) as client:
            request = asyncio.create_task(client.chat(
                model=self.model, messages=messages, tools=tools or None,
                think=config.THINK, keep_alive=config.KEEP_ALIVE, options=config.LLM_OPTIONS,
            ))
            try:
                while not request.done():
                    if cancelled():
                        raise LLMError('Generation stopped by the user.')
                    await asyncio.wait({request}, timeout=0.1)
                if cancelled():
                    raise LLMError('Generation stopped by the user.')
                reply = await request
                return reply.message
            except Exception as exc:
                if isinstance(exc, LLMError):
                    raise
                raise LLMError(f'The model call failed: {exc}') from exc
            finally:
                if not request.done():
                    request.cancel()
                    with suppress(asyncio.CancelledError):
                        await request
