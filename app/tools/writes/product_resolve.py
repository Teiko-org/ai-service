"""Resolve nomes de produtos de fornada para produto_fornada_id (catalogo Java)."""

from __future__ import annotations

from typing import Any

import httpx

from app.tools.writes._helpers import WriteToolError, get_json
from app.tools.writes.text_match import normalize_text as _normalize_text

_FORNADA_TYPE = "FORNADA"
_CATALOG_PATH = "/dashboard/produtosCadastrados"

# Singular/plural e abreviacoes comuns no chat.
_ALIASES: dict[str, str] = {
    "pao": "pao",
    "paes": "pao",
    "pão": "pao",
    "pães": "pao",
    "frances": "frances",
    "francês": "frances",
    "croissant": "croissant",
    "croassant": "croissant",
}


def _alias_tokens(normalized: str) -> str:
    parts = normalized.split()
    mapped = [_ALIASES.get(p, p) for p in parts]
    return " ".join(mapped)


async def fetch_fornada_catalog(
    client: httpx.AsyncClient, base_url: str, token: str | None
) -> list[dict[str, Any]]:
    payload = await get_json(client, f"{base_url}{_CATALOG_PATH}", token)
    if isinstance(payload, dict):
        items = payload.get("data")
        if isinstance(items, list):
            return [p for p in items if isinstance(p, dict)]
        return []
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, dict)]
    return []


def _is_active_catalog_item(item: dict) -> bool:
    ativo = item.get("ativo")
    if ativo is None:
        return True
    if isinstance(ativo, bool):
        return ativo
    if isinstance(ativo, (int, float)):
        return bool(ativo)
    return str(ativo).strip().lower() in ("true", "1", "sim", "s")


def _fornada_products(catalog: list[dict]) -> list[dict]:
    out: list[dict] = []
    for item in catalog:
        if str(item.get("tipo") or "").upper() != _FORNADA_TYPE:
            continue
        if not _is_active_catalog_item(item):
            continue
        pid = item.get("id")
        nome = item.get("nome") or item.get("produto")
        if isinstance(pid, int) and pid > 0 and nome:
            out.append(item)
    return out


def _product_sort_key(prod: dict) -> tuple:
    pid = prod.get("id")
    pid_num = pid if isinstance(pid, int) else 0
    return (not _is_active_catalog_item(prod), -pid_num)


def _coerce_quantity(value: Any, line_index: int) -> int:
    if isinstance(value, bool):
        raise WriteToolError(f"Linha {line_index}: quantidade invalida.")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, int) and value >= 1:
        return value
    raise WriteToolError(f"Linha {line_index}: quantidade deve ser inteiro >= 1.")


def _line_product_name(line: dict) -> str | None:
    for key in ("produto_nome", "nome", "produto", "name", "item", "descricao"):
        raw = line.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _match_score(query_norm: str, nome_norm: str) -> float:
    if query_norm == nome_norm:
        return 100.0
    if query_norm in nome_norm or nome_norm in query_norm:
        return 80.0
    q_tokens = set(query_norm.split())
    n_tokens = set(nome_norm.split())
    if not q_tokens:
        return 0.0
    overlap = len(q_tokens & n_tokens) / len(q_tokens)
    return overlap * 60.0


