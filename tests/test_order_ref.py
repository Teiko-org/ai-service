"""Testes de parse do numero de pedido (resumo) — #42, pedido 42, etc."""

import pytest

from app.tools.order_ref import parse_resumo_order_id, parse_resumo_order_id_list


@pytest.mark.parametrize(
    "raw,expected",
    [
        (42, 42),
        (1, 1),
        ("42", 42),
        ("#42", 42),
        ("  #7 ", 7),
        ("pedido 99", 99),
        ("Pedido #12", 12),
        ("nº 5", 5),
        ("no 8", 8),
    ],
)
def test_parse_resumo_order_id_ok(raw, expected):
    assert parse_resumo_order_id(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [None, "", "  ", "abc", "pedido", "#", -1, 0, 3.5, True, False],
)
def test_parse_resumo_order_id_rejects(raw):
    with pytest.raises(ValueError):
        parse_resumo_order_id(raw)


def test_parse_resumo_order_id_list_ok():
    assert parse_resumo_order_id_list([1, "#2", "pedido 3"]) == [1, 2, 3]


def test_parse_resumo_order_id_list_empty_rejects():
    with pytest.raises(ValueError):
        parse_resumo_order_id_list([])
