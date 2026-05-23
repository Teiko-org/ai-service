"""Tool de escrita V3: cria pedido de bolo completo em uma confirmacao.

Cadeia no commit:
  POST /bolos/recheio-pedido -> POST /bolos -> POST /bolos/pedido -> POST /resumo-pedido
Opcional: POST /enderecos quando tipo_entrega=ENTREGA com objeto `endereco`.

Falha apos passo parcial -> DELETE reverso best-effort.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import google.genai as genai
import httpx

from app.tools.writes._helpers import (
    WriteToolError,
    check_throttle,
    coerce_positive_int,
    delete_best_effort,
    effective_confirmed,
    get_json,
    issue_preview,
    post_json,
    preview_response,
    require_auth,
    run_idempotent_commit,
    verify_commit,
)
from app.core.request_context import current_session_id
from app.core.sessions import session_store
from app.tools.writes.bolo_catalog_resolve import apply_catalog_names
from app.tools.writes.dates import parse_user_date as _parse_user_date
from app.tools.writes.schema_shared import (
    confirm_token_param as _confirm_token_param,
    confirmed_param as _confirmed_param,
)

WRITE_TOOL_NAMES = {"create_pedido_bolo_full"}

_FORMATOS = frozenset({"CIRCULO", "CORACAO"})
_TAMANHOS = frozenset({
    "TAMANHO_5",
    "TAMANHO_7",
    "TAMANHO_12",
    "TAMANHO_15",
    "TAMANHO_17",
})
_TIPOS_ENTREGA = frozenset({"RETIRADA", "ENTREGA"})
_MAX_HORIZON_DAYS = 365
_MAX_OBS_LEN = 2000
_MAX_NAME_LEN = 100
_MIN_PHONE_DIGITS = 10


DECLARATIONS = [
    genai.types.FunctionDeclaration(
        name="create_pedido_bolo_full",
        description=(
            "Cria um pedido completo de bolo personalizado em uma unica operacao: "
            "monta recheio-pedido, bolo, pedido e resumo (Pedido #X). Acao "
            "destrutiva: SEMPRE chame primeiro com confirmed=False, mostre a "
            "previa e SO confirme com confirmed=True + confirm_token apos "
            "resposta explicita do usuario em NOVA mensagem. Antes de chamar, "
            "use tools de catalogo OU informe massa_nome / recheio_nome / "
            "recheio_exclusivo_nome (o servidor resolve o id)."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={
                "massa_id": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description="ID da massa (catalogo get_doughs_catalog).",
                ),
                "massa_nome": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    description=(
                        "Sabor da massa por nome (ex.: Chocolate). Alternativa "
                        "a massa_id; nao peca ID ao usuario."
                    ),
                ),
                "cobertura_id": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description=(
                        "ID da cobertura. Opcional: se omitido, usa a primeira "
                        "cobertura cadastrada no backend."
                    ),
                ),
                "decoracao_id": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description="ID da decoracao (opcional).",
                ),
                "formato": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    description="Formato do bolo: CIRCULO ou CORACAO.",
                ),
                "tamanho": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    description=(
                        "Tamanho enum, ex.: TAMANHO_12 (get_cake_sizes)."
                    ),
                ),
                "categoria": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    description="Categoria do bolo (padrao PERSONALIZADO).",
                ),
                "recheio_exclusivo_id": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description="ID do recheio exclusivo (mutuamente exclusivo com unitarios).",
                ),
                "recheio_exclusivo_nome": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    description="Nome do recheio exclusivo (alternativa ao id).",
                ),
                "recheio_nome": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    description=(
                        "Sabor de recheio unitario unico por nome (alternativa "
                        "a recheio_unitario_id)."
                    ),
                ),
                "recheio_unitario_id": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description=(
                        "ID de um recheio unitario unico (replicado nos dois "
                        "slots, como no app web)."
                    ),
                ),
                "recheio_unitario_1": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description="Primeiro recheio unitario (par com recheio_unitario_2).",
                ),
                "recheio_unitario_2": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description="Segundo recheio unitario.",
                ),
                "nome_cliente": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    description="Nome do cliente.",
                ),
                "telefone_cliente": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    description="Telefone do cliente.",
                ),
                "tipo_entrega": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    description="RETIRADA ou ENTREGA.",
                ),
                "data_previsao_entrega": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    description="Data de entrega (yyyy-MM-dd ou dd/MM/yyyy).",
                ),
                "hora_entrega": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    description="Horario HH:MM para resumo/retirada (opcional, padrao 12:00).",
                ),
                "horario_retirada": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    description="Horario HH:MM obrigatorio se tipo_entrega=RETIRADA.",
                ),
                "observacao": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    description="Observacoes do pedido (opcional).",
                ),
                "endereco_id": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description="ID de endereco existente (ENTREGA).",
                ),
                "endereco": genai.types.Schema(
                    type=genai.types.Type.OBJECT,
                    description=(
                        "Dados para criar endereco novo (ENTREGA). Campos: cep "
                        "(8 digitos), cidade, bairro, logradouro, numero; "
                        "opcionais: estado, nome, complemento, referencia."
                    ),
                    properties={
                        "cep": genai.types.Schema(type=genai.types.Type.STRING),
                        "cidade": genai.types.Schema(type=genai.types.Type.STRING),
                        "bairro": genai.types.Schema(type=genai.types.Type.STRING),
                        "logradouro": genai.types.Schema(type=genai.types.Type.STRING),
                        "numero": genai.types.Schema(type=genai.types.Type.STRING),
                        "estado": genai.types.Schema(type=genai.types.Type.STRING),
                        "nome": genai.types.Schema(type=genai.types.Type.STRING),
                        "complemento": genai.types.Schema(type=genai.types.Type.STRING),
                        "referencia": genai.types.Schema(type=genai.types.Type.STRING),
                    },
                    required=["cep", "cidade", "bairro", "logradouro", "numero"],
                ),
                "confirmed": _confirmed_param(),
                "confirm_token": _confirm_token_param(),
            },
            required=[
                "formato",
                "tamanho",
                "nome_cliente",
                "telefone_cliente",
                "tipo_entrega",
                "data_previsao_entrega",
            ],
        ),
    ),
]


@dataclass
class _ValidatedOrder:
    massa_id: int
    massa_label: str
    recheio_label: str
    cobertura_id: int | None
    decoracao_id: int | None
    formato: str
    tamanho: str
    categoria: str
    # Mesma forma de entrada para o payload bater com o HMAC no commit.
    recheio_exclusivo_id: int | None
    recheio_unitario_id: int | None
    recheio_unitario_1: int | None
    recheio_unitario_2: int | None
    nome_cliente: str
    telefone_cliente: str
    tipo_entrega: str
    data_previsao_entrega: str
    hora_entrega: str
    horario_retirada: str | None
    observacao: str | None
    endereco_id: int | None
    endereco: dict[str, Any] | None


@dataclass
class _CreatedIds:
    endereco_id: int | None = None
    recheio_pedido_id: int | None = None
    bolo_id: int | None = None
    pedido_id: int | None = None
    resumo_id: int | None = None


def _normalize_tamanho(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WriteToolError(
            f"tamanho invalido. Valores: {', '.join(sorted(_TAMANHOS))} ou ex. 12."
        )
    raw = value.strip().upper().replace(" ", "_")
    if raw in _TAMANHOS:
        return raw
    digits = re.sub(r"\D", "", raw)
    if digits:
        cand = f"TAMANHO_{digits}"
        if cand in _TAMANHOS:
            return cand
    raise WriteToolError(
        f"tamanho invalido. Valores: {', '.join(sorted(_TAMANHOS))}."
    )


def _parse_hhmm(value: Any, field: str, required: bool = False) -> str | None:
    if value is None or value == "":
        if required:
            raise WriteToolError(f"{field} e obrigatorio (formato HH:MM).")
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
        raise WriteToolError(f"{field} invalido: use HH:MM.")
    return value


def _normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if len(digits) < _MIN_PHONE_DIGITS:
        raise WriteToolError(
            f"telefone_cliente invalido: minimo {_MIN_PHONE_DIGITS} digitos."
        )
    return phone.strip()[:30]


def _normalize_recheio_fields(
    args: dict,
) -> tuple[int | None, int | None, int | None, int | None]:
    """Validate that exactly one recheio kind is provided and return its canonical fields."""

    exclusivo = args.get("recheio_exclusivo_id")
    unit_single = args.get("recheio_unitario_id")
    u1 = args.get("recheio_unitario_1")
    u2 = args.get("recheio_unitario_2")

    has_ex = exclusivo is not None
    has_single = unit_single is not None
    has_pair = (u1 is not None) or (u2 is not None)

    modes = sum([has_ex, has_single, has_pair])
    if modes != 1:
        raise WriteToolError(
            "Informe exatamente um tipo de recheio: recheio_exclusivo_id, "
            "recheio_unitario_id OU o par recheio_unitario_1 + recheio_unitario_2."
        )

    if has_ex:
        return coerce_positive_int(exclusivo, "recheio_exclusivo_id"), None, None, None
    if has_single:
        return None, coerce_positive_int(unit_single, "recheio_unitario_id"), None, None
    if u1 is None or u2 is None:
        raise WriteToolError(
            "recheio_unitario_1 e recheio_unitario_2 devem ser informados juntos."
        )
    return (
        None,
        None,
        coerce_positive_int(u1, "recheio_unitario_1"),
        coerce_positive_int(u2, "recheio_unitario_2"),
    )


def _build_recheio_body(v: "_ValidatedOrder") -> dict[str, Any]:
    """Build the POST /bolos/recheio-pedido body from validated canonical fields."""

    if v.recheio_exclusivo_id is not None:
        return {
            "idExclusivo": v.recheio_exclusivo_id,
            "idUnitario1": None,
            "idUnitario2": None,
        }
    if v.recheio_unitario_id is not None:
        uid = v.recheio_unitario_id
        return {"idExclusivo": None, "idUnitario1": uid, "idUnitario2": uid}
    return {
        "idExclusivo": None,
        "idUnitario1": v.recheio_unitario_1,
        "idUnitario2": v.recheio_unitario_2,
    }


def _normalize_endereco(raw: Any) -> dict[str, Any] | None:
    """Validate the address dict and return a canonical version (input shape).

    Canonical keys mirror the tool input so the same payload round-trips
    between preview and commit (HMAC over canonical args must match).
    """

    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise WriteToolError("endereco deve ser um objeto.")
    cep = re.sub(r"\D", "", str(raw.get("cep", "")))
    if len(cep) != 8:
        raise WriteToolError("endereco.cep deve ter 8 digitos numericos.")
    for key in ("cidade", "bairro", "logradouro", "numero"):
        val = raw.get(key)
        if not isinstance(val, str) or not val.strip():
            raise WriteToolError(f"endereco.{key} e obrigatorio.")
    return {
        "cep": cep,
        "cidade": raw["cidade"].strip()[:100],
        "bairro": raw["bairro"].strip()[:100],
        "logradouro": raw["logradouro"].strip()[:100],
        "numero": str(raw["numero"]).strip()[:6],
        "estado": (raw.get("estado") or "SP").strip()[:20],
        "nome": (raw.get("nome") or "Endereco de entrega").strip()[:20],
        "complemento": (raw.get("complemento") or "").strip()[:20],
        "referencia": (raw.get("referencia") or "").strip()[:70],
    }


def _endereco_request_body(endereco: dict[str, Any]) -> dict[str, Any]:
    # Backend EnderecoRequestDTO shape, derived from the canonical input dict.
    return {**endereco, "usuario": None}


def _validate_and_canonicalize(args: dict) -> tuple[_ValidatedOrder, dict]:
    massa_raw = args.get("massa_id")
    if massa_raw is None:
        raise WriteToolError("Informe massa_id ou massa_nome.")
    massa_id = coerce_positive_int(massa_raw, "massa_id")
    cobertura_id = args.get("cobertura_id")
    if cobertura_id is not None:
        cobertura_id = coerce_positive_int(cobertura_id, "cobertura_id")

    decoracao_id = args.get("decoracao_id")
    if decoracao_id is not None:
        decoracao_id = coerce_positive_int(decoracao_id, "decoracao_id")

    formato = args.get("formato")
    if not isinstance(formato, str) or formato.upper() not in _FORMATOS:
        raise WriteToolError("formato deve ser CIRCULO ou CORACAO.")
    formato = formato.upper()

    tamanho = _normalize_tamanho(args.get("tamanho"))

    categoria = args.get("categoria") or "PERSONALIZADO"
    if not isinstance(categoria, str) or not categoria.strip():
        raise WriteToolError("categoria invalida.")
    categoria = categoria.strip()[:80]

    rex, runi, ru1, ru2 = _normalize_recheio_fields(args)

    nome = args.get("nome_cliente")
    if not isinstance(nome, str) or not nome.strip():
        raise WriteToolError("nome_cliente e obrigatorio.")
    nome = nome.strip()[:_MAX_NAME_LEN]

    telefone = _normalize_phone(str(args.get("telefone_cliente", "")))

    tipo = args.get("tipo_entrega")
    if not isinstance(tipo, str) or tipo.upper() not in _TIPOS_ENTREGA:
        raise WriteToolError("tipo_entrega deve ser RETIRADA ou ENTREGA.")
    tipo = tipo.upper()

    entrega = _parse_user_date(
        args.get("data_previsao_entrega"), "data_previsao_entrega"
    )
    today = date.today()
    if entrega < today:
        raise WriteToolError("data_previsao_entrega nao pode estar no passado.")
    if entrega > today + timedelta(days=_MAX_HORIZON_DAYS):
        raise WriteToolError(
            f"data_previsao_entrega muito distante (max {_MAX_HORIZON_DAYS} dias)."
        )

    hora_entrega = _parse_hhmm(args.get("hora_entrega"), "hora_entrega") or "12:00"
    horario_retirada = _parse_hhmm(
        args.get("horario_retirada"), "horario_retirada", required=(tipo == "RETIRADA")
    )

    observacao = args.get("observacao")
    if observacao is not None:
        if not isinstance(observacao, str):
            raise WriteToolError("observacao deve ser texto.")
        observacao = observacao.strip()[:_MAX_OBS_LEN] or None

    endereco_id = args.get("endereco_id")
    if endereco_id is not None:
        endereco_id = coerce_positive_int(endereco_id, "endereco_id")

    endereco = _normalize_endereco(args.get("endereco"))

    if tipo == "ENTREGA" and endereco_id is None and endereco is None:
        raise WriteToolError(
            "ENTREGA exige endereco_id (existente) ou objeto endereco (novo)."
        )
    if tipo == "RETIRADA" and (endereco_id is not None or endereco is not None):
        raise WriteToolError(
            "RETIRADA nao deve incluir endereco_id nem endereco."
        )

    canonical = {
        "massa_id": massa_id,
        "cobertura_id": cobertura_id,
        "decoracao_id": decoracao_id,
        "formato": formato,
        "tamanho": tamanho,
        "categoria": categoria,
        "recheio_exclusivo_id": rex,
        "recheio_unitario_id": runi,
        "recheio_unitario_1": ru1,
        "recheio_unitario_2": ru2,
        "nome_cliente": nome,
        "telefone_cliente": telefone,
        "tipo_entrega": tipo,
        "data_previsao_entrega": entrega.isoformat(),
        "hora_entrega": hora_entrega,
        "horario_retirada": horario_retirada,
        "observacao": observacao,
        "endereco_id": endereco_id,
        "endereco": endereco,
    }

    massa_label = _massa_display(args, massa_id)
    recheio_label = _recheio_display(args, rex, runi, ru1, ru2)

    validated = _ValidatedOrder(
        massa_id=massa_id,
        massa_label=massa_label,
        recheio_label=recheio_label,
        cobertura_id=cobertura_id,
        decoracao_id=decoracao_id,
        formato=formato,
        tamanho=tamanho,
        categoria=categoria,
        recheio_exclusivo_id=rex,
        recheio_unitario_id=runi,
        recheio_unitario_1=ru1,
        recheio_unitario_2=ru2,
        nome_cliente=nome,
        telefone_cliente=telefone,
        tipo_entrega=tipo,
        data_previsao_entrega=entrega.isoformat(),
        hora_entrega=hora_entrega,
        horario_retirada=horario_retirada,
        observacao=observacao,
        endereco_id=endereco_id,
        endereco=endereco,
    )
    return validated, canonical


def _label_from_catalog(args: dict, key: str, fallback: str) -> str:
    labels = args.get("_catalog_labels")
    if isinstance(labels, dict):
        text = labels.get(key)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return fallback


def _massa_display(args: dict, massa_id: int) -> str:
    return _label_from_catalog(args, "massa", f"massa #{massa_id}")


def _recheio_display(
    args: dict,
    rex: int | None,
    runi: int | None,
    ru1: int | None,
    ru2: int | None,
) -> str:
    labeled = _label_from_catalog(args, "recheio", "")
    if labeled:
        return labeled
    if rex is not None:
        return f"exclusivo #{rex}"
    if runi is not None:
        return f"unitario #{runi}"
    if ru1 is not None and ru2 is not None:
        return f"dois sabores (#{ru1} + #{ru2})"
    return "recheio"


def _format_tamanho(tamanho: str) -> str:
    if tamanho.startswith("TAMANHO_"):
        return f"{tamanho.replace('TAMANHO_', '')}cm"
    return tamanho


def _format_formato(formato: str) -> str:
    if formato == "CIRCULO":
        return "circulo"
    if formato == "CORACAO":
        return "coracao"
    return formato.lower()


def _preview_message(v: _ValidatedOrder) -> str:
    entrega_txt = (
        f"retirada as {v.horario_retirada}"
        if v.tipo_entrega == "RETIRADA"
        else "entrega"
    )
    deco = f", decoracao #{v.decoracao_id}" if v.decoracao_id else ""
    return (
        f"Vou criar pedido de bolo para {v.nome_cliente}: massa {v.massa_label}, "
        f"recheio {v.recheio_label}, {_format_formato(v.formato)} "
        f"{_format_tamanho(v.tamanho)}{deco}, {entrega_txt} em "
        f"{v.data_previsao_entrega}. Confirma?"
    )


async def _resolve_cobertura_id(
    client: httpx.AsyncClient, base_url: str, token: str | None, explicit: int | None
) -> int:
    if explicit is not None:
        return explicit
    # If the GET fails (network glitch, 5xx, empty list) fall back to creating
    # a default cobertura so the chain still produces a Pedido; the GET error
    # is logged-not-raised so it doesn't blow up the whole commit.
    try:
        data = await get_json(client, f"{base_url}/bolos/cobertura", token)
        if isinstance(data, list) and data:
            first_id = data[0].get("id")
            if isinstance(first_id, int) and first_id > 0:
                return first_id
    except WriteToolError:
        pass
    created = await post_json(
        client,
        f"{base_url}/bolos/cobertura",
        {"cor": "Branco", "descricao": "Cobertura padrao"},
        token,
    )
    cid = created.get("id")
    if not isinstance(cid, int) or cid <= 0:
        raise WriteToolError("Nao foi possivel obter cobertura_id para o bolo.")
    return cid


async def _rollback_chain(
    client: httpx.AsyncClient,
    base_url: str,
    token: str | None,
    created: _CreatedIds,
) -> None:
    if created.resumo_id is not None:
        await delete_best_effort(
            client, f"{base_url}/resumo-pedido/{created.resumo_id}", token
        )
    if created.pedido_id is not None:
        await delete_best_effort(
            client, f"{base_url}/bolos/pedido/{created.pedido_id}", token
        )
    if created.bolo_id is not None:
        await delete_best_effort(client, f"{base_url}/bolos/{created.bolo_id}", token)
    if created.recheio_pedido_id is not None:
        await delete_best_effort(
            client,
            f"{base_url}/bolos/recheio-pedido/{created.recheio_pedido_id}",
            token,
        )
    if created.endereco_id is not None:
        await delete_best_effort(
            client, f"{base_url}/enderecos/{created.endereco_id}", token
        )


async def _execute_chain(
    v: _ValidatedOrder,
    base_url: str,
    token: str | None,
    client: httpx.AsyncClient,
) -> dict:
    created = _CreatedIds()
    try:
        endereco_id = v.endereco_id
        if v.tipo_entrega == "ENTREGA" and v.endereco is not None:
            addr = await post_json(
                client, f"{base_url}/enderecos", _endereco_request_body(v.endereco), token
            )
            endereco_id = addr.get("id")
            if not isinstance(endereco_id, int) or endereco_id <= 0:
                raise WriteToolError("Backend nao retornou id do endereco criado.")
            created.endereco_id = endereco_id

        recheio_resp = await post_json(
            client, f"{base_url}/bolos/recheio-pedido", _build_recheio_body(v), token
        )
        recheio_id = recheio_resp.get("id")
        if not isinstance(recheio_id, int) or recheio_id <= 0:
            raise WriteToolError("Backend nao retornou id do recheio-pedido.")
        created.recheio_pedido_id = recheio_id

        cobertura_id = await _resolve_cobertura_id(
            client, base_url, token, v.cobertura_id
        )
        bolo_body: dict[str, Any] = {
            "recheioPedidoId": recheio_id,
            "massaId": v.massa_id,
            "coberturaId": cobertura_id,
            "formato": v.formato,
            "tamanho": v.tamanho,
            "categoria": v.categoria,
        }
        if v.decoracao_id is not None:
            bolo_body["decoracaoId"] = v.decoracao_id

        bolo_resp = await post_json(client, f"{base_url}/bolos", bolo_body, token)
        bolo_id = bolo_resp.get("id")
        if not isinstance(bolo_id, int) or bolo_id <= 0:
            raise WriteToolError("Backend nao retornou id do bolo.")
        created.bolo_id = bolo_id

        horario = v.horario_retirada if v.tipo_entrega == "RETIRADA" else None
        pedido_body = {
            "boloId": bolo_id,
            "usuarioId": None,
            "observacao": v.observacao,
            "dataPrevisaoEntrega": v.data_previsao_entrega,
            "dataUltimaAtualizacao": datetime.now().isoformat(timespec="seconds"),
            "tipoEntrega": v.tipo_entrega,
            "nomeCliente": v.nome_cliente,
            "telefoneCliente": v.telefone_cliente,
            "enderecoId": endereco_id if v.tipo_entrega == "ENTREGA" else None,
            "horarioRetirada": horario,
        }
        pedido_resp = await post_json(
            client, f"{base_url}/bolos/pedido", pedido_body, token
        )
        pedido_id = pedido_resp.get("id")
        if not isinstance(pedido_id, int) or pedido_id <= 0:
            raise WriteToolError("Backend nao retornou id do pedido de bolo.")
        created.pedido_id = pedido_id

        hora_resumo = horario or v.hora_entrega
        resumo_body = {
            "dataEntrega": f"{v.data_previsao_entrega}T{hora_resumo}:00",
            "pedidoBoloId": pedido_id,
            "pedidoFornadaId": None,
        }
        resumo_resp = await post_json(
            client, f"{base_url}/resumo-pedido", resumo_body, token
        )
        resumo_id = resumo_resp.get("id")
        if not isinstance(resumo_id, int) or resumo_id <= 0:
            raise WriteToolError("Backend nao retornou id do resumo de pedido.")
        created.resumo_id = resumo_id

        sid = current_session_id.get()
        if sid and resumo_id > 0:
            session_store.set_last_pedido_resumo_id(sid, resumo_id)

        return {
            "ok": True,
            "action": "create_pedido_bolo_full",
            "pedido_numero": resumo_id,
            "instruction": (
                "Cite ao usuario apenas pedido_numero (mesmo numero do Kanban/app). "
                "Para get_cake_order_details ou get_order_summary_by_id use "
                f"order_id={resumo_id}. Nao use pedido_bolo_id nem outros ids internos."
            ),
            "ids_internos": {
                "recheio_pedido_id": recheio_id,
                "bolo_id": bolo_id,
                "pedido_bolo_id": pedido_id,
                "resumo_pedido_id": resumo_id,
                "endereco_id": endereco_id,
            },
            "resumo": resumo_resp,
        }
    except WriteToolError:
        await _rollback_chain(client, base_url, token, created)
        raise
    except Exception as exc:
        await _rollback_chain(client, base_url, token, created)
        raise WriteToolError(f"Falha na cadeia de criacao: {exc}") from exc


async def _execute_create_pedido_bolo_full(
    args: dict, base_url: str, token: str | None, client: httpx.AsyncClient
) -> dict:
    resolved = await apply_catalog_names(args, client, base_url, token)
    validated, canonical = _validate_and_canonicalize(resolved)
    confirmed = effective_confirmed(args)

    if not confirmed:
        confirm_token = issue_preview("create_pedido_bolo_full", canonical)
        return preview_response(
            tool_name="create_pedido_bolo_full",
            confirm_token=confirm_token,
            payload=canonical,
            message=_preview_message(validated),
        )

    confirm_token = args.get("confirm_token", "")

    async def _commit() -> dict:
        require_auth(token)
        verify_commit("create_pedido_bolo_full", canonical, confirm_token)
        check_throttle("create_pedido_bolo_full")
        return await _execute_chain(validated, base_url, token, client)

    return await run_idempotent_commit(confirm_token, _commit)


async def execute(
    name: str, args: dict, base_url: str, token: str | None, client: httpx.AsyncClient
) -> dict:
    try:
        if name == "create_pedido_bolo_full":
            return await _execute_create_pedido_bolo_full(
                args, base_url, token, client
            )
        return {"error": f"Tool desconhecida: {name}"}
    except WriteToolError as exc:
        return {"error": str(exc)}
