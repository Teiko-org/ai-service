import logging
import re

from fastapi import APIRouter, Request, HTTPException

from app.api.deps import sanitize_input, check_prompt_injection, check_content_policy, validate_auth_token
from app.core.alerts import get_cached_alerts, refresh_alerts_now
from app.core.assistant import CarambolosAssistant, RateLimitError
from app.core.cache import cache
from app.core.limiter import limiter
from app.core.model_manager import model_manager
from app.core.sessions import session_store
from app.models.schemas import (
    Alert,
    AlertsResponse,
    AskRequest,
    AskResponse,
    Attachment,
    InsightRequest,
    InsightsResponse,
    Insight,
    SuggestedPrompt,
    SuggestedPromptsResponse,
    HealthResponse,
)
from app.tools.reports import REPORT_TOOL_NAME, REPORT_ENDPOINT, REPORT_FILENAME

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")

_REPORT_INTENT_RE = re.compile(
    r"\b(relat[oó]rio|pdf|exportar|exporta[cç][aã]o|documento|baixar|download|arquivo|imprimir)\b",
    re.IGNORECASE,
)


def _user_requested_report(question: str) -> bool:
    return bool(_REPORT_INTENT_RE.search(question or ""))


@router.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(status="ok", version="1.0.0")


@router.post("/ask", response_model=AskResponse)
@limiter.limit("15/minute")
async def ask_question(body: AskRequest, request: Request):
    token = await validate_auth_token(request)

    question = sanitize_input(body.question)
    check_prompt_injection(question)
    check_content_policy(question)

    session = session_store.get_or_create(body.session_id)
    history = session_store.get_history(session.id, limit=10)

    assistant = CarambolosAssistant(auth_token=token)

    try:
        result = await assistant.ask(question, history=history)
    except RateLimitError as exc:
        logger.warning("Rate limit Gemini: %s", exc)
        raise HTTPException(status_code=429, detail=str(exc))
    except Exception as exc:
        logger.error("Erro no assistente: %s", exc)
        raise HTTPException(status_code=500, detail="Erro ao processar a pergunta.")

    session_store.append(session.id, "user", question)
    session_store.append(session.id, "assistant", result["answer"])

    attachments: list[Attachment] = []
    should_attach_report = (
        REPORT_TOOL_NAME in result["tools_used"]
        or _user_requested_report(question)
    )
    if should_attach_report:
        if REPORT_TOOL_NAME not in result["tools_used"]:
            logger.info(
                "Modelo nao chamou %s mas pergunta pediu relatorio; anexando PDF por fallback.",
                REPORT_TOOL_NAME,
            )
        attachments.append(
            Attachment(
                type="pdf_report",
                label="Baixar relatório de insights",
                endpoint=REPORT_ENDPOINT,
                filename=REPORT_FILENAME,
            )
        )

    return AskResponse(
        answer=result["answer"],
        tools_used=result["tools_used"],
        session_id=session.id,
        attachments=attachments,
    )


@router.post("/insights", response_model=InsightsResponse)
@limiter.limit("10/minute")
async def generate_insights(body: InsightRequest, request: Request):
    token = await validate_auth_token(request)

    cache_key = f"insights:{body.context}"
    cached = cache.get(cache_key)
    if cached is not None:
        logger.info("Insights servidos do cache para contexto: %s", body.context)
        return cached

    assistant = CarambolosAssistant(auth_token=token)

    try:
        raw_insights = await assistant.generate_insights(body.context)
    except RateLimitError as exc:
        logger.warning("Rate limit Gemini (insights): %s", exc)
        raise HTTPException(status_code=429, detail=str(exc))
    except Exception as exc:
        logger.error("Erro ao gerar insights: %s", exc)
        raise HTTPException(status_code=500, detail="Erro ao gerar insights.")

    insights = [
        Insight(
            type=i.get("type", "trend"),
            priority=i.get("priority", "medium"),
            title=i.get("title", "Insight"),
            message=i.get("message", ""),
        )
        for i in raw_insights
    ]

    response = InsightsResponse(insights=insights)
    cache.set(cache_key, response, ttl=300)
    return response


@router.get("/alerts", response_model=AlertsResponse)
@limiter.limit("30/minute")
async def get_alerts(request: Request, refresh: bool = False):
    token = await validate_auth_token(request)

    cached = None if refresh else get_cached_alerts()
    if cached is None:
        try:
            cached = await refresh_alerts_now(token=token)
        except Exception as exc:  # noqa: BLE001
            logger.error("Falha ao gerar alertas: %s", exc)
            raise HTTPException(status_code=500, detail="Erro ao gerar alertas.")

    alerts = [Alert(**a) for a in cached.get("alerts", [])]
    return AlertsResponse(generated_at=cached.get("generated_at"), alerts=alerts)


SUGGESTED_PROMPTS = [
    SuggestedPrompt(
        label="Como estão os cancelamentos?",
        prompt=(
            "Gostaria de saber como estao os cancelamentos recentes. "
            "Qual a taxa de cancelamento atual e se houve alguma mudanca significativa?"
        ),
        icon="alert-circle",
    ),
    SuggestedPrompt(
        label="Resumo dos pedidos",
        prompt=(
            "Me de um resumo geral dos pedidos: quantos estao pendentes, pagos, "
            "concluidos e cancelados? Quais os produtos mais pedidos?"
        ),
        icon="clipboard-list",
    ),
    SuggestedPrompt(
        label="Fornadas este mês",
        prompt=(
            "Como foi o desempenho das fornadas este mes? "
            "Qual o percentual de aproveitamento e valor arrecadado?"
        ),
        icon="flame",
    ),
    SuggestedPrompt(
        label="Produção pendente",
        prompt=(
            "Quais massas e recheios estao pendentes para producao? "
            "Existem pedidos com entrega proxima que precisam de atencao?"
        ),
        icon="clock",
    ),
    SuggestedPrompt(
        label="Tendências de vendas",
        prompt=(
            "Quais as tendencias de vendas dos ultimos meses? "
            "Algum produto esta crescendo ou caindo em demanda?"
        ),
        icon="trending-up",
    ),
    SuggestedPrompt(
        label="Principais clientes",
        prompt=(
            "Com base nos pedidos recentes, quem sao os clientes que mais fizeram pedidos? "
            "Liste os nomes dos clientes mais frequentes com a quantidade de pedidos de cada um."
        ),
        icon="users",
    ),
    SuggestedPrompt(
        label="Gerar relatório PDF",
        prompt="Gere um relatorio em PDF com os insights de pedidos.",
        icon="file-down",
    ),
    SuggestedPrompt(
        label="Pedidos para amanhã",
        prompt="Quais pedidos de bolo estao com entrega para amanha? Liste com cliente, status e valor.",
        icon="calendar-clock",
    ),
    SuggestedPrompt(
        label="Próxima fornada",
        prompt="Qual a proxima fornada agendada e quais produtos vao ser oferecidos?",
        icon="flame",
    ),
]


@router.get("/suggested-prompts", response_model=SuggestedPromptsResponse)
async def get_suggested_prompts():
    return SuggestedPromptsResponse(prompts=SUGGESTED_PROMPTS)


@router.get("/models-status")
async def get_models_status():
    return model_manager.get_status()
