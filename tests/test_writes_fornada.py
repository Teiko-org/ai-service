from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings
from app.core import confirm_tokens, write_throttle
from app.core.cache import cache
from app.core.request_context import current_history, current_session_id
from app.tools.writes import fornada as fornada_tool


SESSION_ID = "sess-test-1"


@pytest.fixture(autouse=True)
def _set_secret(monkeypatch):
    monkeypatch.setattr(settings, "confirm_token_secret", "test-secret-only-for-tests")
    monkeypatch.setattr(settings, "confirm_token_ttl_seconds", 120)
    yield


@pytest.fixture(autouse=True)
def _reset_stores():
    cache.clear()
    confirm_tokens._consumed_store = confirm_tokens._ConsumedTokensStore()
    confirm_tokens._issued_metadata_store = confirm_tokens._IssuedMetadataStore()
    write_throttle.write_throttle = write_throttle.WriteThrottle()
    yield
    cache.clear()


@pytest.fixture
def request_ctx():
    history = [{"role": "user", "content": "vamos criar uma fornada"}]
    sess_tok = current_session_id.set(SESSION_ID)
    hist_tok = current_history.set(history)
    try:
        yield history
    finally:
        current_history.reset(hist_tok)
        current_session_id.reset(sess_tok)


def _http_client_returning(status: int = 201, json_body: dict | None = None):
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = status
    resp.json = MagicMock(return_value=json_body or {})
    resp.text = ""
    client.post = AsyncMock(return_value=resp)
    return client


def _iso(d: date) -> str:
    return d.isoformat()


def _future_dates(start_offset: int = 1, span: int = 6) -> tuple[str, str]:
    today = date.today()
    return _iso(today + timedelta(days=start_offset)), _iso(
        today + timedelta(days=start_offset + span)
    )


# create_batch ---------------------------------------------------------


@pytest.mark.asyncio
async def test_create_batch_preview_returns_token(request_ctx):
    di, df = _future_dates()
    client = _http_client_returning()

    result = await fornada_tool.execute(
        "create_batch",
        {"data_inicio": di, "data_fim": df},
        "http://x",
        "tok",
        client,
    )

    assert result["requires_confirmation"] is True
    assert result["action"] == "create_batch"
    assert "confirm_token" in result and "." in result["confirm_token"]
    assert result["payload"] == {"data_inicio": di, "data_fim": df}
    client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_batch_commit_calls_backend(request_ctx):
    di, df = _future_dates()
    client = _http_client_returning(201, {"id": 7, "dataInicio": di, "dataFim": df})

    preview = await fornada_tool.execute(
        "create_batch", {"data_inicio": di, "data_fim": df}, "http://x", "tok", client
    )

    request_ctx.append({"role": "assistant", "content": preview["message"]})
    request_ctx.append({"role": "user", "content": "sim, confirma"})

    result = await fornada_tool.execute(
        "create_batch",
        {
            "data_inicio": di,
            "data_fim": df,
            "confirmed": True,
            "confirm_token": preview["confirm_token"],
        },
        "http://x",
        "tok",
        client,
    )

    assert result["ok"] is True
    assert result["data"]["id"] == 7
    client.post.assert_awaited_once()
    args, kwargs = client.post.call_args
    assert args[0].endswith("/fornadas")
    assert kwargs["json"] == {"dataInicio": di, "dataFim": df}
    assert kwargs["headers"]["Authorization"] == "Bearer tok"


