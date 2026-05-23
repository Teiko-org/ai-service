"""Mensagens humanas para confirmacao de acoes (sem jargao tecnico)."""

from __future__ import annotations


def _is_business_rule_error(message: str) -> bool:
    low = (message or "").lower()
    markers = (
        "fornada ativa",
        "replace_active_batch",
        "close_batch",
        "nao use create_batch",
        "substituir a atual",
    )
    return any(m in low for m in markers)


def _is_bolo_catalog_error(message: str) -> bool:
    low = (message or "").lower()
    return (
        "massas disponiveis" in low
        or "recheios unitarios disponiveis" in low
        or "recheios exclusivos disponiveis" in low
        or ("massa '" in low and "nao encontrada no cadastro" in low)
        or ("recheio '" in low and "nao encontrado no cadastro" in low)
    )


def humanize_confirm_error(message: str) -> str:
    raw = (message or "").strip()
    if _is_bolo_catalog_error(raw):
        return raw
    low = raw.lower()
    if "fornada ativa" in low and "create_batch" in low:
        return (
            "Ja existe uma fornada aberta no sistema. "
            "Use o botao Confirmar na previa de substituicao, ou diga "
            "'substituir' (nova fornada) / 'so encerrar' (fecha a atual)."
        )
    if "fornada ativa" in low:
        return (
            "Ja existe uma fornada aberta no sistema. "
            "Diga 'substituir' com as datas desejadas ou 'so encerrar'."
        )
    if "expirou" in low:
        return (
            "Essa confirmacao expirou. Me diga de novo o que deseja alterar "
            "que eu mostro a previa outra vez."
        )
    if "ja foi processada" in low or "ja tinha" in low:
        return "Essa alteracao ja tinha sido feita."
    if "nao batem" in low or "invalido" in low:
        return (
            "Nao consegui concluir com essa confirmacao. "
            "Responda sim de novo ou toque em Confirmar na mensagem anterior."
        )
    if "faltou" in low or "aguardando" in low or "nao execute" in low:
        return (
            "Ainda preciso da sua confirmacao. "
            'Responda "sim" ou toque em Confirmar na previa acima.'
        )
    if "resposta explicita" in low or "mesmo turno" in low:
        return (
            "Aguarde: primeiro confirme a previa com sim ou no botao Confirmar, "
            "depois eu executo."
        )
    if "secret" in low or "servidor sem" in low:
        return (
            "Confirmacao indisponivel no servidor no momento. "
            "Avise o suporte tecnico."
        )
    if "nao identifiquei qual fornada" in low or "qual fornada usar" in low:
        return (
            "Nao sei em qual fornada colocar os produtos. "
            "Crie uma fornada antes ou diga o numero (ex.: fornada #12)."
        )
    if "quantidade" in low and "inteiro" in low:
        return (
            "Nao consegui montar a lista de produtos. "
            "Tente de novo com quantidades inteiras (ex.: 5 paes, 3 croissants)."
        )
    if "ambiguo" in low:
        return (
            "Encontrei mais de um produto parecido com o que voce pediu. "
            "Diga o nome exato como esta no cadastro ou seja mais especifico."
        )
    if "produto_nome" in low or "catalogo" in low or "nao encontrei" in low:
        return (
            "Nao encontrei esse produto no cadastro de fornada. "
            "Confira o nome (ex.: Pao Frances, Croissant) e tente de novo."
        )
    if "autenticado" in low:
        return (
            "Para criar fornada ou pedido pela IA, o app precisa enviar login "
            "(token) ao assistente — ou, em ambiente local, ative "
            "ALLOW_ANONYMOUS_WRITES=true no .env do ai-service e reinicie."
        )
    return (
        "Nao foi possivel concluir agora. "
        "Tente confirmar de novo com sim ou no botao Confirmar."
    )


def tool_result_for_llm(result: dict) -> dict:
    """Remove dados internos (HMAC) e deixa instrucoes claras para o modelo."""
    if not isinstance(result, dict):
        return result

    if result.get("requires_confirmation"):
        message = (result.get("message") or "").strip()
        return {
            "requires_confirmation": True,
            "message": message,
            "instruction": (
                "Responda ao usuario usando EXATAMENTE o texto do campo message, "
                "sem mencionar token, HMAC, API ou detalhes tecnicos. "
                "NUNCA chame esta tool com confirmed=True — o servidor executa "
                "quando o usuario disser sim ou tocar em Confirmar."
            ),
        }

    if result.get("error"):
        raw = str(result["error"])
        if _is_bolo_catalog_error(raw):
            return {
                "error": raw,
                "instruction": (
                    "Repasse ao usuario o erro completo (massa e recheio juntos, "
                    "se houver). Use os nomes legiveis do erro — combinacoes nomeadas "
                    "no formato Nome (Sabor1 + Sabor2). NAO chame get_fillings_catalog "
                    "nem despeje lista enorme de sabores avulsos. Peca um pedido "
                    "corrigido; nao peca confirmacao."
                ),
            }
        low = raw.lower()
        if any(
            x in low
            for x in (
                "quantidade",
                "produto_nome",
                "nao encontrei",
                "ambiguo",
                "linha",
            )
        ):
            return {
                "error": humanize_confirm_error(raw),
                "instruction": (
                    "Explique o erro ao usuario e chame add_batch_lines de novo "
                    "com lines contendo um item por produto: "
                    "produto_nome + quantidade (inteiro). Nao peca confirmacao generica."
                ),
            }
        if _is_business_rule_error(raw):
            return {
                "error": humanize_confirm_error(raw),
                "instruction": (
                    "Fornada ativa: chame replace_active_batch (confirmed=False) "
                    "com as datas que o usuario pediu — o servidor mostra previa "
                    "e botao Confirmar. Para so encerrar, close_batch (confirmed=False). "
                    "Nao fique repetindo a pergunta se o usuario ja disse sim: "
                    "execute a tool adequada."
                ),
            }
        return {
            "error": humanize_confirm_error(raw),
            "instruction": (
                "Explique o erro de forma simples. Se for confirmacao pendente, "
                "peca sim ou Confirmar — nao tente executar a acao de novo."
            ),
        }

    return result
