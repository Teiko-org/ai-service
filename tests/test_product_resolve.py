import pytest

from app.tools.writes.product_resolve import (
    canonical_lines_for_hmac,
    resolve_product_name,
)


_CATALOG = [
    {"id": 10, "nome": "Pao Frances", "tipo": "FORNADA", "ativo": True},
    {"id": 11, "nome": "Croissant", "tipo": "FORNADA", "ativo": True},
    {"id": 99, "nome": "Bolo X", "tipo": "BOLO", "ativo": True},
]


def test_resolve_pao_frances_by_partial_name():
    pid, name = resolve_product_name("paes", _CATALOG, line_index=1)
    assert pid == 10
    assert "Pao" in name


def test_resolve_croissant():
    pid, _ = resolve_product_name("croissant", _CATALOG, line_index=1)
    assert pid == 11


def test_resolve_unknown_raises():
    with pytest.raises(Exception, match="nao encontrei"):
        resolve_product_name("Sushi", _CATALOG, line_index=1)


def test_resolve_duplicate_same_name_picks_highest_active_id():
    catalog = [
        {"id": 2, "nome": "Pao Frances", "tipo": "FORNADA", "ativo": True},
        {"id": 6, "nome": "Pao Frances", "tipo": "FORNADA", "ativo": True},
        {"id": 14, "nome": "Pao Frances", "tipo": "FORNADA", "ativo": True},
        {"id": 11, "nome": "Croissant", "tipo": "FORNADA", "ativo": True},
    ]
    pid, _ = resolve_product_name("paes franceses", catalog, line_index=1)
    assert pid == 14


def test_canonical_lines_sorted():
    lines = [
        {"produto_fornada_id": 11, "quantidade": 2, "produto_nome": "Croissant"},
        {"produto_fornada_id": 10, "quantidade": 5, "produto_nome": "Pao"},
    ]
    canon = canonical_lines_for_hmac(lines)
    assert canon[0]["produto_fornada_id"] == 10
    assert "produto_nome" not in canon[0]
