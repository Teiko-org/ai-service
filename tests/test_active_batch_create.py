from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings
from app.tools.writes import fornada as fornada_tool


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "confirm_token_secret", "test-secret-only-for-tests")
    monkeypatch.setattr(settings, "allow_anonymous_writes", True)


@pytest.mark.asyncio
async def test_create_batch_blocked_when_any_active_exists():
    di = date.today() + timedelta(days=20)
    df = di + timedelta(days=6)
    list_resp = MagicMock()
    list_resp.status_code = 200
    list_resp.json = MagicMock(
        return_value=[
            {
                "id": 5,
                "dataInicio": "2026-06-01",
                "dataFim": "2026-06-07",
                "ativo": True,
            }
        ]
    )
    list_resp.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(return_value=list_resp)

    result = await fornada_tool.execute(
        "create_batch",
        {"data_inicio": di.isoformat(), "data_fim": df.isoformat()},
        "http://x",
        None,
        client,
    )

    assert "error" in result
    assert "fornada ativa" in result["error"].lower()
    assert "#5" in result["error"]
    client.post.assert_not_called()
