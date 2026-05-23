"""Parsing de datas vindas do usuario / modelo (BR ou ISO)."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.tools.writes._helpers import WriteToolError

_BR_DATE_RE = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$")


def parse_user_date(value: Any, field: str) -> date:
    """Aceita yyyy-MM-dd ou dd/MM/yyyy."""
    if not isinstance(value, str):
        raise WriteToolError(
            f"Campo {field} deve ser uma data (yyyy-MM-dd ou dd/MM/yyyy)."
        )
    text = value.strip()
    br = _BR_DATE_RE.match(text)
    if br:
        day, month, year = int(br.group(1)), int(br.group(2)), int(br.group(3))
        try:
            return date(year, month, day)
        except ValueError as exc:
            raise WriteToolError(f"Campo {field} invalido: '{value}'.") from exc
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise WriteToolError(
            f"Campo {field} invalido: '{value}' (use yyyy-MM-dd ou dd/MM/yyyy)."
        ) from exc
