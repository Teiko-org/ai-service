import logging
import re

from fastapi import APIRouter, Request, HTTPException, UploadFile, File, Form

from app.api.deps import sanitize_input, check_prompt_injection, check_content_policy, validate_auth_token
from app.config import settings
from app.core.alerts import get_cached_alerts, refresh_alerts_now
from app.core.assistant import CarambolosAssistant, RateLimitError
from app.core.cache import cache
from app.core.confirmation_ux import humanize_confirm_error
from app.core.fornada_summary import append_fornada_summary_if_applicable
from app.core.request_context import (
    current_history,
    current_session_id,
    direct_user_commit,
)
from app.core.limiter import limiter
from app.core.api_key_manager import api_key_manager
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
    PendingConfirmation,
    SuggestedPrompt,
    SuggestedPromptsResponse,
    HealthResponse,
    WriteConfirmationCommit,
)
from app.tools.order_ref import extract_order_ids_from_text
from app.tools.registry import execute_tool
from app.tools.reports import REPORT_TOOL_NAME, REPORT_ENDPOINT, REPORT_FILENAME

# V3 writes (gated by ENABLE_WRITE_TOOLS on direct commit).
_VALID_WRITE_ACTIONS = frozenset(
    {
        "create_batch",
        "add_batch_lines",
        "close_batch",
        "replace_active_batch",
        "create_pedido_bolo_full",
    }
)

# V2 order status changes — always allowed via direct commit / "sim".
_ORDER_STATUS_ACTIONS = frozenset(
    {
        "mark_order_as_paid",
        "mark_order_as_completed",
        "mark_order_as_cancelled",
        "mark_order_as_pending",
    }
)

_VALID_COMMIT_ACTIONS = _VALID_WRITE_ACTIONS | _ORDER_STATUS_ACTIONS

_CONFIRM_PHRASE_RE = re.compile(
    r"^\s*(sim|confirmo|confirmar|confirmo\.|pode|ok|yes|confirm|manda|vai)\s*[.!?]?\s*$",
    re.IGNORECASE,
)

_BATCH_DATE_RANGE_RE = re.compile(
    r"(\d{1,2}/\d{1,2}/\d{4})\s*(?:a|ate|até|-|–)\s*(\d{1,2}/\d{1,2}/\d{4})",
    re.IGNORECASE,
)

_ALLOWED_AUDIO_MIME = frozenset(
    {
        "audio/webm",
        "audio/wav",
        "audio/x-wav",
        "audio/mpeg",
        "audio/mp3",
        "audio/mp4",
        "audio/m4a",
        "audio/ogg",
        "audio/x-m4a",
    }
)
_MAX_AUDIO_BYTES = 10 * 1024 * 1024

_AUDIO_EXT_MIME = {
    "webm": "audio/webm",
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "ogg": "audio/ogg",
}

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")

_REPORT_INTENT_RE = re.compile(
    r"\b(relat[oó]rio|pdf|exportar|exporta[cç][aã]o|documento|baixar|download|arquivo|imprimir)\b",
    re.IGNORECASE,
)

_WHATSAPP_INTENT_RE = re.compile(
    r"\b(whatsapp|zap|mensagem\s+de\s+confirm)",
    re.IGNORECASE,
)


def _user_requested_report(question: str) -> bool:
    return bool(_REPORT_INTENT_RE.search(question or ""))


def _normalize_audio_mime(content_type: str | None, filename: str | None) -> str:
    raw = (content_type or "").split(";")[0].strip().lower()
    if raw in _ALLOWED_AUDIO_MIME:
        return raw
    if filename and "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower()
        return _AUDIO_EXT_MIME.get(ext, raw or "audio/webm")
    return raw or "audio/webm"


def _build_ask_response(
    session_id: str,
    question: str,
    result: dict,
    *,
    transcription: str | None = None,
) -> AskResponse:
    pending = result.get("pending_confirmation")
    if isinstance(pending, dict) and (pending.get("message") or "").strip():
        result["answer"] = pending["message"].strip()

    session_store.append(session_id, "user", question)
    session_store.append(session_id, "assistant", result["answer"])

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

    pending = result.get("pending_confirmation")
    pending_model = (
        PendingConfirmation(**pending) if isinstance(pending, dict) else None
    )
    session_store.set_pending_confirmation(
        session_id, pending if isinstance(pending, dict) else None
    )

    return AskResponse(
        answer=result["answer"],
        tools_used=result["tools_used"],
        session_id=session_id,
        attachments=attachments,
        pending_confirmation=pending_model,
        transcription=transcription,
    )


