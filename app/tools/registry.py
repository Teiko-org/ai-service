import logging
import re

from app.config import settings
from app.core.http_client import get_http_client
from app.tools import (
    actions,
    batches,
    catalog,
    dashboard,
    deep_orders,
    orders,
    products,
    production,
    reports,
)

logger = logging.getLogger(__name__)

TOOL_DECLARATIONS = (
    dashboard.DECLARATIONS
    + orders.DECLARATIONS
    + products.DECLARATIONS
    + production.DECLARATIONS
    + reports.DECLARATIONS
    + actions.DECLARATIONS
    + deep_orders.DECLARATIONS
    + batches.DECLARATIONS
    + catalog.DECLARATIONS
)

_EXECUTORS = {
    **{d.name: dashboard.execute for d in dashboard.DECLARATIONS},
    **{d.name: orders.execute for d in orders.DECLARATIONS},
    **{d.name: products.execute for d in products.DECLARATIONS},
    **{d.name: production.execute for d in production.DECLARATIONS},
    **{d.name: reports.execute for d in reports.DECLARATIONS},
    **{d.name: actions.execute for d in actions.DECLARATIONS},
    **{d.name: deep_orders.execute for d in deep_orders.DECLARATIONS},
    **{d.name: batches.execute for d in batches.DECLARATIONS},
    **{d.name: catalog.execute for d in catalog.DECLARATIONS},
}

# Phase-1 V3 writes (creation tools) are gated behind ENABLE_WRITE_TOOLS so
# the model can't even see them in read-only deployments. Startup in
# config.py already enforces CONFIRM_TOKEN_SECRET when this is on.
if settings.enable_write_tools:
    from app.tools.writes import fornada as writes_fornada

    TOOL_DECLARATIONS = TOOL_DECLARATIONS + writes_fornada.DECLARATIONS
    _EXECUTORS.update(
        {d.name: writes_fornada.execute for d in writes_fornada.DECLARATIONS}
    )


# Indirect prompt injection guard: customer-supplied DB fields (observacao,
# nomeCliente, ...) flow back to the model through read tools. Treat them as
# data, never as instructions. Patterns below get neutralized in-place.
_INSTRUCTION_PATTERNS = re.compile(
    r"("
    r"ignore\s+(as\s+|todas\s+|all\s+|previous\s+|anterior(es)?\s+|suas\s+|minhas\s+|essas\s+|the\s+)?(instrucoes|instruções|instructions|regras|rules|prompts?)"
    r"|esquec[aá]\s+(suas|tuas|as|todas)?\s*(instrucoes|instruções|regras)?"
    r"|forget\s+(your|all|previous)\s+(instructions|rules|prompts?)"
    r"|system\s*prompt"
    r"|atue\s+como"
    r"|act\s+as"
    r"|pretend\s+to\s+be"
    r"|jailbreak"
    r"|DAN\s+mode"
    r"|<\s*system\s*>"
    r"|\[\s*system\s*\]"
    r")",
    re.IGNORECASE,
)

# Conservative allowlist of DB string fields that come from end-user input.
# Only these get sanitized; trusted fields (status, IDs, ...) pass through.
_UNTRUSTED_STRING_KEYS = {
    "observacao",
    "observation",
    "observations",
    "nomeCliente",
    "nome_cliente",
    "nomeUsuario",
    "nome",
    "telefoneCliente",
    "telefone",
    "phone",
    "endereco",
    "endereço",
    "logradouro",
    "complemento",
    "descricao",
    "descrição",
    "mensagem",
    "comentario",
    "comentário",
}

_MAX_UNTRUSTED_STRING_LEN = 500


def _neutralize_instruction(text: str) -> str:
    return _INSTRUCTION_PATTERNS.sub(lambda m: f"[texto bloqueado: {m.group(0)[:6]}...]", text)


def sanitize_for_llm(value, key: str | None = None):
    """Strip prompt-injection patterns from untrusted DB-sourced strings."""
    if isinstance(value, dict):
        return {k: sanitize_for_llm(v, key=k) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_for_llm(item, key=key) for item in value]
    if isinstance(value, str) and key and key in _UNTRUSTED_STRING_KEYS:
        truncated = value[:_MAX_UNTRUSTED_STRING_LEN]
        return _neutralize_instruction(truncated)
    return value


async def execute_tool(
    name: str, args: dict, base_url: str, token: str | None = None
) -> dict:
    executor = _EXECUTORS.get(name)
    if not executor:
        logger.warning("Tool nao registrada: %s", name)
        return {"error": f"Tool '{name}' nao encontrada"}

    client = get_http_client()
    try:
        result = await executor(name, args, base_url, token, client)
        # PII guard: log tool name only, never argument values.
        logger.info("Tool executada: %s", name)
        return sanitize_for_llm(result) if isinstance(result, (dict, list)) else result
    except Exception as exc:
        logger.error("Erro ao executar tool %s: %s", name, exc)
        return {"error": f"Falha ao buscar dados: {str(exc)}"}
