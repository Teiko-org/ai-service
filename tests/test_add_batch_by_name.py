"""add_batch_lines resolve produto_nome sem o usuario saber IDs."""

from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.core.request_context import current_history, current_session_id
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
async def test_add_batch_lines_by_product_name_preview():
    client = AsyncMock()
    sess = current_session_id.set("s1")
    hist = current_history.set([{"role": "user", "content": "adicione paes"}])
    try:
        result = await fornada_tool.execute(
            "add_batch_lines",
            {
                "fornada_id": 11,
                "lines": [
                    {"produto_nome": "paes", "quantidade": 5},
                    {"produto_nome": "Croissant", "quantidade": 2},
                ],
            },
            "http://x",
            None,
            client,
        )
    finally:
        current_history.reset(hist)
        current_session_id.reset(sess)

    assert result["requires_confirmation"] is True
    assert "Pao Frances x5" in result["message"]
    assert "Croissant x2" in result["message"]
    assert result["payload"]["lines"][0]["produto_fornada_id"] == 10