def _user_requested_whatsapp(question: str) -> bool:
    return bool(_WHATSAPP_INTENT_RE.search(question or ""))


async def _apply_whatsapp_answer_fallback(
    question: str, token: str | None, result: dict
) -> None:
    """Garante texto real da API quando o usuario pediu mensagem de WhatsApp."""
    if not _user_requested_whatsapp(question):
        return
    if "generate_whatsapp_message" in result.get("tools_used", []):
        return
    ids = extract_order_ids_from_text(question)
    if not ids:
        return
    wa = await execute_tool(
        "generate_whatsapp_message",
        {"order_ids": ids},
        settings.carambolos_api_url,
        token,
    )
    if not isinstance(wa, dict):
        return
    if wa.get("error"):
        result["answer"] = str(wa["error"])
    elif wa.get("message_text"):
        result["answer"] = str(wa["message_text"]).strip()
    else:
        return
    tools = list(result.get("tools_used") or [])
    if "generate_whatsapp_message" not in tools:
        tools.append("generate_whatsapp_message")
    result["tools_used"] = tools


def _is_confirmation_phrase(question: str) -> bool:
    return bool(_CONFIRM_PHRASE_RE.match((question or "").strip()))


def _pending_dict_from_tool_result(result: dict) -> dict | None:
    if not result.get("requires_confirmation"):
        return None
    token = (result.get("confirm_token") or "").strip()
    if not token:
        return None
    return {
        "action": result.get("action") or "",
        "confirm_token": token,
        "payload": result.get("payload") or {},
        "message": (result.get("message") or "").strip(),
    }


def _extract_batch_dates_from_history(history: list[dict]) -> tuple[str, str] | None:
    from app.tools.writes.dates import parse_user_date

    for msg in reversed(history):
        if msg.get("role") != "user":
            continue
        content = msg.get("content") or ""
        match = _BATCH_DATE_RANGE_RE.search(content)
        if not match:
            continue
        try:
            start = parse_user_date(match.group(1), "data_inicio").isoformat()
            end = parse_user_date(match.group(2), "data_fim").isoformat()
            return start, end
        except Exception:
            continue
    return None


def _wants_replace_batch(question: str) -> bool:
    low = (question or "").lower()
    if "substituir" in low or "trocar" in low:
        return True
    return _is_confirmation_phrase(question)


def _wants_close_only_batch(question: str) -> bool:
    low = (question or "").lower()
    return ("encerrar" in low or "encerre" in low) and "substituir" not in low


async def _preview_replace_batch(
    data_inicio: str, data_fim: str, bearer_token: str | None
) -> dict | None:
    result = await execute_tool(
        "replace_active_batch",
        {
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "confirmed": False,
        },
        settings.carambolos_api_url,
        bearer_token,
    )
    pending = _pending_dict_from_tool_result(result) if isinstance(result, dict) else None
    if not pending:
        return None
    return {
        "answer": pending["message"],
        "tools_used": ["replace_active_batch"],
        "pending_confirmation": pending,
        "ok": False,
    }


async def _preview_close_active_batch(bearer_token: str | None) -> dict | None:
    listed = await execute_tool(
        "get_active_batches",
        {},
        settings.carambolos_api_url,
        bearer_token,
    )
    rows = []
    if isinstance(listed, dict):
        rows = listed.get("fornadas") or listed.get("data") or []
    fid = None
    if isinstance(rows, list) and rows:
        first = rows[0]
        if isinstance(first, dict):
            fid = first.get("numero") or first.get("id")
    if not isinstance(fid, int) or fid <= 0:
        return {
            "answer": "Nao encontrei fornada aberta para encerrar.",
            "tools_used": ["get_active_batches"],
            "pending_confirmation": None,
            "ok": False,
        }
    result = await execute_tool(
        "close_batch",
        {"fornada_id": fid, "confirmed": False},
        settings.carambolos_api_url,
        bearer_token,
    )
    pending = _pending_dict_from_tool_result(result) if isinstance(result, dict) else None
    if not pending:
        err = result.get("error") if isinstance(result, dict) else None
        return {
            "answer": humanize_confirm_error(str(err or "Nao foi possivel encerrar.")),
            "tools_used": ["close_batch"],
            "pending_confirmation": None,
            "ok": False,
        }
    return {
        "answer": pending["message"],
        "tools_used": ["close_batch"],
        "pending_confirmation": pending,
        "ok": False,
    }


