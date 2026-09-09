"""The connection to Ollama."""

from __future__ import annotations

import logging
import asyncio
from collections.abc import Mapping
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
        self.last_response_metadata: dict = {}

    @staticmethod
    def _prepare_messages(messages: list[dict]) -> list[dict]:
        """Translate saved tool results to Ollama's wire format without edits."""
        prepared = []
        for message in messages:
            outgoing = dict(message)
            if outgoing.get('role') == 'tool':
                legacy_name = outgoing.pop('name', None)
                if not outgoing.get('tool_name') and legacy_name:
                    outgoing['tool_name'] = legacy_name
            outgoing.pop('internal', None)
            outgoing.pop('model_visible', None)
            prepared.append(outgoing)
        return prepared

    def _record_response_metadata(self, reply) -> None:
        """Keep generation diagnostics, never response text or tool arguments."""
        def field(value, name):
            return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)

        metadata = {}
        done = field(reply, 'done')
        reason = field(reply, 'done_reason')
        if isinstance(done, bool):
            metadata['done'] = done
        if isinstance(reason, str):
            metadata['done_reason'] = reason
        for name in ('total_duration', 'load_duration', 'prompt_eval_count',
                     'prompt_eval_cached_count', 'prompt_eval_duration',
                     'eval_count', 'eval_duration'):
            value = field(reply, name)
            if type(value) is int and value >= 0:
                metadata[name] = value
        message = field(reply, 'message')
        for name in ('thinking', 'content'):
            value = field(message, name)
            if isinstance(value, str):
                metadata[name + '_chars'] = len(value)
        calls = field(message, 'tool_calls')
        if isinstance(calls, (list, tuple)):
            metadata['tool_call_count'] = len(calls)
        self.last_response_metadata = metadata

    def available_models(self) -> list[str]:
        try:
            data = self.client.list()
        except Exception as exc:
            raise LLMError(f"Ollama does not answer at {self.host}: {exc}") from exc

        if isinstance(data, list):
            models_list = data
        elif isinstance(data, dict):
            models_list = data.get("models", [])
        else:
            models_list = getattr(data, "models", [])

        names = []
        for item in models_list:
            if isinstance(item, dict):
                name = item.get("model")
            else:
                name = getattr(item, "model", None)
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

    def chat(self, messages: list[dict], tools: list[dict] | None = None, cancelled=None,
             *, response_format: dict | None = None):
        """Send one turn. Returns the message object from Ollama."""
        self.last_response_metadata = {}
        if response_format is not None and tools:
            raise LLMError('Structured dialogue requests cannot include executable tools.')
        self.ensure_model()
        if cancelled is not None:
            return asyncio.run(self._chat_cancellable(messages, tools, cancelled, response_format))
        try:
            reply = self.client.chat(
                model=self.model,
                messages=self._prepare_messages(messages),
                tools=tools or None,
                think=False if response_format is not None else config.THINK,
                keep_alive=config.KEEP_ALIVE,
                options=config.LLM_OPTIONS,
                **({'format': response_format} if response_format is not None else {}),
            )
        except Exception as exc:
            raise LLMError(f"The model call failed: {exc}") from exc
        self._record_response_metadata(reply)
        return reply.message


    async def _chat_cancellable(self, messages, tools, cancelled, response_format=None):
        """Release the worker and HTTP request when the user presses Stop."""
        self.last_response_metadata = {}
        async with ollama.AsyncClient(host=self.host, timeout=config.MODEL_TIMEOUT_S) as client:
            request = asyncio.create_task(client.chat(
                model=self.model, messages=self._prepare_messages(messages), tools=tools or None,
                think=False if response_format is not None else config.THINK,
                keep_alive=config.KEEP_ALIVE, options=config.LLM_OPTIONS,
                **({'format': response_format} if response_format is not None else {}),
            ))
            try:
                while not request.done():
                    if cancelled():
                        raise LLMError('Generation stopped by the user.')
                    await asyncio.wait({request}, timeout=0.1)
                if cancelled():
                    raise LLMError('Generation stopped by the user.')
                reply = await request
                self._record_response_metadata(reply)
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
