import asyncio
import json
import logging
import re

import google.genai as genai

from app.config import settings
from app.core.gemini import get_client
from app.core.model_manager import model_manager
from app.core.prompts import SYSTEM_PROMPT, INSIGHTS_PROMPT
from app.tools.registry import TOOL_DECLARATIONS, execute_tool

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5
MAX_FALLBACK_ATTEMPTS = 3

_RETRY_DELAY_RE = re.compile(r"retryDelay.*?(\d+(?:\.\d+)?)\s*s", re.IGNORECASE)


class RateLimitError(RuntimeError):
    """All Gemini models quota exceeded."""


def _extract_retry_seconds(exc: genai.errors.APIError) -> float | None:
    match = _RETRY_DELAY_RE.search(str(exc))
    return float(match.group(1)) if match else None


class CarambolosAssistant:

    def __init__(self, auth_token: str | None = None):
        self.client = get_client()
        self.backend_url = settings.carambolos_api_url
        self.auth_token = auth_token

    def _build_history_contents(self, history: list[dict]) -> list:
        contents = []
        for msg in history:
            role = "user" if msg["role"] == "user" else "model"
            contents.append(
                genai.types.Content(
                    role=role,
                    parts=[genai.types.Part.from_text(text=msg["content"])],
                )
            )
        return contents

    async def _generate_with_fallback(self, contents, config) -> tuple:
        """Try generating content, falling back to other models on 429/404."""
        model = model_manager.get_model()
        last_exc = None

        for attempt in range(MAX_FALLBACK_ATTEMPTS):
            try:
                logger.info("Usando modelo: %s (tentativa %d)", model, attempt + 1)
                response = await self.client.aio.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
                return response, model
            except genai.errors.APIError as exc:
                last_exc = exc

                if exc.code == 429:
                    retry_after = _extract_retry_seconds(exc)
                    fallback = model_manager.mark_rate_limited(model, retry_after)
                elif exc.code == 404:
                    logger.warning("Modelo %s nao encontrado, pulando...", model)
                    fallback = model_manager.mark_rate_limited(model, 3600)
                else:
                    raise RuntimeError(f"Falha na comunicacao com a IA: {exc.message}") from exc

                if fallback is None:
                    raise RateLimitError(
                        "Todos os modelos estao temporariamente indisponiveis. "
                        "Aguarde um momento e tente novamente."
                    ) from exc

                model = fallback

        raise RateLimitError(
            "Todos os modelos estao temporariamente indisponiveis. "
            "Aguarde um momento e tente novamente."
        ) from last_exc

    async def ask(self, question: str, history: list[dict] | None = None) -> dict:
        tools_used: list[str] = []

        config = genai.types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[genai.types.Tool(function_declarations=TOOL_DECLARATIONS)],
        )

        contents = self._build_history_contents(history or [])
        contents.append(
            genai.types.Content(
                role="user",
                parts=[genai.types.Part.from_text(text=question)],
            )
        )

        response, model = await self._generate_with_fallback(contents, config)

        for _ in range(MAX_TOOL_ROUNDS):
            if not response.candidates or not response.candidates[0].content.parts:
                break

            function_calls = [
                part
                for part in response.candidates[0].content.parts
                if part.function_call
            ]

            if not function_calls:
                break

            function_responses = []
            for fc in function_calls:
                tool_name = fc.function_call.name
                tool_args = dict(fc.function_call.args) if fc.function_call.args else {}
                tools_used.append(tool_name)

                logger.info("Chamando tool: %s(%s)", tool_name, tool_args)
                result = await execute_tool(
                    tool_name, tool_args, self.backend_url, self.auth_token
                )

                function_responses.append(
                    genai.types.Part.from_function_response(
                        name=tool_name, response={"result": result}
                    )
                )

            contents.append(response.candidates[0].content)
            contents.append(
                genai.types.Content(role="user", parts=function_responses)
            )

            response, model = await self._generate_with_fallback(contents, config)

        if not response.candidates or not response.candidates[0].content.parts:
            return {"answer": "Nao foi possivel gerar uma resposta no momento.", "tools_used": tools_used}

        answer = response.candidates[0].content.parts[0].text
        return {"answer": answer, "tools_used": tools_used}

    async def generate_insights(self, context: str = "dashboard_main") -> list[dict]:
        tool_sets = {
            "dashboard_main": [
                ("get_orders_count", {}),
                ("get_top_products", {}),
                ("get_recent_orders", {}),
                ("get_pending_doughs", {}),
                ("get_upcoming_deliveries", {"days_ahead": 7}),
            ],
            "production": [
                ("get_pending_doughs", {}),
                ("get_pending_fillings", {}),
                ("get_upcoming_deliveries", {"days_ahead": 3}),
            ],
            "batches": [
                ("get_latest_batch_kpi", {}),
                ("get_batch_kpi_by_period", {"period_type": "MES"}),
                ("get_batch_orders_by_period", {"period_type": "MES"}),
            ],
        }

        tools_to_call = tool_sets.get(context, tool_sets["dashboard_main"])

        tasks = [
            execute_tool(name, args, self.backend_url, self.auth_token)
            for name, args in tools_to_call
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        collected_data = {}
        for (tool_name, _), result in zip(tools_to_call, results):
            if isinstance(result, Exception):
                logger.warning("Tool %s falhou no insights: %s", tool_name, result)
                collected_data[tool_name] = {"error": str(result)}
            else:
                collected_data[tool_name] = result

        prompt = (
            f"{INSIGHTS_PROMPT}\n\n"
            f"Dados coletados do sistema:\n"
            f"{json.dumps(collected_data, ensure_ascii=False, default=str)}"
        )

        config = genai.types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
        )

        response, _ = await self._generate_with_fallback(prompt, config)

        if not response.candidates or not response.candidates[0].content.parts:
            return [{"type": "alert", "priority": "high", "title": "Erro", "message": "Nao foi possivel gerar insights."}]

        raw_text = response.candidates[0].content.parts[0].text

        try:
            cleaned = raw_text.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1]
                cleaned = cleaned.rsplit("```", 1)[0]
            insights = json.loads(cleaned)
            if isinstance(insights, list):
                return insights
        except (json.JSONDecodeError, IndexError):
            logger.warning("Falha ao parsear insights JSON, retornando como texto")

        return [
            {
                "type": "trend",
                "priority": "medium",
                "title": "Analise geral",
                "message": raw_text,
            }
        ]