async def _try_batch_conflict_fast_path(
    question: str,
    history: list[dict],
    bearer_token: str | None,
) -> dict | None:
    """Quando o usuario responde sim/substituir/encerrar sem botao visivel."""
    if _wants_close_only_batch(question):
        return await _preview_close_active_batch(bearer_token)
    if not _wants_replace_batch(question):
        return None
    dates = _extract_batch_dates_from_history(history)
    if not dates:
        return None
    return await _preview_replace_batch(dates[0], dates[1], bearer_token)


def _pending_model_from_session(session_id: str) -> PendingConfirmation | None:
    pending = session_store.get_pending_confirmation(session_id)
    if not isinstance(pending, dict):
        return None
    token = (pending.get("confirm_token") or "").strip()
    if not token:
        return None
    try:
        return PendingConfirmation(**pending)
    except Exception:
        return None


async def _finalize_write_commit_answer(
    action: str, result: dict, bearer_token: str | None
) -> str:
    answer = _format_write_commit_answer(action, result)
    if result.get("ok"):
        answer = await append_fornada_summary_if_applicable(
            action, result, answer, settings.carambolos_api_url, bearer_token
        )
    return answer


def _format_write_commit_answer(action: str, result: dict) -> str:
    if result.get("error"):
        return humanize_confirm_error(str(result["error"]))
    if action == "create_batch":
        fid = result.get("fornada_id")
        if fid is None:
            data = result.get("data")
            if isinstance(data, dict):
                fid = data.get("id")
        if fid is not None:
            return f"Fornada #{fid} criada com sucesso."
        return "Fornada criada com sucesso."
    if action == "add_batch_lines":
        fid = result.get("fornada_id")
        if fid is not None:
            return f"Produtos adicionados a fornada #{fid} com sucesso."
        return "Produtos adicionados a fornada com sucesso."
    if action == "close_batch":
        ids = result.get("fornada_ids")
        if isinstance(ids, list) and len(ids) > 1:
            listed = ", ".join(f"#{i}" for i in ids)
            return f"Fornadas {listed} encerradas com sucesso."
        fid = result.get("fornada_id")
        if fid is None and isinstance(ids, list) and len(ids) == 1:
            fid = ids[0]
        if fid is not None:
            return f"Fornada #{fid} encerrada com sucesso. Ela nao aparece mais como ativa no app."
        return "Fornada encerrada com sucesso."
    if action == "replace_active_batch":
        closed = result.get("closed_fornada_id")
        new_id = result.get("fornada_id")
        if new_id is None:
            data = result.get("data")
            if isinstance(data, dict):
                new_id = data.get("id")
        if closed is not None and new_id is not None:
            return (
                f"Fornada #{closed} encerrada e fornada #{new_id} criada com sucesso."
            )
        if new_id is not None:
            return f"Nova fornada #{new_id} criada com sucesso."
        return "Fornada substituida com sucesso."
    if action == "create_pedido_bolo_full":
        numero = result.get("pedido_numero")
        if numero is not None:
            return (
                f"Pedido #{numero} criado com sucesso. "
                "Esse e o numero no Kanban e no app."
            )
        return "Pedido de bolo criado com sucesso."
    if action in _ORDER_STATUS_ACTIONS and result.get("ok"):
        oid = result.get("order_id")
        status = result.get("new_status", "")
        if oid is not None:
            return f"O pedido #{oid} foi marcado como {status} com sucesso."
        return f"Pedido marcado como {status} com sucesso."
    return "Acao concluida com sucesso."


