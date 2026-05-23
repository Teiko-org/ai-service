from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings
from app.core.request_context import current_history, current_session_id
from app.tools.writes import fornada as fornada_tool

SESSION_ID = "sess-replace-1"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "confirm_token_secret", "test-secret-only-for-tests")
    monkeypatch.setattr(settings, "allow_anonymous_writes", True)


def _future_dates():
    di = date.today() + timedelta(days=12)
    df = di + timedelta(days=6)
    return di.isoformat(), df.isoformat()


def _client_replace_ok(close_id: int = 11, new_id: int = 20):
    list_resp = MagicMock()
    list_resp.status_code = 200
    list_resp.json = MagicMock(
        return_value=[
            {
                "id": close_id,
                "dataInicio": "2026-06-01",
                "dataFim": "2026-06-07",
                "ativo": True,
            }
        ]
    )
    list_resp.raise_for_status = MagicMock()
    del_resp = MagicMock()
    del_resp.status_code = 204
    post_resp = MagicMock()
    post_resp.status_code = 201
    post_resp.json = MagicMock(
        return_value={
            "id": new_id,
            "dataInicio": "2026-06-10",
            "dataFim": "2026-06-16",
            "ativo": True,
        }
    )
    post_resp.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(return_value=list_resp)
    client.delete = AsyncMock(return_value=del_resp)
    client.post = AsyncMock(return_value=post_resp)
    return client


@pytest.mark.asyncio
async def test_replace_active_batch_preview_and_commit():
    di, df = _future_dates()
    client = _client_replace_ok()
    hist = [{"role": "user", "content": "troca a fornada"}]
    sess = current_session_id.set(SESSION_ID)
    hist_tok = current_history.set(hist)
    try:
        preview = await fornada_tool.execute(
            "replace_active_batch",
            {"data_inicio": di, "data_fim": df},
            "http://x",
            None,
            client,
        )
        assert preview["requires_confirmation"] is True
        assert "encerrar" in preview["message"].lower()
        assert "criar" in preview["message"].lower()
        assert "#11" in preview["message"]
        assert di in preview["message"]

        current_history.set([*hist, {"role": "user", "content": "sim"}])
        result = await fornada_tool.execute(
            "replace_active_batch",
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
        assert result["ok"] is True
        assert result["closed_fornada_id"] == 11
        client.delete.assert_awaited_once()
        client.post.assert_awaited_once()
    finally:
        current_session_id.reset(sess)
        current_history.reset(hist_tok)


@pytest.mark.asyncio
async def test_replace_active_batch_without_active_errors():
    di, df = _future_dates()
    list_resp = MagicMock()
    list_resp.status_code = 200
    list_resp.json = MagicMock(return_value=[])
    list_resp.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(return_value=list_resp)

    result = await fornada_tool.execute(
        "replace_active_batch",
        {"data_inicio": di, "data_fim": df},
        "http://x",
        None,
        client,
    )
    assert "error" in result
    assert "create_batch" in result["error"]
