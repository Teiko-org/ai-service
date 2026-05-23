from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings
from app.core.request_context import current_history, current_session_id
from app.tools.writes import fornada as fornada_tool

SESSION_ID = "sess-close-multi"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "confirm_token_secret", "test-secret-only-for-tests")
    monkeypatch.setattr(settings, "allow_anonymous_writes", True)


def _detail(fid: int, start: str, end: str):
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(
        return_value={"id": fid, "dataInicio": start, "dataFim": end, "ativo": True}
    )
    resp.raise_for_status = MagicMock()
    return resp


def _client_multi_delete():
    client = MagicMock()
    client.get = AsyncMock(
        side_effect=[
            _detail(10, "2025-12-08", "2025-12-16"),
            _detail(11, "2026-06-01", "2026-06-07"),
        ]
    )
    del_resp = MagicMock()
    del_resp.status_code = 204
    client.delete = AsyncMock(return_value=del_resp)
    return client


@pytest.mark.asyncio
async def test_close_batch_multiple_ids_one_confirmation():
    client = _client_multi_delete()
    hist = [{"role": "user", "content": "encerre 10 e 11"}]
    sess = current_session_id.set(SESSION_ID)
    hist_tok = current_history.set(hist)
    try:
        preview = await fornada_tool.execute(
            "close_batch",
            {"fornada_ids": [10, 11]},
            "http://x",
            None,
            client,
        )
        assert preview["requires_confirmation"] is True
        assert "#10" in preview["message"]
        assert "#11" in preview["message"]
        assert preview["payload"]["fornada_ids"] == [10, 11]

        current_history.set([*hist, {"role": "user", "content": "sim"}])
        result = await fornada_tool.execute(
            "close_batch",
            {
                "fornada_ids": [10, 11],
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
    assert result["fornada_ids"] == [10, 11]
    assert client.delete.await_count == 2
