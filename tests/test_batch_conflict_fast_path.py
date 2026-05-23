"""Resposta sim/substituir com fornada ativa gera previa e botao Confirmar."""

from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.api.routes import (
    _extract_batch_dates_from_history,
    _try_batch_conflict_fast_path,
)
from app.config import settings


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "confirm_token_secret", "test-secret-only-for-tests")
    monkeypatch.setattr(settings, "allow_anonymous_writes", True)
    monkeypatch.setattr(settings, "enable_write_tools", True)


def test_extract_batch_dates_from_history():
    history = [
        {
            "role": "user",
            "content": "Vou criar fornada de 14/06/2026 a 24/06/2026. Confirma?",
        }
    ]
    dates = _extract_batch_dates_from_history(history)
    assert dates == ("2026-06-14", "2026-06-24")


@pytest.mark.asyncio
async def test_sim_after_conflict_offers_replace_preview():
    preview = {
        "requires_confirmation": True,
        "action": "replace_active_batch",
        "confirm_token": "tok.sig",
        "payload": {"data_inicio": "2026-06-14", "data_fim": "2026-06-24"},
        "message": "Vou encerrar a fornada #5 e criar uma nova de 2026-06-14 a 2026-06-24. Confirma?",
    }
    history = [
        {
            "role": "user",
            "content": "Vou criar fornada de 14/06/2026 a 24/06/2026. Confirma?",
        },
        {
            "role": "assistant",
            "content": "Ja existe fornada ativa. Substituir ou encerrar?",
        },
    ]
    with patch("app.api.routes.execute_tool", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = preview
        out = await _try_batch_conflict_fast_path("sim", history, "bearer")

    assert out is not None
    assert out["pending_confirmation"]["action"] == "replace_active_batch"
    assert "Confirma" in out["answer"]
    mock_exec.assert_awaited_once()
