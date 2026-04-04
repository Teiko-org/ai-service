import re
from fastapi import Request, HTTPException


MAX_QUESTION_LENGTH = 1000

GUARDRAIL_MESSAGE = (
    "Sou a Kuroko, assistente de dados da Carambolos. "
    "Só posso ajudar com análises e informações sobre o negócio. "
    "Como posso te ajudar com isso?"
)

INJECTION_PATTERNS = re.compile(
    r"("
    r"ignore\s+(todas|all|previous|anterior)"
    r"|esqueca\s+(suas|tuas|as)\s+instrucoes"
    r"|forget\s+your\s+instructions"
    r"|system\s*prompt"
    r"|reveal\s+your"
    r"|mostre\s+(seu|suas)\s+(prompt|instrucoes)"
    r"|atue\s+como"
    r"|finja\s+que\s+(e|eh|voce|vc)"
    r"|pretend\s+(you|to\s+be)"
    r"|act\s+as"
    r"|roleplay"
    r"|jailbreak"
    r"|ignore\s+all\s+instructions"
    r"|override\s+(your|the)\s+"
    r"|bypass\s+(your|the|security)"
    r"|desabilit(e|ar)\s+(suas|as)\s+regras"
    r"|modo\s+(sem\s+restricao|irrestrito|developer|desenvolvedor)"
    r"|unrestricted\s+mode"
    r"|DAN\s+mode"
    r")",
    re.IGNORECASE,
)

PROFANITY_PATTERNS = re.compile(
    r"\b("
    r"porra|caralho|merda|foda[\-\s]?se|fdp|pqp|vsf|tnc"
    r"|puta\s*(que\s*pariu|merda)?"
    r"|arrombad[oa]|cuzao|cuzão|babaca|idiota|imbecil"
    r"|vai\s+(se\s+)?f[ou]der"
    r"|fuck|shit|bitch|asshole|damn\s+you"
    r"|filh[oa]\s+d[aeu]\s+(uma\s+)?put[ao]"
    r"|desgraça(do|da)?"
    r"|otari[oa]"
    r"|lixo\s+de\s+(ia|bot|assistente)"
    r")\b",
    re.IGNORECASE,
)

OFF_TOPIC_PATTERNS = re.compile(
    r"("
    r"conte?\s+(uma?\s+)?(piada|historia|conto)"
    r"|escreva\s+(um|uma)\s+(poema|musica|redacao|carta|texto\s+sobre)"
    r"|fale\s+sobre\s+(politica|religiao|futebol|esporte|jogo)"
    r"|quem\s+vai\s+ganhar"
    r"|o\s+que\s+voce\s+acha\s+de"
    r"|sua\s+opiniao\s+sobre"
    r"|me\s+ajud[ae]\s+com\s+(licao|dever|prova|trabalho\s+de\s+escola)"
    r"|traduza?\s+(isso|para|em)"
    r"|gere?\s+(um\s+)?(codigo|script|programa|sql|query)"
    r"|crie\s+(um\s+)?app"
    r"|tell\s+me\s+a\s+joke"
    r"|write\s+(a|me)\s+"
    r")",
    re.IGNORECASE,
)


def sanitize_input(text: str) -> str:
    text = text.strip()
    if len(text) > MAX_QUESTION_LENGTH:
        text = text[:MAX_QUESTION_LENGTH]
    return text


def check_prompt_injection(text: str) -> None:
    if INJECTION_PATTERNS.search(text):
        raise HTTPException(status_code=400, detail=GUARDRAIL_MESSAGE)


def check_content_policy(text: str) -> None:
    if PROFANITY_PATTERNS.search(text):
        raise HTTPException(status_code=400, detail=GUARDRAIL_MESSAGE)
    if OFF_TOPIC_PATTERNS.search(text):
        raise HTTPException(status_code=400, detail=GUARDRAIL_MESSAGE)


async def validate_auth_token(request: Request) -> str | None:
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Formato de token invalido.")
    return auth_header.split(" ", 1)[1]