def resolve_product_name(
    name: str, catalog: list[dict], *, line_index: int
) -> tuple[int, str]:
    products = _fornada_products(catalog)
    if not products:
        raise WriteToolError(
            "Nao ha produtos de fornada cadastrados no sistema para resolver o nome."
        )

    query = _alias_tokens(_normalize_text(name))
    if not query:
        raise WriteToolError(f"Linha {line_index}: nome do produto vazio.")

    scored: list[tuple[float, dict]] = []
    for prod in products:
        nome = str(prod.get("nome") or prod.get("produto") or "")
        nome_norm = _alias_tokens(_normalize_text(nome))
        score = _match_score(query, nome_norm)
        if score >= 40.0:
            scored.append((score, prod))

    scored.sort(key=lambda x: (-x[0], *_product_sort_key(x[1])))

    if not scored:
        nomes = ", ".join(
            sorted({str(p.get("nome", "")) for p in products if p.get("nome")})[:12]
        )
        raise WriteToolError(
            f"Linha {line_index}: nao encontrei produto de fornada parecido com "
            f"'{name}'. Cadastrados (ex.): {nomes}."
        )

    best_score, best = scored[0]
    close = [(s, p) for s, p in scored if s >= best_score - 5]
    if len(close) > 1:
        norm_names = {
            _alias_tokens(_normalize_text(str(p.get("nome") or p.get("produto") or "")))
            for _, p in close
        }
        if len(norm_names) == 1:
            # Cadastro duplicado (mesmo nome, varios ids): usa o ativo com maior id.
            best = sorted((p for _, p in close), key=_product_sort_key)[0]
        else:
            seen: set[str] = set()
            labels: list[str] = []
            for _, p in close[:6]:
                label = str(p.get("nome") or p.get("produto") or "")
                if label in seen:
                    continue
                seen.add(label)
                labels.append(f"{label} (id {p.get('id')})")
            options = ", ".join(labels)
            raise WriteToolError(
                f"Linha {line_index}: '{name}' ficou ambiguo entre produtos diferentes. "
                f"Qual destes? {options}"
            )

    pid = best.get("id")
    display = str(best.get("nome") or name)
    if not isinstance(pid, int) or pid <= 0:
        raise WriteToolError(f"Linha {line_index}: produto '{display}' sem id valido.")
    return pid, display


async def resolve_batch_lines(
    raw_lines: list,
    client: httpx.AsyncClient,
    base_url: str,
    token: str | None,
) -> list[dict]:
    catalog = await fetch_fornada_catalog(client, base_url, token)
    normalized: list[dict] = []

    for idx, line in enumerate(raw_lines, start=1):
        if not isinstance(line, dict):
            raise WriteToolError(f"Linha {idx} invalida: deve ser objeto.")

        qty = _coerce_quantity(line.get("quantidade"), idx)

        pid = line.get("produto_fornada_id")
        if isinstance(pid, float) and pid.is_integer():
            pid = int(pid)
        nome = _line_product_name(line)

        if isinstance(pid, int) and pid > 0:
            display = str(nome).strip() if isinstance(nome, str) and nome.strip() else ""
            if nome:
                resolved_id, display = resolve_product_name(
                    str(nome), catalog, line_index=idx
                )
                if resolved_id != pid:
                    raise WriteToolError(
                        f"Linha {idx}: id {pid} nao bate com o nome '{nome}' "
                        f"(cadastro aponta id {resolved_id} — {display})."
                    )
            if not display:
                for prod in _fornada_products(catalog):
                    if prod.get("id") == pid:
                        display = str(prod.get("nome") or f"produto #{pid}")
                        break
                if not display:
                    display = f"produto #{pid}"
            normalized.append(
                {
                    "produto_fornada_id": pid,
                    "quantidade": qty,
                    "produto_nome": display,
                }
            )
            continue

        if isinstance(nome, str) and nome.strip():
            resolved_id, display = resolve_product_name(
                nome.strip(), catalog, line_index=idx
            )
            normalized.append(
                {
                    "produto_fornada_id": resolved_id,
                    "quantidade": qty,
                    "produto_nome": display,
                }
            )
            continue

        raise WriteToolError(
            f"Linha {idx}: informe produto_nome (ex.: 'Pao Frances') ou "
            "produto_fornada_id."
        )

    return normalized


def canonical_lines_for_hmac(lines: list[dict]) -> list[dict]:
    """Somente ids + quantidades, ordem estavel para token HMAC."""
    slim = [
        {"produto_fornada_id": ln["produto_fornada_id"], "quantidade": ln["quantidade"]}
        for ln in lines
    ]
    return sorted(slim, key=lambda x: (x["produto_fornada_id"], x["quantidade"]))
