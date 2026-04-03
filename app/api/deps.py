import re
from fastapi import Request, HTTPException


MAX_QUESTION_LENGTH = 1000
BLOCKED_PATTERNS = re.compile(
    r"(ignore\s+(todas|all|previous|anterior)|"
    r"esqueca\s+(suas|tuas|as)\s+instrucoes|"
    r"forget\s+your\s+instructions|"
    r"system\s*prompt|"
    r"reveal\s+your|"
    r"mostre\s+(seu|suas)\s+(prompt|instrucoes))",
    re.IGNORECASE,
)


def sanitize_input(text: str) -> str:
    text = text.strip()
    if len(text) > MAX_QUESTION_LENGTH:
        text = text[:MAX_QUESTION_LENGTH]
    return text


def check_prompt_injection(text: str) -> None:
    if BLOCKED_PATTERNS.search(text):
        raise HTTPException(
            status_code=400,
            detail="A pergunta contem padroes nao permitidos.",
        )


async def validate_auth_token(request: Request) -> str | None:
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Formato de token invalido.")
    return auth_header.split(" ", 1)[1]
