from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(
        ..., min_length=3, max_length=1000,
        description="Pergunta em linguagem natural sobre o negocio"
    )
    session_id: str | None = None


class AskResponse(BaseModel):
    answer: str
    tools_used: list[str]


class InsightRequest(BaseModel):
    context: str = Field(
        default="dashboard_main",
        description="Contexto do dashboard: dashboard_main, production, batches"
    )


class Insight(BaseModel):
    type: str
    priority: str
    title: str
    message: str


class InsightsResponse(BaseModel):
    insights: list[Insight]


class SuggestedPrompt(BaseModel):
    label: str
    prompt: str
    icon: str | None = None


class SuggestedPromptsResponse(BaseModel):
    prompts: list[SuggestedPrompt]


class HealthResponse(BaseModel):
    status: str
    version: str
