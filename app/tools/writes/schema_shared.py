"""Schemas Gemini compartilhados entre tools de escrita V3."""

from __future__ import annotations

import google.genai as genai


def confirmed_param() -> genai.types.Schema:
    return genai.types.Schema(
        type=genai.types.Type.BOOLEAN,
        description=(
            "False (padrao) retorna apenas a previa para confirmacao; "
            "True executa de fato. NUNCA passe True sem confirmacao "
            "explicita do usuario em mensagem posterior a previa."
        ),
    )


def confirm_token_param() -> genai.types.Schema:
    return genai.types.Schema(
        type=genai.types.Type.STRING,
        description=(
            "Token retornado pela tool no preview (campo confirm_token). "
            "Obrigatorio quando confirmed=True. Mesmo valor exato da previa."
        ),
    )