@pytest.mark.asyncio
async def test_create_batch_commit_without_token_errors(request_ctx):
    di, df = _future_dates()
    client = _http_client_returning()

    result = await fornada_tool.execute(
        "create_batch",
        {"data_inicio": di, "data_fim": df, "confirmed": True},
        "http://x",
        "tok",
        client,
    )

    assert "error" in result
    assert "token" in result["error"].lower()
    client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_batch_commit_without_auth_errors(request_ctx):
    di, df = _future_dates()
    client = _http_client_returning()
    preview = await fornada_tool.execute(
        "create_batch", {"data_inicio": di, "data_fim": df}, "http://x", "tok", client
    )

    request_ctx.append({"role": "user", "content": "sim"})

    result = await fornada_tool.execute(
        "create_batch",
        {
            "data_inicio": di,
            "data_fim": df,
            "confirmed": True,
            "confirm_token": preview["confirm_token"],
        },
        "http://x",
        None,
        client,
    )

    assert "error" in result
    assert "autentic" in result["error"].lower()
    client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_batch_commit_same_turn_refused(request_ctx):
    di, df = _future_dates()
    client = _http_client_returning()
    preview = await fornada_tool.execute(
        "create_batch", {"data_inicio": di, "data_fim": df}, "http://x", "tok", client
    )

    # No new user message between preview and commit.
    result = await fornada_tool.execute(
        "create_batch",
        {
            "data_inicio": di,
            "data_fim": df,
            "confirmed": True,
            "confirm_token": preview["confirm_token"],
        },
        "http://x",
        "tok",
        client,
    )

    assert "error" in result
    assert "explicita" in result["error"].lower()
    client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_batch_replay_returns_idempotent_ok(request_ctx):
    di, df = _future_dates()
    client = _http_client_returning(201, {"id": 7})
    preview = await fornada_tool.execute(
        "create_batch", {"data_inicio": di, "data_fim": df}, "http://x", "tok", client
    )
    request_ctx.append({"role": "user", "content": "sim"})
    args = {
        "data_inicio": di,
        "data_fim": df,
        "confirmed": True,
        "confirm_token": preview["confirm_token"],
    }
    first = await fornada_tool.execute("create_batch", args, "http://x", "tok", client)

    request_ctx.append({"role": "user", "content": "manda de novo"})
    second = await fornada_tool.execute(
        "create_batch", args, "http://x", "tok", client
    )

    assert first["ok"] is True
    assert second == first
    assert client.post.await_count == 1


@pytest.mark.asyncio
async def test_create_batch_args_tampered_refused(request_ctx):
    di, df = _future_dates()
    client = _http_client_returning()
    preview = await fornada_tool.execute(
        "create_batch", {"data_inicio": di, "data_fim": df}, "http://x", "tok", client
    )
    request_ctx.append({"role": "user", "content": "sim"})

    other_di, other_df = _future_dates(start_offset=30, span=2)
    result = await fornada_tool.execute(
        "create_batch",
        {
            "data_inicio": other_di,
            "data_fim": other_df,
            "confirmed": True,
            "confirm_token": preview["confirm_token"],
        },
        "http://x",
        "tok",
        client,
    )

    assert "error" in result
    assert "batem" in result["error"].lower()
    client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_batch_validation_rejects_past_date(request_ctx):
    past = _iso(date.today() - timedelta(days=1))
    df = _iso(date.today() + timedelta(days=3))
    client = _http_client_returning()

    result = await fornada_tool.execute(
        "create_batch", {"data_inicio": past, "data_fim": df}, "http://x", "tok", client
    )

    assert "error" in result
    assert "passado" in result["error"].lower()


@pytest.mark.asyncio
async def test_create_batch_validation_rejects_end_before_start(request_ctx):
    di = _iso(date.today() + timedelta(days=10))
    df = _iso(date.today() + timedelta(days=2))
    client = _http_client_returning()

    result = await fornada_tool.execute(
        "create_batch", {"data_inicio": di, "data_fim": df}, "http://x", "tok", client
    )

    assert "error" in result
    assert "data_fim" in result["error"].lower()


@pytest.mark.asyncio
async def test_create_batch_validation_rejects_bad_format(request_ctx):
    client = _http_client_returning()
    result = await fornada_tool.execute(
        "create_batch", {"data_inicio": "01/06/2026", "data_fim": "07/06/2026"}, "http://x", "tok", client
    )
    assert "error" in result
    assert "yyyy-MM-dd" in result["error"]


@pytest.mark.asyncio
async def test_create_batch_backend_error_surfaces(request_ctx):
    di, df = _future_dates()
    client = _http_client_returning(400, {"message": "fornada sobreposta"})
    preview = await fornada_tool.execute(
        "create_batch", {"data_inicio": di, "data_fim": df}, "http://x", "tok", client
    )
    request_ctx.append({"role": "user", "content": "sim"})

    result = await fornada_tool.execute(
        "create_batch",
        {
            "data_inicio": di,
            "data_fim": df,
            "confirmed": True,
            "confirm_token": preview["confirm_token"],
        },
        "http://x",
        "tok",
        client,
    )

    assert "error" in result
    assert "400" in result["error"]
    assert "sobreposta" in result["error"]


