from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str


class WriteConfirmationCommit(BaseModel):
    """Direct commit for a pending write preview (G13 — app Confirm button)."""

    action: str = Field(..., min_length=1, max_length=80)
    confirm_token: str = Field(..., min_length=10, max_length=256)
    payload: dict = Field(default_factory=dict)


class PendingConfirmation(BaseModel):
    action: str
    confirm_token: str
    payload: dict = Field(default_factory=dict)
    message: str = ""


class AskRequest(BaseModel):
    question: str = Field(
        ..., min_length=1, max_length=1000,
        description="Pergunta em linguagem natural sobre o negocio"
    )
    session_id: str | None = None
    confirmation: WriteConfirmationCommit | None = Field(
        default=None,
        description="Quando preenchido, executa o commit da acao pendente sem Gemini.",
    )


class Attachment(BaseModel):
    type: str = Field(..., description="Tipo do anexo: 'pdf_report', etc.")
    label: str = Field(..., description="Texto exibido no botao do frontend.")
    endpoint: str = Field(..., description="Caminho relativo no backend Carambolos.")
    filename: str = Field(..., description="Nome sugerido do arquivo no download.")


class AskResponse(BaseModel):
    answer: str
    tools_used: list[str]
    session_id: str
    attachments: list[Attachment] = Field(default_factory=list)
    pending_confirmation: PendingConfirmation | None = None


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


class Alert(BaseModel):
    type: str = Field(..., description="Categoria do alerta: production, delivery, cancellation, batch.")
    priority: str = Field(..., description="high | medium | low.")
    title: str
    message: str
    metadata: dict = Field(default_factory=dict)


class AlertsResponse(BaseModel):
    generated_at: str | None = Field(
        default=None,
        description="Timestamp ISO-8601 (UTC) de quando os alertas foram calculados.",
    )
    alerts: list[Alert] = Field(default_factory=list)
