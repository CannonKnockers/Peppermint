import asyncio
import time
import pytest
from peppermint.daemon.llm import LLM, LLMError


def test_cancellation_closes_model_request_and_client(monkeypatch):
    events=[]
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): events.append('closed')
        async def chat(self, **kwargs):
            try:
                await asyncio.sleep(30)
            finally:
                events.append('request_stopped')
    monkeypatch.setattr('peppermint.daemon.llm.ollama.AsyncClient',Client)
    model=LLM()
    model.last_response_metadata = {'done_reason': 'stop', 'eval_count': 123}
    started=time.monotonic()
    with pytest.raises(LLMError,match='stopped'):
        asyncio.run(model._chat_cancellable([],[],lambda: time.monotonic()-started > .01))
    assert time.monotonic()-started < 1
    assert events == ['request_stopped','closed']
    assert model.last_response_metadata == {}