async def _handle_write_confirmation(
    confirmation: WriteConfirmationCommit,
    session_id: str,
    history: list[dict],
    bearer_token: str | None,
    user_confirmation_text: str = "Confirmo.",
) -> dict:
    user_line = (user_confirmation_text or "Confirmo.").strip() or "Confirmo."
    history_with = [*history, {"role": "user", "content": user_line}]
    session_token = current_session_id.set(session_id)
    history_token = current_history.set(history_with)
    commit_token = direct_user_commit.set(True)
    try:
        args: dict = {**confirmation.payload, "confirmed": True}
        token = (confirmation.confirm_token or "").strip()
        if token:
            args["confirm_token"] = token
        result = await execute_tool(
            confirmation.action,
            args,
            settings.carambolos_api_url,
            bearer_token,
        )
    finally:
        direct_user_commit.reset(commit_token)
        current_history.reset(history_token)
        current_session_id.reset(session_token)

    if result.get("ok") and confirmation.action in (
        "create_batch",
        "replace_active_batch",
    ):
        fid = result.get("fornada_id")
        data = result.get("data")
        if fid is None and isinstance(data, dict):
            fid = data.get("id")
        if isinstance(fid, int) and fid > 0:
            session_store.set_last_fornada_id(session_id, fid)

    if result.get("ok") and confirmation.action == "create_pedido_bolo_full":
        numero = result.get("pedido_numero")
        if isinstance(numero, int) and numero > 0:
            session_store.set_last_pedido_resumo_id(session_id, numero)

    answer = await _finalize_write_commit_answer(
        confirmation.action, result, bearer_token
    )
    pending_replay = None
    if not result.get("ok"):
        err_low = str(result.get("error") or answer or "").lower()
        if (
            confirmation.action == "create_batch"
            and "fornada" in err_low
            and confirmation.payload.get("data_inicio")
            and confirmation.payload.get("data_fim")
        ):
            session_store.set_pending_confirmation(session_id, None)
            replace = await _preview_replace_batch(
                str(confirmation.payload["data_inicio"]),
                str(confirmation.payload["data_fim"]),
                bearer_token,
            )
            if replace:
                session_store.set_pending_confirmation(
                    session_id, replace.get("pending_confirmation")
                )
                return replace
        pending_replay = _pending_model_from_session(session_id)
    return {
        "answer": answer,
        "tools_used": [confirmation.action],
        "pending_confirmation": pending_replay,
        "ok": bool(result.get("ok")),
    }


def _pending_payload(pending: dict) -> dict:
    payload = dict(pending.get("payload") or {})
    if pending.get("order_id") is not None and "order_id" not in payload:
        payload["order_id"] = pending["order_id"]
    return payload


async def _try_commit_stored_pending(
    question: str,
    session_id: str,
    history: list[dict],
    bearer_token: str | None,
) -> dict | None:
    if not _is_confirmation_phrase(question):
        return None
    pending = session_store.get_pending_confirmation(session_id)
    if not pending:
        return None
    action = pending.get("action")
    if not action or action not in _VALID_COMMIT_ACTIONS:
        return None
    if action in _VALID_WRITE_ACTIONS and not settings.enable_write_tools:
        return None

    token = (pending.get("confirm_token") or "").strip()
    payload = _pending_payload(pending)
    if token:
        confirmation = WriteConfirmationCommit(
            action=action,
            confirm_token=token,
            payload=payload,
        )
        out = await _handle_write_confirmation(
            confirmation,
            session_id,
            history,
            bearer_token,
            user_confirmation_text=question.strip(),
        )
        if out.get("ok"):
            session_store.set_pending_confirmation(session_id, None)
        return out

    if action in _VALID_WRITE_ACTIONS:
        # Writes V3 sempre exigem HMAC; sem token, recusar para nao executar
        # um commit nao validado.
        logger.warning(
            "Commit '%s' recusado: pending sem confirm_token (sessao %s).",
            action,
            session_id,
        )
        session_store.set_pending_confirmation(session_id, None)
        return {
            "answer": (
                "Essa confirmacao expirou ou nao tem o codigo de seguranca. "
                "Me peca de novo o que deseja fazer que eu mostro a previa outra vez."
            ),
            "tools_used": [],
            "pending_confirmation": None,
            "ok": False,
        }

    session_token = current_session_id.set(session_id)
    history_token = current_history.set(
        [*history, {"role": "user", "content": question.strip()}]
    )
    try:
        result = await execute_tool(
            action,
            {**payload, "confirmed": True},
            settings.carambolos_api_url,
            bearer_token,
        )
    finally:
        current_history.reset(history_token)
        current_session_id.reset(session_token)
    answer = await _finalize_write_commit_answer(action, result, bearer_token)
    if result.get("ok"):
        session_store.set_pending_confirmation(session_id, None)
    pending_replay = None
    if not result.get("ok"):
        pending_replay = _pending_model_from_session(session_id)
    return {
        "answer": answer,
        "tools_used": [action],
        "pending_confirmation": pending_replay,
        "ok": bool(result.get("ok")),
    }


