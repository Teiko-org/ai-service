"""Extracao de numeros de pedido em texto livre."""

from app.tools.order_ref import extract_order_ids_from_text


def test_extract_multiple_order_hashes():
    text = (
        "Gera a mensagem de WhatsApp de confirmacao para os pedidos "
        "#3019 e #1796"
    )
    assert extract_order_ids_from_text(text) == [3019, 1796]
