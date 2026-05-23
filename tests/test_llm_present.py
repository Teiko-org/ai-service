"""Payloads apresentaveis para o Gemini."""

from app.core.llm_present import (
    format_order_line,
    prepare_tool_result_for_llm,
    simplify_order_row,
    trim_orders_for_llm,
)


def test_recent_orders_trimmed_and_simplified():
    raw = [
        {
            "id": 3019,
            "nomeDoCliente": "Maria",
            "status": "PENDENTE",
            "valorPedido": 175.0,
            "tipoDoPedido": "RETIRADA",
            "tipoProduto": "BOLO",
            "dataPedido": "2026-05-23T10:00:00",
        }
    ]
    out = prepare_tool_result_for_llm("get_recent_orders", raw)
    assert out["returned"] == 1
    row = out["data"][0]
    assert row["pedido_numero"] == 3019
    assert row["cliente"] == "Maria"
    assert row["status"] == "Pendente"
    assert row["valor"] == "R$ 175,00"
    assert row["entrega"] == "Retirada"
    assert "linha" in row
    assert "—" not in row["linha"]
    assert "Maria" in row["linha"]
    assert "instruction" in out


def test_order_line_skips_empty_fields_no_double_dash():
    line = format_order_line(
        {
            "pedido_numero": 3019,
            "produto": "Bolo",
            "status": "Pendente",
            "valor": "R$ 0,00",
            "data": "01/01/2025",
        }
    )
    assert "—" not in line
    assert line.startswith("Pedido #3019 (Bolo)")
    assert "Pendente" in line


def test_whatsapp_message_instruction_only_body():
    raw = {"ok": True, "order_ids": [1, 2], "message_text": "Ola! Pedido #1"}
    out = prepare_tool_result_for_llm("generate_whatsapp_message", raw)
    assert out["message_text"] == "Ola! Pedido #1"
    assert "Sem aspas" in out["instruction"]


def test_doughs_catalog_dedupes_snake_case():
    raw = {"data": [
        {"id": 1, "sabor": "cacau"},
        {"id": 5, "sabor": "cacau"},
        {"id": 2, "sabor": "baunilha"},
    ]}
    out = prepare_tool_result_for_llm("get_doughs_catalog", raw)
    assert out["sabores_distintos"] == 2
    assert out["total_registros_api"] == 3
    names = [i["nome"] for i in out["itens"]]
    assert "Cacau" in names
    assert "Baunilha" in names


def test_exclusive_fillings_human_label():
    raw = [
        {
            "id": 10,
            "nome": "Giovanna",
            "sabor1": "brigadeiro_tradicional",
            "sabor2": "ninho",
        }
    ]
    out = prepare_tool_result_for_llm("get_exclusive_fillings_catalog", raw)
    assert "Giovanna (Brigadeiro Tradicional + Ninho)" in out["itens"][0]["nome"]


def test_get_next_batch_enriched_for_llm():
    raw = {
        "id": 14,
        "dataInicio": "2026-06-10",
        "dataFim": "2026-06-16",
        "ativo": True,
    }
    out = prepare_tool_result_for_llm("get_next_batch", raw)
    assert out["fornada"]["numero"] == 14
    assert out["fornada"]["periodo_calendario"] == "futuro"
    assert "NUNCA diga so" in out["instruction"]


def test_trim_many_orders():
    orders = [{"id": i, "dataPedido": f"2026-05-{i:02d}"} for i in range(1, 20)]
    out = trim_orders_for_llm(orders, limit=8, simplify=False)
    assert out["returned"] == 8
    assert out["truncated"] is True
