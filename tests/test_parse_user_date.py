import pytest

from app.tools.writes.fornada import _parse_user_date, _validate_create_batch_args


def test_parse_br_date():
    d = _parse_user_date("10/06/2026", "data_inicio")
    assert d.isoformat() == "2026-06-10"


def test_parse_iso_date():
    d = _parse_user_date("2026-06-10", "data_inicio")
    assert d.isoformat() == "2026-06-10"


def test_validate_create_batch_accepts_br_format():
    di, df = _validate_create_batch_args(
        {"data_inicio": "10/06/2026", "data_fim": "16/06/2026"}
    )
    assert di == "2026-06-10"
    assert df == "2026-06-16"
