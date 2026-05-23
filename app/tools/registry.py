import logging
import re

from app.config import settings
from app.core.http_client import get_http_client
from app.core.llm_present import prepare_tool_result_for_llm
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

# Nomes das tools V3 (writes) — usados para mensagem clara quando ENABLE_WRITE_TOOLS=false.
_WRITE_TOOL_NAMES = frozenset(
    {
        "create_batch",
        "add_batch_lines",
        "close_batch",
        "replace_active_batch",
        "create_pedido_bolo_full",
    }
)

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
    from app.tools.writes import router as writes_router

    TOOL_DECLARATIONS = TOOL_DECLARATIONS + writes_router.DECLARATIONS
    _EXECUTORS.update(
        {d.name: writes_router.execute for d in writes_router.DECLARATIONS}
    )
    logger.info(
        "V3 write tools ativas: %s",
        ", ".join(sorted(_WRITE_TOOL_NAMES)),
    )
else:
    logger.info(
        "V3 write tools desligadas (ENABLE_WRITE_TOOLS=false). "
        "Pedidos mark_order_* seguem ativos se CONFIRM_TOKEN_SECRET estiver definido."
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
        if name in _WRITE_TOOL_NAMES:
            return {
                "error": (
                    "Criacao de fornada/pedido pela IA esta desligada neste servidor. "
                    "No arquivo ai-service/.env defina ENABLE_WRITE_TOOLS=true "
                    "(e mantenha CONFIRM_TOKEN_SECRET), depois reinicie o uvicorn."
                )
            }
        return {"error": f"Tool '{name}' nao encontrada"}

    client = get_http_client()
    try:
        result = await executor(name, args, base_url, token, client)
        if isinstance(result, (dict, list)):
            result = prepare_tool_result_for_llm(name, result)
            result = sanitize_for_llm(result)
        # PII guard: log tool name only, never argument values.
        logger.info("Tool executada: %s", name)
        return result
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        logger.error("Erro ao executar tool %s: %s", name, detail, exc_info=True)
        return {
            "error": (
                f"Falha ao consultar o sistema ({detail}). "
                "Verifique se o backend Java esta no ar e tente de novo."
            )
        }
