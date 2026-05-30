import asyncio
import json
import logging
import re

import google.genai as genai

from app.config import settings
from app.core.api_key_manager import api_key_manager
from app.core.gemini import get_client
from app.core.model_manager import model_manager
from app.core.prompts import SYSTEM_PROMPT, INSIGHTS_PROMPT
from app.tools.registry import TOOL_DECLARATIONS, execute_tool

logger = logging.getLogger(__name__)

# Read-only flows: keep the budget tight to bound Gemini quota usage.
# Write flows (V3 chains: catalog lookup -> preview -> commit -> wrap-up)
# legitimately need more rounds, so we promote the cap on demand.
MAX_TOOL_ROUNDS_READ = 5
MAX_TOOL_ROUNDS_WRITE = 8
# Alias kept for backwards compat with existing tests/imports.
MAX_TOOL_ROUNDS = MAX_TOOL_ROUNDS_READ

def _max_fallback_attempts() -> int:
    key_count = max(1, len(settings.gemini_api_keys_list))
    model_count = max(1, len(model_manager._models))
    return max(9, key_count * model_count)

_RETRY_DELAY_RE = re.compile(r"retryDelay.*?(\d+(?:\.\d+)?)\s*s", re.IGNORECASE)

_WRITE_TOOL_PREFIXES = ("create_", "add_", "close_", "replace_", "update_", "delete_")


def _is_invalid_api_key_error(exc: genai.errors.APIError) -> bool:
    msg = (getattr(exc, "message", None) or str(exc)).lower()
    return exc.code in (400, 401, 403) and "api key" in msg


def _is_write_tool(name: str) -> bool:
    # Local import: avoid circular dependency at module load time.
    from app.tools import actions
    if name in actions.ACTION_TOOL_NAMES and name != "generate_whatsapp_message":
        return True
    return name.startswith(_WRITE_TOOL_PREFIXES)


