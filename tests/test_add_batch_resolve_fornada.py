from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.core.request_context import current_history, current_session_id
from app.core.sessions import session_store
from app.tools.writes import fornada as fornada_tool

_CATALOG = [
    {"id": 10, "nome": "Pao Frances", "tipo": "FORNADA", "ativo": True},
    {"id": 11, "nome": "Croissant", "tipo": "FORNADA", "ativo": True},
]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "confirm_token_secret", "test-secret-only-for-tests")
    monkeypatch.setattr(settings, "allow_anonymous_writes", True)
    monkeypatch.setattr(
        "app.tools.writes.product_resolve.fetch_fornada_catalog",
        AsyncMock(return_value=_CATALOG),
    )


@pytest.mark.asyncio
async def test_add_batch_lines_uses_last_fornada_in_session_without_id():
    sid = "sess-add-no-id"
    session_store.get_or_create(sid)
    session_store.set_last_fornada_id(sid, 12)
    client = AsyncMock()
    sess_tok = current_session_id.set(sid)
    hist_tok = current_history.set(
        [{"role": "user", "content": "adicione na fornada que criamos"}]
    )
    try:
        result = await fornada_tool.execute(
            "add_batch_lines",
            {
                "lines": [
                    {"produto_nome": "Pao Frances", "quantidade": 5},
                    {"produto_nome": "Croissant", "quantidade": 3},
                ],
            },
            "http://x",
            None,
            client,
        )
    finally:
        current_history.reset(hist_tok)
        current_session_id.reset(sess_tok)

    assert result["requires_confirmation"] is True
    assert result["payload"]["fornada_id"] == 12
    assert "fornada #12" in result["message"]
    assert "Pao Frances x5" in result["message"]
    assert "Croissant x3" in result["message"]


@pytest.mark.asyncio
async def test_add_batch_lines_falls_back_to_active_fornada(monkeypatch):
    monkeypatch.setattr(
        "app.tools.writes.fornada.find_active_batch",
        AsyncMock(
            return_value={
                "id": 7,
                "dataInicio": "2026-06-10",
                "dataFim": "2026-06-16",
                "ativo": True,
            }
        ),
    )
    sid = "sess-add-active"
    session_store.get_or_create(sid)
    client = AsyncMock()
    sess_tok = current_session_id.set(sid)
    hist_tok = current_history.set([{"role": "user", "content": "coloca paes na fornada"}])
    try:
        result = await fornada_tool.execute(
            "add_batch_lines",
            {"lines": [{"produto_nome": "paes", "quantidade": 2}]},
            "http://x",
            None,
            client,
        )
    finally:
        current_history.reset(hist_tok)
        current_session_id.reset(sess_tok)

    assert result["requires_confirmation"] is True
    assert result["payload"]["fornada_id"] == 7
