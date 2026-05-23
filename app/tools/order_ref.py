"""Normalizacao do numero de pedido visivel no app / WhatsApp para o id do resumo.

No backend, o "Número do pedido" na mensagem de WhatsApp e o mesmo `id` da
entidade `resumo_pedido` (ex.: #42). O usuario pode citar 42, #42, pedido 42,
etc. — convertemos tudo para inteiro antes de chamar a API.
"""

from __future__ import annotations

import re

# Aceita: 42, #42, " 42 ", "pedido 42", "Pedido #42", "nº 7", "no 7"
_ORDER_PATTERNS = (
    re.compile(r"^\s*pedido\s*#?\s*(\d+)\s*$", re.IGNORECASE),
    re.compile(r"^\s*n[ºo°.]\s*\.?\s*(\d+)\s*$", re.IGNORECASE),
    re.compile(r"^\s*#?\s*(\d+)\s*$"),
)


def parse_resumo_order_id(value: object) -> int:
    """Interpreta valor vindo do modelo ou do usuario e devolve o id do resumo."""
    if value is None:
        raise ValueError("Numero do pedido e obrigatorio.")

    if isinstance(value, bool):
        raise ValueError("Numero do pedido invalido.")

    if isinstance(value, int):
        if value <= 0:
            raise ValueError("Numero do pedido deve ser um inteiro positivo.")
        return value

    if isinstance(value, float):
        if value <= 0 or not value.is_integer():
            raise ValueError("Numero do pedido invalido.")
        return int(value)

    s = str(value).strip().replace("\u00a0", " ")
    if not s:
        raise ValueError("Numero do pedido e obrigatorio.")

    for pat in _ORDER_PATTERNS:
        m = pat.match(s)
        if m:
            n = int(m.group(1))
            if n <= 0:
                raise ValueError("Numero do pedido deve ser positivo.")
            return n

    raise ValueError(
        "Nao entendi o numero do pedido. Use o que aparece no app como "
        '"Pedido #123" ou no WhatsApp (ex.: 123, #123, pedido 123).'
    )


_ORDER_IDS_IN_TEXT_RE = re.compile(
    r"(?:pedido\s*#?\s*|#\s*)(\d+)",
    re.IGNORECASE,
)


def extract_order_ids_from_text(text: str) -> list[int]:
    """Extrai numeros de pedido (#3019, pedido 3019) de uma frase do usuario."""
    if not text or not str(text).strip():
        return []
    seen: set[int] = set()
    out: list[int] = []
    for m in _ORDER_IDS_IN_TEXT_RE.finditer(str(text)):
        n = int(m.group(1))
        if n > 0 and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def parse_resumo_order_id_list(values: object) -> list[int]:
    """Lista de ids de resumo (para WhatsApp consolidado, etc.)."""
    if values is None:
        raise ValueError("Lista de pedidos e obrigatoria.")
    if not isinstance(values, list):
        raise ValueError("Informe uma lista de numeros de pedido.")
    out: list[int] = []
    for i, item in enumerate(values):
        try:
            out.append(parse_resumo_order_id(item))
        except ValueError as exc:
            raise ValueError(f"Item {i + 1}: {exc}") from exc
    if not out:
        raise ValueError("Informe pelo menos um numero de pedido.")
    return out
