from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings
from app.core.request_context import current_history, current_session_id
from app.tools.writes import fornada as fornada_tool

SESSION_ID = "sess-close-1"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "confirm_token_secret", "test-secret-only-for-tests")
    monkeypatch.setattr(settings, "allow_anonymous_writes", True)


def _future_dates():
    di = date.today() + timedelta(days=5)
    df = di + timedelta(days=6)
    return di.isoformat(), df.isoformat()


def _client_delete_ok():
    client = MagicMock()
    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json = MagicMock(
        return_value={
            "id": 11,
            "dataInicio": "2026-06-01",
            "dataFim": "2026-06-07",
            "ativo": True,
        }
    )
    get_resp.raise_for_status = MagicMock()
    del_resp = MagicMock()
    del_resp.status_code = 204
    client.get = AsyncMock(return_value=get_resp)
    client.delete = AsyncMock(return_value=del_resp)
    return client


@pytest.mark.asyncio
async def test_close_batch_preview_and_commit():
    client = _client_delete_ok()
    hist = [{"role": "user", "content": "cancele a fornada"}]
    sess = current_session_id.set(SESSION_ID)
    hist_tok = current_history.set(hist)
    try:
        preview = await fornada_tool.execute(
            "close_batch", {"fornada_ids": [11]}, "http://x", None, client
        )
        assert preview["requires_confirmation"] is True
        assert "encerrar" in preview["message"].lower()
        assert "confirma" in preview["message"].lower()

        current_history.set([*hist, {"role": "user", "content": "sim"}])
        result = await fornada_tool.execute(
            "close_batch",
            {
                "fornada_ids": [11],
                "confirmed": True,
                "confirm_token": preview["confirm_token"],
            },
            "http://x",
            None,
            client,
        )
    finally:
        current_history.reset(hist_tok)
        current_session_id.reset(sess)

    assert result["ok"] is True
    client.delete.assert_awaited_once()
    assert client.delete.call_args[0][0].endswith("/fornadas/11")
