"""Recorte de listas grandes de pedidos para o LLM."""

from app.tools.deep_orders import _trim_orders_for_llm


def test_trim_orders_dedupes_and_limits_to_ten_most_recent():
    orders = [
        {
            "id": i,
            "status": "PAGO",
            "dataPedido": f"2024-09-{10 + i:02d}T10:00:00",
            "valor": 100.0,
        }
        for i in range(1, 16)
    ]

    result = _trim_orders_for_llm(orders, limit=10)

    assert result["returned"] == 10
    assert result["truncated"] is True
    assert result["total"] == 15
    assert len(result["data"]) == 10
    assert result["data"][0]["dataPedido"] >= result["data"][-1]["dataPedido"]
    assert "message" in result


def test_trim_orders_small_list_not_truncated():
    orders = [{"id": 1, "dataPedido": "2025-01-01T00:00:00"}]
    result = _trim_orders_for_llm(orders)
    assert result["total"] == 1
    assert result["truncated"] is False
    assert "message" not in result
