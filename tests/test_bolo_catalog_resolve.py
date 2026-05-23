"""Cobertura do resolver de nomes para catalogo de bolos."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.tools.writes import bolo_catalog_resolve as resolver
from app.tools.writes._helpers import WriteToolError


def _client_with_catalog(routes: dict[str, list]):
    """httpx.AsyncClient mock que devolve listas diferentes por endpoint."""
    client = MagicMock()

    async def _get(url, headers=None):
        resp = MagicMock()
        for suffix, payload in routes.items():
            if url.endswith(suffix):
                resp.status_code = 200
                resp.json = MagicMock(return_value=payload)
                resp.text = ""
                return resp
        resp.status_code = 404
        resp.json = MagicMock(return_value=[])
        resp.text = ""
        return resp

    client.get = AsyncMock(side_effect=_get)
    return client


@pytest.mark.asyncio
async def test_resolve_massa_by_name_returns_id_and_label():
    client = _client_with_catalog(
        {"/bolos/massa": [{"id": 1, "sabor": "Baunilha"}, {"id": 2, "sabor": "Chocolate"}]}
    )
    mid, label = await resolver.resolve_massa_id(client, "http://x", "tok", "chocolate")
    assert mid == 2
    assert label.lower() == "chocolate"


@pytest.mark.asyncio
async def test_resolve_massa_not_found_raises():
    client = _client_with_catalog({"/bolos/massa": [{"id": 1, "sabor": "Baunilha"}]})
    with pytest.raises(WriteToolError, match="nao encontrada"):
        await resolver.resolve_massa_id(client, "http://x", "tok", "morango")


@pytest.mark.asyncio
async def test_resolve_recheio_by_name_prefers_unitario_first():
    client = _client_with_catalog(
        {
            "/bolos/recheio-unitario": [
                {"id": 5, "sabor": "Brigadeiro"},
                {"id": 6, "sabor": "Ninho"},
            ],
            "/bolos/recheio-exclusivo": [{"id": 10, "nome": "Hugo"}],
        }
    )
    fields, label = await resolver.resolve_recheio_by_name(
        client, "http://x", "tok", "brigadeiro"
    )
    assert fields["recheio_unitario_id"] == 5
    assert fields["recheio_exclusivo_id"] is None
    assert "brigadeiro" in label.lower()


@pytest.mark.asyncio
async def test_resolve_recheio_by_name_falls_back_to_exclusivo():
    client = _client_with_catalog(
        {
            "/bolos/recheio-unitario": [{"id": 5, "sabor": "Brigadeiro"}],
            "/bolos/recheio-exclusivo": [{"id": 10, "nome": "Hugo"}],
        }
    )
    fields, label = await resolver.resolve_recheio_by_name(
        client, "http://x", "tok", "Hugo"
    )
    assert fields["recheio_exclusivo_id"] == 10
    assert fields["recheio_unitario_id"] is None
    assert label == "Hugo"


@pytest.mark.asyncio
async def test_apply_catalog_names_fills_ids_and_labels():
    client = _client_with_catalog(
        {
            "/bolos/massa": [{"id": 1, "sabor": "Baunilha"}],
            "/bolos/recheio-exclusivo": [{"id": 9, "nome": "Hugo"}],
        }
    )
    out = await resolver.apply_catalog_names(
        {"massa_nome": "baunilha", "recheio_exclusivo_nome": "Hugo"},
        client,
        "http://x",
        "tok",
    )
    assert out["massa_id"] == 1
    assert out["recheio_exclusivo_id"] == 9
    labels = out["_catalog_labels"]
    assert labels["massa"].lower() == "baunilha"
    assert labels["recheio"] == "Hugo"


@pytest.mark.asyncio
async def test_apply_catalog_names_keeps_existing_ids():
    client = _client_with_catalog({})
    out = await resolver.apply_catalog_names(
        {"massa_id": 7, "recheio_unitario_id": 3},
        client,
        "http://x",
        "tok",
    )
    assert out["massa_id"] == 7
    assert out["recheio_unitario_id"] == 3
    client.get.assert_not_awaited()
