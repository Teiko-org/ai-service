import google.genai as genai

from app.config import settings

_clients: dict[int, genai.Client] = {}


def get_client(key_index: int | None = None) -> genai.Client:
    from app.core.api_key_manager import api_key_manager

    idx = key_index if key_index is not None else api_key_manager.get_key_index()
    keys = settings.gemini_api_keys_list
    if idx < 0 or idx >= len(keys):
        raise ValueError(f"Indice de API key invalido: {idx}")

    if idx not in _clients:
        _clients[idx] = genai.Client(api_key=keys[idx])
    return _clients[idx]
