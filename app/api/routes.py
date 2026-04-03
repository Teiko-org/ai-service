import logging

from fastapi import APIRouter, Request, HTTPException

from app.api.deps import sanitize_input, check_prompt_injection, validate_auth_token
from app.core.assistant import CarambolosAssistant
from app.core.limiter import limiter
from app.models.schemas import (
    AskRequest,
    AskResponse,
    InsightRequest,
    InsightsResponse,
    Insight,
    SuggestedPrompt,
    SuggestedPromptsResponse,
    HealthResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")


@router.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(status="ok", version="1.0.0")


@router.post("/ask", response_model=AskResponse)
@limiter.limit("15/minute")
async def ask_question(body: AskRequest, request: Request):
    token = await validate_auth_token(request)

    question = sanitize_input(body.question)
    check_prompt_injection(question)

    assistant = CarambolosAssistant(auth_token=token)

    try:
        result = await assistant.ask(question)
    except Exception as exc:
        logger.error("Erro no assistente: %s", exc)
        raise HTTPException(status_code=500, detail="Erro ao processar a pergunta.")

    return AskResponse(answer=result["answer"], tools_used=result["tools_used"])


@router.post("/insights", response_model=InsightsResponse)
@limiter.limit("10/minute")
async def generate_insights(body: InsightRequest, request: Request):
    token = await validate_auth_token(request)

    assistant = CarambolosAssistant(auth_token=token)

    try:
        raw_insights = await assistant.generate_insights(body.context)
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

    return InsightsResponse(insights=insights)


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
            "Quem sao os principais clientes da confeitaria? "
            "Quantos pedidos os clientes mais frequentes fizeram?"
        ),
        icon="users",
    ),
]


@router.get("/suggested-prompts", response_model=SuggestedPromptsResponse)
async def get_suggested_prompts():
    return SuggestedPromptsResponse(prompts=SUGGESTED_PROMPTS)
