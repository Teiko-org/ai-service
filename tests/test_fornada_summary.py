"""Resumo de produtos apos commit de fornada."""

from unittest.mock import AsyncMock, patch

import pytest

from app.core.fornada_summary import append_fornada_summary_if_applicable


@pytest.mark.asyncio
async def test_append_fornada_summary_after_add_batch_lines():
    with patch(
        "app.core.fornada_summary.execute_tool", new_callable=AsyncMock
    ) as mock_tool:
        mock_tool.return_value = {
            "fornada_id": 14,
            "produtos": [
                {"produto": "Pao Frances", "quantidade": 5},
                {"produto": "Croissant", "quantidade": 3},
                {"produto": "Pao Frances", "quantidade": 3},
                {"produto": "Croissant", "quantidade": 3},
            ],
        }
        answer = await append_fornada_summary_if_applicable(
            "add_batch_lines",
            {"ok": True, "fornada_id": 14},
            "Produtos adicionados a fornada #14 com sucesso.",
            "http://x",
            "tok",
        )
    assert "Fornada #14 agora:" in answer
    assert "Pao Frances x8" in answer
    assert "Croissant x6" in answer
    assert "x5" not in answer


@pytest.mark.asyncio
async def test_append_fornada_summary_skips_on_error():
    answer = await append_fornada_summary_if_applicable(
        "add_batch_lines",
        {"error": "falhou"},
        "Erro.",
        "http://x",
        "tok",
    )
    assert answer == "Erro."
