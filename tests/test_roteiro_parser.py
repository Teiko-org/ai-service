"""Substituicao de placeholders do roteiro (apply_config)."""

from scripts.roteiro_parser import RoteiroStep, apply_config


def _step(question: str) -> list[RoteiroStep]:
    return [
        RoteiroStep(
            step_id="1.0",
            section="test",
            title="t",
            question=question,
        )
    ]


def test_delivery_date_iso_not_corrupted_by_order_id():
    cfg = {
        "order_id_bolo": 3020,
        "order_id_fornada": 1796,
        "delivery_date": "23/05/2026",
        "delivery_date_iso": "2026-05-23",
    }
    q = apply_config(
        _step("Quais pedidos de bolo tem entrega em 2026-05-15?"),
        cfg,
    )[0].question
    assert q == "Quais pedidos de bolo tem entrega em 2026-05-23?"
    assert "3020" not in q


def test_pedido_de_fornada_substituted():
    cfg = {"order_id_fornada": 1796}
    q = apply_config(
        _step("Me mostra os detalhes do pedido de fornada 3"),
        cfg,
    )[0].question
    assert "pedido de fornada 1796" in q


def test_pedido_de_bolo_substituted():
    cfg = {"order_id_bolo": 3020}
    q = apply_config(
        _step("Me mostra os detalhes completos do pedido de bolo 15"),
        cfg,
    )[0].question
    assert "pedido de bolo 3020" in q


def test_fornadas_close_ids_substituted():
    cfg = {"batch_close_1": 16, "batch_close_2": 17}
    q = apply_config(_step("Encerre as fornadas 10 e 11"), cfg)[0].question
    assert q == "Encerre as fornadas 16 e 17"


def test_pedido_bolo_massa_and_date():
    cfg = {
        "massa_nome": "Cacau Expresso",
        "pedido_retirada_data": "23/05/2026",
    }
    q = apply_config(
        _step(
            "Cria pedido de bolo: cliente Maria, massa Cacau, "
            "retirada dia 10/06/2026 as 17:00"
        ),
        cfg,
    )[0].question
    assert "massa Cacau Expresso" in q
    assert "retirada dia 23/05/2026" in q
