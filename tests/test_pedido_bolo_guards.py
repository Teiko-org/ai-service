from datetime import date, timedelta

import pytest

from app.tools.writes._helpers import WriteToolError, coerce_positive_int
from app.tools.writes.pedido_bolo import (
    _normalize_tamanho,
    _preview_message,
    _validate_and_canonicalize,
)
from app.tools.writes.fornada import _parse_user_date


def test_coerce_positive_int_accepts_float():
    assert coerce_positive_int(3.0, "massa_id") == 3


def test_parse_user_date_br_format():
    d = _parse_user_date("10/06/2026", "data_previsao_entrega")
    assert d == date(2026, 6, 10)


def test_normalize_tamanho_from_digits():
    assert _normalize_tamanho("12") == "TAMANHO_12"


def test_validate_accepts_br_date_and_float_massa():
    entrega = date.today() + timedelta(days=5)
    future = entrega.strftime("%d/%m/%Y")
    validated, canonical = _validate_and_canonicalize(
        {
            "massa_id": 1.0,
            "formato": "CIRCULO",
            "tamanho": "12",
            "recheio_unitario_id": 2.0,
            "nome_cliente": "Teste",
            "telefone_cliente": "11999998888",
            "tipo_entrega": "RETIRADA",
            "data_previsao_entrega": future,
            "horario_retirada": "17:00",
        }
    )
    assert validated.massa_id == 1
    assert validated.tamanho == "TAMANHO_12"
    assert canonical["data_previsao_entrega"] == entrega.isoformat()


def test_preview_includes_massa_and_recheio():
    entrega = date.today() + timedelta(days=5)
    validated, _ = _validate_and_canonicalize(
        {
            "massa_id": 1,
            "formato": "CIRCULO",
            "tamanho": "12",
            "recheio_unitario_id": 2,
            "nome_cliente": "Maria",
            "telefone_cliente": "11999998888",
            "tipo_entrega": "RETIRADA",
            "data_previsao_entrega": entrega.strftime("%d/%m/%Y"),
            "horario_retirada": "17:00",
            "_catalog_labels": {"massa": "baunilha", "recheio": "Hugo"},
        }
    )
    msg = _preview_message(validated)
    assert "baunilha" in msg
    assert "Hugo" in msg
    assert "Maria" in msg
