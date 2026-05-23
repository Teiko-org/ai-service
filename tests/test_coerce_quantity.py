from unittest.mock import AsyncMock

import pytest

from app.tools.writes.product_resolve import _coerce_quantity, resolve_batch_lines


def test_coerce_quantity_accepts_float_whole():
    assert _coerce_quantity(5.0, 1) == 5


def test_coerce_quantity_accepts_string_digit():
    assert _coerce_quantity("3", 1) == 3


@pytest.mark.asyncio
async def test_resolve_batch_lines_float_quantity(monkeypatch):
    monkeypatch.setattr(
        "app.tools.writes.product_resolve.fetch_fornada_catalog",
        AsyncMock(
            return_value=[
                {"id": 10, "nome": "Pao Frances", "tipo": "FORNADA", "ativo": True},
                {"id": 11, "nome": "Croissant", "tipo": "FORNADA", "ativo": True},
            ]
        ),
    )
    client = AsyncMock()
    lines = await resolve_batch_lines(
        [
            {"produto_nome": "paes franceses", "quantidade": 5.0},
            {"produto_nome": "croissants", "quantidade": 3.0},
        ],
        client,
        "http://x",
        None,
    )
    assert len(lines) == 2
    assert lines[0]["quantidade"] == 5
    assert lines[1]["quantidade"] == 3