@router.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(status="ok", version="1.0.0")


@router.post("/ask", response_model=AskResponse)
@limiter.limit("15/minute")
async def ask_question(body: AskRequest, request: Request):
    token = await validate_auth_token(request)

    session = session_store.get_or_create(body.session_id)
    history = session_store.get_history(session.id, limit=10)

    if body.confirmation is not None:
        action = body.confirmation.action
        if action not in _VALID_COMMIT_ACTIONS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Acao '{action}' nao e uma acao de confirmacao reconhecida."
                ),
            )
        if action in _VALID_WRITE_ACTIONS and not settings.enable_write_tools:
            raise HTTPException(
                status_code=400,
                detail="Confirmacao recebida mas escrita V3 esta desabilitada no servidor.",
            )
        result = await _handle_write_confirmation(
            body.confirmation,
            session.id,
            history,
            token,
            user_confirmation_text="Confirmo.",
        )
        if result.get("ok"):
            session_store.set_pending_confirmation(session.id, None)
        session_store.append(session.id, "user", "Confirmo.")
        session_store.append(session.id, "assistant", result["answer"])
        pending_model = result.get("pending_confirmation")
        if pending_model is None and not result.get("ok"):
            pending_model = _pending_model_from_session(session.id)
        return AskResponse(
            answer=result["answer"],
            tools_used=result["tools_used"],
            session_id=session.id,
            attachments=[],
            pending_confirmation=pending_model,
        )

    question = sanitize_input(body.question)
    check_prompt_injection(question)
    check_content_policy(question)

    fast_commit = await _try_commit_stored_pending(
        question, session.id, history, token
    )
    if fast_commit is None:
        fast_commit = await _try_batch_conflict_fast_path(
            question,
            [*history, {"role": "user", "content": question}],
            token,
        )
    if fast_commit is not None:
        session_store.append(session.id, "user", question)
        session_store.append(session.id, "assistant", fast_commit["answer"])
        pending_raw = fast_commit.get("pending_confirmation")
        if isinstance(pending_raw, dict):
            session_store.set_pending_confirmation(session.id, pending_raw)
        pending_model = (
            PendingConfirmation(**pending_raw)
            if isinstance(pending_raw, dict)
            else _pending_model_from_session(session.id)
        )
        if pending_model is None and not fast_commit.get("ok"):
            pending_model = _pending_model_from_session(session.id)
        return AskResponse(
            answer=fast_commit["answer"],
            tools_used=fast_commit["tools_used"],
            session_id=session.id,
            attachments=[],
            pending_confirmation=pending_model,
        )

    assistant = CarambolosAssistant(auth_token=token)

    # Publish per-request context so write tools (V3) can bind the preview
    # token to this session and enforce same-turn confirmation.
    history_with_current = [*history, {"role": "user", "content": question}]
    session_token = current_session_id.set(session.id)
    history_token = current_history.set(history_with_current)
    try:
        result = await assistant.ask(question, history=history)
    except RateLimitError as exc:
        logger.warning("Rate limit Gemini: %s", exc)
        raise HTTPException(status_code=429, detail=str(exc))
    except RuntimeError as exc:
        msg = str(exc).lower()
        if "high demand" in msg or "try again" in msg:
            logger.warning("Gemini indisponivel (demanda): %s", exc)
            raise HTTPException(
                status_code=429,
                detail=(
                    "A IA esta com alta demanda no momento. "
                    "Aguarde alguns segundos e tente novamente."
                ),
            ) from exc
        logger.error("Erro no assistente: %s", exc)
        raise HTTPException(status_code=500, detail="Erro ao processar a pergunta.") from exc
    except Exception as exc:
        logger.error("Erro no assistente: %s", exc)
        raise HTTPException(status_code=500, detail="Erro ao processar a pergunta.") from exc
    finally:
        current_history.reset(history_token)
        current_session_id.reset(session_token)

    await _apply_whatsapp_answer_fallback(question, token, result)

    return _build_ask_response(session.id, question, result)


