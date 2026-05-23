"""Filtros de pedidos por data e massa."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.tools.order_filters import (
    fetch_orders_by_delivery_date,
    fetch_orders_by_mass_ids,
    parse_delivery_date_arg,
    resolve_mass_ids_for_name,
)


def test_parse_delivery_date_brazilian():
    d, err = parse_delivery_date_arg("10/01/2025")
    assert err is None
    assert d == date(2025, 1, 10)


@pytest.mark.asyncio
async def test_resolve_mass_ids_uses_single_canonical_id():
    client = MagicMock()

    async def _get(url, headers=None, params=None):
        resp = MagicMock()
        resp.status_code = 200
        if url.endswith("/bolos/massa"):
            resp.json = MagicMock(
                return_value=[
                    {"id": 2, "sabor": "cacau"},
                    {"id": 149, "sabor": "cacau"},
                    {"id": 150, "sabor": "cacau_expresso"},
                    {"id": 3, "sabor": "baunilha"},
                ]
            )
        return resp

    client.get = AsyncMock(side_effect=_get)
    ids, err = await resolve_mass_ids_for_name(client, "http://x", "tok", "cacau")
    assert err is None
    assert ids == [149]


@pytest.mark.asyncio
async def test_resolve_mass_ids_does_not_match_cacau_expresso_for_cacau():
    client = MagicMock()

    async def _get(url, headers=None, params=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(
            return_value=[{"id": 150, "sabor": "cacau_expresso"}]
        )
        return resp

    client.get = AsyncMock(side_effect=_get)
    ids, err = await resolve_mass_ids_for_name(client, "http://x", "tok", "cacau")
    assert err is not None
    assert ids == []


@pytest.mark.asyncio
async def test_fetch_orders_by_mass_ids_merges():
    client = MagicMock()

    async def _get(url, headers=None, params=None, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(return_value=[{"id": 816, "status": "PENDENTE"}])
        return resp

    client.get = AsyncMock(side_effect=_get)
    out = await fetch_orders_by_mass_ids(client, "http://x", "tok", [149], None)
    assert out["data"][0]["pedido_numero"] == 816


@pytest.mark.asyncio
async def test_delivery_date_fallback_by_previsao():
    client = MagicMock()
    noise = [
        {
            "id": i,
            "status": "PENDENTE",
            "valor": 10.0,
            "pedidoBoloId": (i % 8) + 1,
            "dataEntrega": "2026-05-20T00:00:00",
        }
        for i in range(1, 50)
    ]

    async def _get(url, headers=None, params=None, **kwargs):
        resp = MagicMock()
        if "por-data-entrega" in url:
            resp.status_code = 204
            return resp
        if url.endswith("/pedido-bolo") and "detalhe" not in url:
            resp.status_code = 200
            resp.json = MagicMock(
                return_value=[
                    *noise,
                    {
                        "id": 816,
                        "status": "PENDENTE",
                        "valor": 95.0,
                        "pedidoBoloId": 88,
                        "dataEntrega": None,
                    },
                ]
            )
            return resp
        if url.endswith("/detalhe/88"):
            resp.status_code = 200
            resp.json = MagicMock(
                return_value={
                    "nomeCliente": "Joao Silva",
                    "dataPrevisaoEntrega": "2025-01-10",
                }
            )
            return resp
        resp.status_code = 204
        return resp

    client.get = AsyncMock(side_effect=_get)
    out = await fetch_orders_by_delivery_date(
        client, "http://x", "tok", "10/01/2025", None
    )
    assert out["data"][0]["pedido_numero"] == 816
    assert out["data"][0]["cliente"] == "Joao Silva"
    assert "ampliada" in out.get("message", "").lower()