_EMPTY_RESPONSE_NUDGE = (
    "A resposta anterior veio vazia. Use as ferramentas do sistema para buscar "
    "dados reais (ex.: get_orders_by_dough, get_order_summaries, get_doughs_catalog) "
    "e responda em texto claro, sem Markdown."
)


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

    @staticmethod
    def _extract_text_from_response(response) -> str:
        """Texto final do usuario (ignora partes 'thought' do Gemini 2.5)."""
        sdk_text = getattr(response, "text", None)
        if isinstance(sdk_text, str) and sdk_text.strip():
            return sdk_text.strip()

        if not response.candidates:
            return ""
        content = response.candidates[0].content
        if not content or not content.parts:
            return ""

        chunks: list[str] = []
        for part in content.parts:
            if getattr(part, "thought", False) is True:
                continue
            if isinstance(part.text, str) and part.text:
                chunks.append(part.text)
        return "\n".join(chunks).strip()

    @staticmethod
    def _function_call_parts(response) -> list:
        if not response.candidates:
            return []
        content = response.candidates[0].content
        if not content or not content.parts:
            return []
        return [
            part
            for part in content.parts
            if part.function_call is not None
        ]

    @staticmethod
    def _log_empty_gemini_response(response, phase: str) -> None:
        finish = None
        if response.candidates:
            finish = getattr(response.candidates[0], "finish_reason", None)
        logger.warning(
            "Gemini sem texto nem tools (%s): finish_reason=%s",
            phase,
            finish,
        )

    async def _run_tool_rounds(
        self,
        response,
        contents: list,
        config,
        tools_used: list[str],
        pending_confirmation: dict | None,
        max_rounds: int,
    ) -> tuple:
        whatsapp_text: str | None = None
        round_idx = 0
        while round_idx < max_rounds:
            function_calls = self._function_call_parts(response)
            if not function_calls:
                break

            function_responses = []
            for fc in function_calls:
                tool_name = fc.function_call.name
                tool_args = (
                    dict(fc.function_call.args) if fc.function_call.args else {}
                )
                tools_used.append(tool_name)

                if _is_write_tool(tool_name) and max_rounds == MAX_TOOL_ROUNDS_READ:
                    max_rounds = MAX_TOOL_ROUNDS_WRITE

                logger.info(
                    "Chamando tool: %s (args_keys=%s)",
                    tool_name,
                    sorted(tool_args.keys()),
                )
                result = await execute_tool(
                    tool_name, tool_args, self.backend_url, self.auth_token
                )
                llm_result = result
                if isinstance(result, dict):
                    from app.core.confirmation_ux import tool_result_for_llm

                    llm_result = tool_result_for_llm(result)
                if isinstance(result, dict) and result.get("requires_confirmation"):
                    payload = result.get("payload") or {}
                    if not payload and result.get("order_id") is not None:
                        payload = {"order_id": result["order_id"]}
                    pending_confirmation = {
                        "action": result.get("action") or tool_name,
                        "confirm_token": result.get("confirm_token", ""),
                        "payload": payload,
                        "message": result.get("message") or "",
                    }

                if (
                    tool_name == "generate_whatsapp_message"
                    and isinstance(result, dict)
                    and result.get("ok")
                ):
                    raw_text = result.get("message_text")
                    if isinstance(raw_text, str) and raw_text.strip():
                        whatsapp_text = raw_text.strip()

                function_responses.append(
                    genai.types.Part.from_function_response(
                        name=tool_name, response={"result": llm_result}
                    )
                )

            if not response.candidates or not response.candidates[0].content:
                break
            contents.append(response.candidates[0].content)
            contents.append(
                genai.types.Content(role="user", parts=function_responses)
            )

            response, _ = await self._generate_with_fallback(contents, config)
            round_idx += 1

        return (
            response,
            contents,
            tools_used,
            pending_confirmation,
            max_rounds,
            whatsapp_text,
        )

    async def _generate_with_fallback(self, contents, config) -> tuple:
        """Try generating content; fallback models, then API keys on 429/503."""
        key_idx = api_key_manager.get_key_index()
        model = model_manager.get_model()
        client = get_client(key_idx)
        models_tried_on_key: set[str] = set()
        last_exc = None
        max_attempts = _max_fallback_attempts()

        for attempt in range(max_attempts):
            try:
                logger.info(
                    "Gemini key #%d, modelo %s (tentativa %d)",
                    key_idx + 1,
                    model,
                    attempt + 1,
                )
                response = await client.aio.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
                return response, model
            except genai.errors.APIError as exc:
                last_exc = exc

                if exc.code in (429, 503):
                    retry_after = _extract_retry_seconds(exc)
                    models_tried_on_key.add(model)
                    fallback_model = model_manager.mark_rate_limited(
                        model, retry_after
                    )
                    if (
                        fallback_model is not None
                        and fallback_model not in models_tried_on_key
                    ):
                        model = fallback_model
                        continue

                    next_key = api_key_manager.mark_rate_limited(
                        key_idx, retry_after
                    )
                    if next_key is not None:
                        key_idx = next_key
                        client = get_client(key_idx)
                        model = model_manager.get_model()
                        models_tried_on_key = set()
                        continue

                    raise RateLimitError(
                        "Todas as chaves e modelos estao temporariamente indisponiveis. "
                        "Aguarde um momento e tente novamente."
                    ) from exc
                elif exc.code == 404:
                    logger.warning("Modelo %s nao encontrado, pulando...", model)
                    fallback = model_manager.mark_rate_limited(model, 3600)
                    if fallback is None:
                        raise RateLimitError(
                            "Todos os modelos estao temporariamente indisponiveis. "
                            "Aguarde um momento e tente novamente."
                        ) from exc
                    model = fallback
                    continue
                elif _is_invalid_api_key_error(exc):
                    logger.warning(
                        "API key #%d invalida, tentando proxima chave...",
                        key_idx + 1,
                    )
                    next_key = api_key_manager.mark_rate_limited(key_idx, 3600)
                    if next_key is None:
                        raise RuntimeError(
                            "Nenhuma API key Gemini valida configurada. "
                            "Revise GEMINI_API_KEYS no .env do ai-service."
                        ) from exc
                    key_idx = next_key
                    client = get_client(key_idx)
                    model = model_manager.get_model()
                    continue
                else:
                    raise RuntimeError(
                        f"Falha na comunicacao com a IA: {exc.message}"
                    ) from exc

        raise RateLimitError(
            "Todas as chaves e modelos estao temporariamente indisponiveis. "
            "Aguarde um momento e tente novamente."
        ) from last_exc

    async def _recover_text_after_tools(self, contents: list) -> str:
        """Force a text answer when the model returned tool calls but no text.

        Recovery must never claim a write executed when the last
        function_response was actually a preview (requires_confirmation=True).
        """
        recovery_contents = [
            *contents,
            genai.types.Content(
                role="user",
                parts=[
                    genai.types.Part.from_text(
                        text=(
                            "Os dados ja estao na conversa acima (resultados das "
                            "funcoes). Escreva AGORA a resposta final em portugues "
                            "para o usuario, com base nesses dados. NAO invoque "
                            "novas funcoes. NAO use Markdown. Se o ultimo "
                            "function_response tiver requires_confirmation=true, "
                            "voce DEVE apresentar a previa e pedir confirmacao — "
                            "NUNCA afirme que a acao foi executada."
                        )
                    )
                ],
            ),
        ]
        recovery_cfg = genai.types.GenerateContentConfig(
            system_instruction=(
                "Voce e a Kuroko, assistente da Carambolos. Os dados ja foram "
                "obtidos e aparecem como function_response na conversa. "
                "Produza somente texto final para o usuario. E proibido chamar "
                "ferramentas. Se houver campo error no JSON, explique de forma "
                "clara. Se houver requires_confirmation=true, repita o campo "
                "message ao usuario e NUNCA diga que executou. Sem jargao "
                "tecnico. NAO use Markdown."
            ),
        )
        try:
            response, _ = await self._generate_with_fallback(
                recovery_contents, recovery_cfg
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Recuperacao pos-tools falhou: %s", exc)
            return ""

        return self._extract_text_from_response(response)

    async def ask(
        self,
        question: str,
        history: list[dict] | None = None,
        *,
        user_parts: list | None = None,
    ) -> dict:
        tools_used: list[str] = []
        pending_confirmation: dict | None = None
        whatsapp_text: str | None = None
        max_rounds = MAX_TOOL_ROUNDS_READ

        config = genai.types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[genai.types.Tool(function_declarations=TOOL_DECLARATIONS)],
        )

        contents = self._build_history_contents(history or [])
        if user_parts is not None:
            parts = user_parts
        else:
            parts = [genai.types.Part.from_text(text=question)]
        contents.append(
            genai.types.Content(
                role="user",
                parts=parts,
            )
        )

        response, model = await self._generate_with_fallback(contents, config)
        (
            response,
            contents,
            tools_used,
            pending_confirmation,
            max_rounds,
            whatsapp_text,
        ) = await self._run_tool_rounds(
            response,
            contents,
            config,
            tools_used,
            pending_confirmation,
            max_rounds,
        )

        answer = self._extract_text_from_response(response)

        if not answer and not tools_used:
            self._log_empty_gemini_response(response, "primeira_passagem")
            contents.append(
                genai.types.Content(
                    role="user",
                    parts=[genai.types.Part.from_text(text=_EMPTY_RESPONSE_NUDGE)],
                )
            )
            response, model = await self._generate_with_fallback(contents, config)
            (
                response,
                contents,
                tools_used,
                pending_confirmation,
                max_rounds,
                wa_retry,
            ) = await self._run_tool_rounds(
                response,
                contents,
                config,
                tools_used,
                pending_confirmation,
                max_rounds,
            )
            if wa_retry:
                whatsapp_text = wa_retry
            answer = self._extract_text_from_response(response)

        if not answer and tools_used:
            answer = await self._recover_text_after_tools(contents)

        if pending_confirmation and pending_confirmation.get("message"):
            answer = pending_confirmation["message"]
        elif whatsapp_text and "generate_whatsapp_message" in tools_used:
            answer = whatsapp_text
        elif not answer and any(_is_write_tool(t) for t in tools_used):
            answer = self._fallback_answer(tools_used)

        if not answer:
            answer = self._fallback_answer(tools_used)

        return {
            "answer": answer,
            "tools_used": tools_used,
            "pending_confirmation": pending_confirmation,
        }

    async def transcribe_audio(self, audio_bytes: bytes, mime_type: str) -> str:
        config = genai.types.GenerateContentConfig(
            system_instruction=(
                "Transcreva o audio em portugues brasileiro. "
                "Retorne somente a transcricao literal, sem aspas ou comentarios."
            ),
        )
        contents = [
            genai.types.Content(
                role="user",
                parts=[
                    genai.types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
                ],
            )
        ]
        response, _ = await self._generate_with_fallback(contents, config)
        return self._extract_text_from_response(response).strip()

    async def ask_audio(
        self,
        audio_bytes: bytes,
        mime_type: str,
        history: list[dict] | None = None,
    ) -> dict:
        user_parts = [
            genai.types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
            genai.types.Part.from_text(
                text=(
                    "Pergunta do usuario em audio acima. "
                    "Use as ferramentas do sistema quando necessario e responda em portugues."
                )
            ),
        ]
        return await self.ask("", history=history, user_parts=user_parts)

    @staticmethod
    def _fallback_answer(tools_used: list[str]) -> str:
        from app.tools.reports import REPORT_TOOL_NAME

        if REPORT_TOOL_NAME in tools_used:
            return "Pronto, gerei o relatorio de insights. Clique no botao abaixo para baixar o PDF."
        if "get_upcoming_deliveries" in tools_used:
            return (
                "Consultei entregas proximas no sistema e nao ha pedidos de bolo "
                "com entrega para o periodo pedido (lista vazia). "
                "Se esperava pedidos, confira as datas no cadastro."
            )
        if "get_recent_orders" in tools_used:
            return (
                "Os pedidos recentes foram consultados, mas a resposta em texto "
                "nao veio na primeira tentativa. Envie de novo a mesma pergunta "
                "ou tente em uma linha: "
                "'Quem sao os 5 clientes que mais aparecem nos pedidos recentes?'"
            )
        if "get_orders_by_dough" in tools_used:
            return (
                "Consultei pedidos por massa no sistema, mas a resposta em texto "
                "nao veio. Envie de novo: 'Quais pedidos usam a massa com id 2?'"
            )
        if "get_active_batch_with_products" in tools_used:
            return (
                "Consultei a fornada ativa e os produtos no sistema, mas o texto nao veio. "
                "Tente de novo: 'Mostra a fornada ativa e os produtos'."
            )
        if "get_active_batches" in tools_used or "get_products_in_batch" in tools_used:
            return (
                "Consultei fornadas no sistema, mas a resposta em texto nao veio. "
                "Pergunte: 'Mostra a fornada ativa e os produtos'."
            )
        write_hits = [
            t
            for t in tools_used
            if _is_write_tool(t)
        ]
        if write_hits:
            return (
                "Processei sua solicitacao no sistema, mas a resposta em texto nao veio. "
                "Se apareceu uma previa com Confirmar acima, use o botao ou diga sim. "
                "Se ja existe fornada ativa, diga se quer substituir ou encerrar a atual."
            )
        return "Nao foi possivel gerar uma resposta no momento. Tente reformular a pergunta."

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
