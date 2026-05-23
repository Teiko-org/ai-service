"""Testes da Feature 3 (Gestao de Fornada) e tools de catalogo."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tools.registry import execute_tool


def _resp(status_code=200, payload=None):
    r = MagicMock()
    r.status_code = status_code
    r.json = MagicMock(return_value=payload if payload is not None else {})
    r.raise_for_status = MagicMock()
    return r


def _client(get_resp):
    c = MagicMock()
    c.get = AsyncMock(return_value=get_resp)
    return c


@pytest.mark.asyncio
async def test_get_next_batch_returns_payload():
    client = _client(_resp(200, {"id": 5, "dataInicio": "2026-05-15"}))
    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool("get_next_batch", {}, "http://x", "tok")
    assert result["fornada"]["numero"] == 5
    assert "instruction" in result
    assert client.get.call_args[0][0].endswith("/fornadas/proxima")


@pytest.mark.asyncio
async def test_get_next_batch_204_returns_message():
    client = _client(_resp(204))
    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool("get_next_batch", {}, "http://x", "tok")
    assert result["data"] is None
    assert "message" in result


@pytest.mark.asyncio
async def test_get_active_batches_calls_correct_endpoint():
    client = _client(_resp(200, [{"id": 1}, {"id": 2}]))
    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool("get_active_batches", {}, "http://x", "tok")
    assert result["total"] == 2
    assert result["fornadas"][0]["numero"] == 1
    assert client.get.call_args[0][0].endswith("/fornadas")


@pytest.mark.asyncio
async def test_get_batches_by_month_requires_year_and_month():
    client = _client(_resp(200, []))
    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool(
            "get_batches_by_month", {"year": 2026}, "http://x", "tok"
        )
    assert "error" in result
    client.get.assert_not_called()


@pytest.mark.asyncio
async def test_get_batches_by_month_passes_query_params():
    client = _client(_resp(200, []))
    with patch("app.tools.registry.get_http_client", return_value=client):
        await execute_tool(
            "get_batches_by_month",
            {"year": 2026, "month": 5},
            "http://x",
            "tok",
        )
    params = client.get.call_args.kwargs["params"]
    assert params == {"ano": 2026, "mes": 5}


@pytest.mark.asyncio
async def test_get_products_in_batch_url():
    client = _client(_resp(200, []))
    with patch("app.tools.registry.get_http_client", return_value=client):
        await execute_tool(
            "get_products_in_batch", {"batch_id": 7}, "http://x", "tok"
        )
    url = client.get.call_args[0][0]
    assert url.endswith("/fornadas/da-vez/produtos/7")


@pytest.mark.asyncio
async def test_get_latest_batch_products_endpoint():
    client = _client(_resp(200, []))
    with patch("app.tools.registry.get_http_client", return_value=client):
        await execute_tool("get_latest_batch_products", {}, "http://x", "tok")
    url = client.get.call_args[0][0]
    assert url.endswith("/fornadas/mais-recente/produtos")


@pytest.mark.asyncio
async def test_catalog_decorations_endpoint():
    client = _client(_resp(200, [{"id": 1, "nome": "Topo de bolo"}]))
    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool("get_decorations", {}, "http://x", "tok")
    assert result["data"][0]["nome"] == "Topo de bolo"
    url = client.get.call_args[0][0]
    assert url.endswith("/decoracoes")


@pytest.mark.asyncio
async def test_catalog_cake_sizes_endpoint():
    client = _client(_resp(200, ["TAMANHO_5", "TAMANHO_7"]))
    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool("get_cake_sizes", {}, "http://x", "tok")
    assert result["data"] == ["TAMANHO_5", "TAMANHO_7"]
    url = client.get.call_args[0][0]
    assert url.endswith("/bolos/tamanhos")


@pytest.mark.asyncio
async def test_catalog_204_returns_empty_data():
    client = _client(_resp(204))
    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool(
            "get_registered_products", {}, "http://x", "tok"
        )
    assert result["data"] == []
    assert "message" in result
