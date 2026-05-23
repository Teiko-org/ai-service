"""Extracao de IDs das respostas do assistant para o runner do roteiro."""

from scripts.roteiro_resolve import (
    _parse_orders_from_text,
    _pick_batch_id,
    _pick_batch_ids,
    _pick_mass_id,
    _pick_massa_nome,
    br_date_to_iso,
    build_runtime_config,
)


def test_parse_orders_from_linha_format():
    text = """
Pedido #3019 (Bolo) · Maria · Pendente · R$ 175,00 · Retirada · 23/05/2026

Pedido #816 (Bolo) · Joao · Pago · R$ 200,00 · Entrega · 10/01/2025
"""
    orders = _parse_orders_from_text(text)
    assert len(orders) == 2
    assert orders[0].order_id == 3019
    assert orders[0].tipo == "Bolo"
    assert orders[1].delivery_date == "10/01/2025"


def test_pick_mass_and_batch():
    massas = "#149 · Cacau expresso\n#2 · Baunilha"
    assert _pick_mass_id(massas) == 149
    assert _pick_massa_nome(massas) == "Cacau expresso"
    batch = "Fornada #14 aberta no sistema. Periodo 10/06 a 16/06."
    assert _pick_batch_id(batch) == 14
    multi = "Fornada #16 e fornada #17 ativas."
    assert _pick_batch_ids(multi) == [16, 17]


def test_br_date_to_iso():
    assert br_date_to_iso("23/05/2026") == "2026-05-23"
    assert br_date_to_iso("2026-05-15") == "2026-05-15"


def test_build_runtime_config():
    from scripts.roteiro_resolve import ParsedOrder

    orders = [
        ParsedOrder(3019, "Bolo", "Pendente", "23/05/2026"),
        ParsedOrder(500, "Fornada", "Pago", None),
    ]
    cfg = build_runtime_config(
        orders,
        massa_id=149,
        batch_id=14,
        batch_ids=[14, 15],
        massa_nome="Cacau",
    )
    assert cfg["order_id_bolo"] == 3019
    assert cfg["order_id_fornada"] == 500
    assert cfg["massa_id"] == 149
    assert cfg["batch_id"] == 14
    assert cfg["delivery_date_iso"] == "2026-05-23"
    assert cfg["batch_close_1"] == 14
    assert cfg["batch_close_2"] == 15
    assert cfg["massa_nome"] == "Cacau"
    assert cfg["pedido_retirada_data"] == "23/05/2026"
