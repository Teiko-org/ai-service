from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.cache import cache
from app.core import confirm_tokens, write_throttle
from app.core.request_context import current_history, current_session_id
from app.tools.writes import fornada as fornada_tool


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    confirm_tokens._consumed_store = confirm_tokens._ConsumedTokensStore()
    confirm_tokens._issued_metadata_store = confirm_tokens._IssuedMetadataStore()
    write_throttle.write_throttle = write_throttle.WriteThrottle()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _mock_active_batch(monkeypatch):
    monkeypatch.setattr(
        "app.tools.writes.fornada.find_active_batch",
        AsyncMock(return_value=None),
    )


@pytest.fixture
def request_ctx():
    history = [{"role": "user", "content": "criar"}]
    sess_tok = current_session_id.set("s1")
    hist_tok = current_history.set(history)
    try:
        yield history
    finally:
        current_history.reset(hist_tok)
        current_session_id.reset(sess_tok)


def _client_ok():
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 201
    resp.json = MagicMock(return_value={"id": 9})
    resp.text = ""
    client.post = AsyncMock(return_value=resp)
    return client


@pytest.mark.asyncio
async def test_duplicate_commit_returns_cached_result(monkeypatch, request_ctx):
    from app.config import settings

    monkeypatch.setattr(settings, "confirm_token_secret", "test-secret")

    client = _client_ok()
    from datetime import date, timedelta

    di = (date.today() + timedelta(days=2)).isoformat()
    df = (date.today() + timedelta(days=9)).isoformat()
    preview = await fornada_tool.execute(
        "create_batch", {"data_inicio": di, "data_fim": df}, "http://x", "tok", client
    )
    token = preview["confirm_token"]
    request_ctx.append({"role": "user", "content": "sim"})
    args = {
        "data_inicio": di,
        "data_fim": df,
        "confirmed": True,
        "confirm_token": token,
    }
    first = await fornada_tool.execute(
        "create_batch", args, "http://x", "tok", client
    )
    assert first["ok"] is True
    assert client.post.await_count == 1

    second = await fornada_tool.execute(
        "create_batch", args, "http://x", "tok", client
    )
    assert second == first
    assert client.post.await_count == 1