# add_batch_lines ------------------------------------------------------


@pytest.mark.asyncio
async def test_add_batch_lines_preview_summary(request_ctx):
    client = _http_client_returning()
    result = await fornada_tool.execute(
        "add_batch_lines",
        {
            "fornada_id": 5,
            "lines": [
                {"produto_fornada_id": 10, "quantidade": 20},
                {"produto_fornada_id": 11, "quantidade": 5},
            ],
        },
        "http://x",
        "tok",
        client,
    )

    assert result["requires_confirmation"] is True
    assert "10 x20" in result["message"] or "produto 10 x20" in result["message"]
    assert result["payload"]["fornada_id"] == 5
    client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_batch_lines_commit_posts_each_line(request_ctx):
    client = _http_client_returning(201, {"id": 1})
    args_preview = {
        "fornada_id": 5,
        "lines": [
            {"produto_fornada_id": 10, "quantidade": 20},
            {"produto_fornada_id": 11, "quantidade": 5},
        ],
    }
    preview = await fornada_tool.execute(
        "add_batch_lines", args_preview, "http://x", "tok", client
    )
    request_ctx.append({"role": "user", "content": "sim, pode adicionar"})

    args_commit = {**args_preview, "confirmed": True, "confirm_token": preview["confirm_token"]}
    result = await fornada_tool.execute(
        "add_batch_lines", args_commit, "http://x", "tok", client
    )

    assert result["ok"] is True
    assert len(result["created"]) == 2
    assert client.post.await_count == 2
    first_call_kwargs = client.post.await_args_list[0].kwargs
    assert first_call_kwargs["json"] == {
        "fornadaId": 5,
        "produtoFornadaId": 10,
        "quantidade": 20,
    }


@pytest.mark.asyncio
async def test_add_batch_lines_validation_rejects_negative_quantity(request_ctx):
    client = _http_client_returning()
    result = await fornada_tool.execute(
        "add_batch_lines",
        {
            "fornada_id": 5,
            "lines": [{"produto_fornada_id": 10, "quantidade": 0}],
        },
        "http://x",
        "tok",
        client,
    )
    assert "error" in result
    assert "quantidade" in result["error"].lower()


@pytest.mark.asyncio
async def test_add_batch_lines_validation_rejects_empty(request_ctx):
    client = _http_client_returning()
    result = await fornada_tool.execute(
        "add_batch_lines", {"fornada_id": 5, "lines": []}, "http://x", "tok", client
    )
    assert "error" in result
    assert "lines" in result["error"].lower()


# Throttle -------------------------------------------------------------


@pytest.mark.asyncio
async def test_throttle_blocks_after_burst(monkeypatch, request_ctx):
    write_throttle.write_throttle = write_throttle.WriteThrottle(limits=((1, 60),))
    di, df = _future_dates()
    client = _http_client_returning(201, {"id": 1})

    preview = await fornada_tool.execute(
        "create_batch", {"data_inicio": di, "data_fim": df}, "http://x", "tok", client
    )
    request_ctx.append({"role": "user", "content": "sim"})
    args = {
        "data_inicio": di,
        "data_fim": df,
        "confirmed": True,
        "confirm_token": preview["confirm_token"],
    }
    await fornada_tool.execute("create_batch", args, "http://x", "tok", client)

    di2, df2 = _future_dates(start_offset=15)
    preview2 = await fornada_tool.execute(
        "create_batch", {"data_inicio": di2, "data_fim": df2}, "http://x", "tok", client
    )
    request_ctx.append({"role": "user", "content": "sim de novo"})
    args2 = {
        "data_inicio": di2,
        "data_fim": df2,
        "confirmed": True,
        "confirm_token": preview2["confirm_token"],
    }
    result = await fornada_tool.execute("create_batch", args2, "http://x", "tok", client)

    assert "error" in result
    assert "muitas" in result["error"].lower()