@router.post("/ask/audio", response_model=AskResponse)
@limiter.limit("10/minute")
async def ask_audio(
    request: Request,
    audio: UploadFile = File(...),
    session_id: str | None = Form(default=None),
):
    token = await validate_auth_token(request)

    session = session_store.get_or_create(session_id)
    history = session_store.get_history(session.id, limit=10)

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Arquivo de audio vazio.")
    if len(audio_bytes) > _MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=400,
            detail="Audio muito grande. Maximo 10 MB.",
        )

    mime_type = _normalize_audio_mime(audio.content_type, audio.filename)
    if mime_type not in _ALLOWED_AUDIO_MIME:
        raise HTTPException(
            status_code=400,
            detail=f"Formato de audio nao suportado: {mime_type or 'desconhecido'}.",
        )

    assistant = CarambolosAssistant(auth_token=token)

    try:
        transcription = await assistant.transcribe_audio(audio_bytes, mime_type)
    except RateLimitError as exc:
        logger.warning("Rate limit Gemini (transcricao): %s", exc)
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Erro ao transcrever audio: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Erro ao transcrever o audio.",
        ) from exc

    question = sanitize_input(transcription)
    if not question.strip():
        raise HTTPException(
            status_code=400,
            detail="Nao foi possivel entender o audio. Tente falar mais perto do microfone.",
        )
    check_prompt_injection(question)
    check_content_policy(question)

    fast_commit = await _try_commit_stored_pending(
        question, session.id, history, token
    )
    if fast_commit is None:
        fast_commit = await _try_batch_conflict_fast_path(
            question,
            [*history, {"role": "user", "content": question}],
            token,
        )
    if fast_commit is not None:
        session_store.append(session.id, "user", question)
        session_store.append(session.id, "assistant", fast_commit["answer"])
        pending_raw = fast_commit.get("pending_confirmation")
        if isinstance(pending_raw, dict):
            session_store.set_pending_confirmation(session.id, pending_raw)
        pending_model = (
            PendingConfirmation(**pending_raw)
            if isinstance(pending_raw, dict)
            else _pending_model_from_session(session.id)
        )
        if pending_model is None and not fast_commit.get("ok"):
            pending_model = _pending_model_from_session(session.id)
        return AskResponse(
            answer=fast_commit["answer"],
            tools_used=fast_commit["tools_used"],
            session_id=session.id,
            attachments=[],
            pending_confirmation=pending_model,
            transcription=question,
        )

    history_with_current = [*history, {"role": "user", "content": question}]
    session_token = current_session_id.set(session.id)
    history_token = current_history.set(history_with_current)
    try:
        result = await assistant.ask_audio(audio_bytes, mime_type, history=history)
    except RateLimitError as exc:
        logger.warning("Rate limit Gemini (audio): %s", exc)
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except RuntimeError as exc:
        msg = str(exc).lower()
        if "high demand" in msg or "try again" in msg:
            logger.warning("Gemini indisponivel (demanda): %s", exc)
            raise HTTPException(
                status_code=429,
                detail=(
                    "A IA esta com alta demanda no momento. "
                    "Aguarde alguns segundos e tente novamente."
                ),
            ) from exc
        logger.error("Erro no assistente (audio): %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Erro ao processar o audio.",
        ) from exc
    except Exception as exc:
        logger.error("Erro no assistente (audio): %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Erro ao processar o audio.",
        ) from exc
    finally:
        current_history.reset(history_token)
        current_session_id.reset(session_token)

    await _apply_whatsapp_answer_fallback(question, token, result)

    return _build_ask_response(
        session.id,
        question,
        result,
        transcription=question,
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
    mm = model_manager.get_status()
    return {
        "model_chain": mm["chain"],
        "model_primary": mm["primary"],
        "models": mm["models"],
        "api_keys": api_key_manager.get_status(),
    }
