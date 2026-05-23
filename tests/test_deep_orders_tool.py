"""Testes da Feature 2 — Detalhamento e filtros de pedidos."""

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
async def test_get_order_summary_accepts_string_pedido_format():
    client = _client(_resp(200, {"id": 5, "valor": 10.0}))
    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool(
            "get_order_summary_by_id", {"order_id": "pedido 5"}, "http://x", "tok"
        )
    assert result["id"] == 5
    assert client.get.call_args[0][0].endswith("/resumo-pedido/5")


@pytest.mark.asyncio
async def test_get_cake_order_details_resolves_via_resumo_pedido_bolo_id():
    resumo = _resp(200, {"id": 7, "pedidoBoloId": 88})
    detalhe = _resp(200, {"id": 88, "massa": "Chocolate"})
    client = MagicMock()
    client.get = AsyncMock(side_effect=[resumo, detalhe])

    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool(
            "get_cake_order_details", {"order_id": 7}, "http://x", "tok"
        )
    assert result["massa"] == "Chocolate"
    assert result["pedido_numero"] == 7
    assert "instruction" in result
    calls = client.get.call_args_list
    assert calls[0][0][0].endswith("/resumo-pedido/7")
    assert calls[1][0][0].endswith("/resumo-pedido/pedido-bolo/detalhe/88")


@pytest.mark.asyncio
async def test_get_orders_by_status_uppercases_input():
    client = _client(_resp(200, [{"id": 1}]))
    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool(
            "get_orders_by_status", {"status": "pendente"}, "http://x", "tok"
        )
    assert result["data"] == [{"id": 1}]
    url = client.get.call_args[0][0]
    assert url.endswith("/resumo-pedido/status/PENDENTE")


@pytest.mark.asyncio
async def test_get_orders_by_status_invalid_returns_error():
    client = _client(_resp(200, []))
    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool(
            "get_orders_by_status", {"status": "FOO"}, "http://x", "tok"
        )
    assert "error" in result


@pytest.mark.asyncio
async def test_get_orders_by_delivery_date_with_status_filter():
    client = _client(_resp(200, [{"id": 7}]))
    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool(
            "get_orders_by_delivery_date",
            {"delivery_date": "2026-05-12", "status": "PAGO"},
            "http://x",
            "tok",
        )
    assert result["data"] == [{"id": 7}]
    kwargs = client.get.call_args.kwargs
    assert kwargs["params"]["dataEntrega"] == "2026-05-12"
    assert kwargs["params"]["status"] == "PAGO"


@pytest.mark.asyncio
async def test_get_orders_by_delivery_date_204_returns_empty_data():
    client = _client(_resp(204))
    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool(
            "get_orders_by_delivery_date",
            {"delivery_date": "2026-05-12"},
            "http://x",
            "tok",
        )
    assert result["data"] == []
    assert "message" in result


@pytest.mark.asyncio
async def test_get_orders_by_dough_passes_status_when_present():
    client = _client(_resp(200, [{"id": 1}]))
    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool(
            "get_orders_by_dough",
            {"dough_id": 4, "status": "CONCLUIDO"},
            "http://x",
            "tok",
        )
    url = client.get.call_args[0][0]
    assert url.endswith("/resumo-pedido/pedido-bolo/por-massa/4")
    assert client.get.call_args.kwargs["params"]["status"] == "CONCLUIDO"
    assert result["data"] == [{"id": 1}]
    assert result["total"] == 1
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_get_orders_by_dough_trims_large_duplicate_list():
    payload = [
        {"id": i, "status": "PAGO", "dataPedido": f"2024-09-{d:02d}T10:00:00"}
        for i, d in enumerate(range(1, 25), start=1)
    ]
    payload.append(
        {"id": 1, "status": "PAGO", "dataPedido": "2024-09-01T10:00:00"}
    )
    client = _client(_resp(200, payload))
    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool(
            "get_orders_by_dough",
            {"dough_id": 2},
            "http://x",
            "tok",
        )
    assert result["returned"] == 10
    assert result["truncated"] is True
    assert result["total"] == 24  # 24 unicos + 1 duplicata do id 1
    assert len(result["data"]) == 10


@pytest.mark.asyncio
async def test_get_cake_order_details_404_returns_error_message():
    resumo = _resp(404)
    client = MagicMock()
    client.get = AsyncMock(return_value=resumo)

    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool(
            "get_cake_order_details", {"order_id": 999}, "http://x", "tok"
        )
    assert "error" in result
    assert "999" in result["error"]
    assert client.get.call_count == 1


@pytest.mark.asyncio
async def test_get_cake_order_details_resumo_sem_bolo_retorna_erro_claro():
    resumo = _resp(200, {"id": 3, "pedidoBoloId": None, "pedidoFornadaId": 10})
    client = MagicMock()
    client.get = AsyncMock(return_value=resumo)

    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool(
            "get_cake_order_details", {"order_id": 3}, "http://x", "tok"
        )
    assert "error" in result
    assert "nao e um pedido de bolo" in result["error"].lower()
    assert client.get.call_count == 1


@pytest.mark.asyncio
async def test_get_order_summary_by_id_missing_param():
    client = _client(_resp(200))
    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool(
            "get_order_summary_by_id", {}, "http://x", "tok"
        )
    assert "error" in result
