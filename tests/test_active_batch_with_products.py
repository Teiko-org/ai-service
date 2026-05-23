from unittest.mock import AsyncMock, MagicMock

import pytest

from app.tools import batches as batches_tool


@pytest.mark.asyncio
async def test_get_active_batch_with_products():
    active_resp = MagicMock()
    active_resp.status_code = 200
    active_resp.json = MagicMock(
        return_value=[
            {"id": 14, "dataInicio": "2026-06-10", "dataFim": "2026-06-16", "ativo": True}
        ]
    )
    active_resp.raise_for_status = MagicMock()

    prod_resp = MagicMock()
    prod_resp.status_code = 200
    prod_resp.json = MagicMock(
        return_value=[
            {
                "produto": "Pao Frances",
                "categoria": "Padaria",
                "quantidade": 5,
                "valor": 5.0,
            },
            {"produto": "Croissant", "categoria": "Salgados", "quantidade": 3, "valor": 12.0},
            {
                "produto": "Pao Frances",
                "categoria": "Padaria",
                "quantidade": 1,
                "valor": 5.0,
            },
            {"produto": "Croissant", "categoria": "Salgados", "quantidade": 3, "valor": 12.0},
        ]
    )
    prod_resp.raise_for_status = MagicMock()

    client = AsyncMock()
    client.get = AsyncMock(side_effect=[active_resp, prod_resp])

    result = await batches_tool.execute(
        "get_active_batch_with_products", {}, "http://x", None, client
    )

    assert result["fornada_ativa"]["id"] == 14
    assert len(result["produtos"]) == 2
    by_name = {p["produto"]: p["quantidade"] for p in result["produtos"]}
    assert by_name["Pao Frances"] == 6
    assert by_name["Croissant"] == 6
