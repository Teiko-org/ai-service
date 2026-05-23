# Core — Logica do Assistente e Integracao Gemini

Nucleo do servico: client Gemini, loop de function calling com fallback automatico de modelos, sessoes de conversa, cache, prompts e infra compartilhada.

## Modulos

| Arquivo          | Responsabilidade                                                     |
|------------------|----------------------------------------------------------------------|
| `assistant.py`   | `CarambolosAssistant` — orquestra ask() e generate_insights() com fallback |
| `gemini.py`      | Singleton do `genai.Client` (inicializado com API key)               |
| `prompts.py`     | SYSTEM_PROMPT e INSIGHTS_PROMPT (persona + regras + politica de conteudo) |
| `model_catalog.py` | Cadeia padrao de modelos Gemini (texto + tools; exclui Live/TTS/imagem) |
| `model_manager.py` | Gerenciador de modelos com fallback automatico e cooldown por modelo |
| `api_key_manager.py` | Rotacao de `GEMINI_API_KEYS` em 429/503 |
| `alerts.py`      | Heuristicas de alertas V2, cache e refresh em background (lifespan)  |
| `sessions.py`    | SessionStore in-memory com TTL, cleanup automatico e historico       |
| `cache.py`       | SimpleCache thread-safe com TTL (usado para insights e alertas)     |
| `http_client.py` | Singleton `httpx.AsyncClient` com lifecycle (cleanup no shutdown)    |
| `limiter.py`     | Instancia do slowapi Limiter (importada pelas routes)                |

## Function Calling Loop (assistant.py)

O metodo `ask()` implementa o loop de function calling do Gemini:

1. Constroi historico de conversa a partir da sessao (`_build_history_contents`)
2. Envia pergunta + historico + TOOL_DECLARATIONS ao Gemini via `_generate_with_fallback`
3. Se resposta contem `function_call` → executa via registry → envia `function_response` de volta
4. Repete ate max `MAX_TOOL_ROUNDS=5` ou Gemini responder com texto
5. O Gemini pode chamar multiplas tools num unico turno (parallel function calling)

O metodo `generate_insights()` e diferente: NAO usa function calling do Gemini. Ele chama as tools diretamente via `asyncio.gather()` (paralelo), concatena os dados, e pede ao Gemini pra analisar e gerar JSON de insights.

## Fallback automatico (model_manager + api_key_manager + assistant.py)

O `_generate_with_fallback()` encapsula toda chamada ao Gemini:

1. `api_key_manager.get_key_index()` + `model_manager.get_model()` escolhem chave e modelo
2. Em 429/503: troca de modelo via `model_manager.mark_rate_limited()`
3. Se nao houver modelo disponivel na chave atual: `api_key_manager.mark_rate_limited()` e nova chave (`get_client(idx)`)
4. 404 (modelo invalido): cooldown longo no modelo, sem trocar de chave
5. Tentativas ate `max(9, keys * models)`; se esgotar chaves e modelos, `RateLimitError`

Cadeia padrao (`DEFAULT_GEMINI_FALLBACK_MODELS` em `model_catalog.py`; `GEMINI_MODEL` vem primeiro; override via `GEMINI_FALLBACK_MODELS` no `.env`):
- `gemini-2.5-flash-lite` → `gemini-2.5-flash` → `gemini-3.1-flash-lite` → `gemini-3.5-flash` → `gemini-2.5-pro`

Descobrir modelos habilitados na chave: `python scripts/list_gemini_models.py` na raiz do `ai-service`.

## Sessoes de conversa (sessions.py)

- `SessionStore` gerencia sessoes in-memory com `threading.Lock`
- Cada sessao tem historico (max 50 mensagens), `created_at` e `last_active`
- TTL de 30 minutos — sessao expira apos 30 min de inatividade
- Cleanup automatico via `threading.Timer` a cada 5 minutos
- `session_store` e singleton importado pelas routes

## Cache (cache.py)

- `SimpleCache` thread-safe com TTL por entrada
- Usado para cachear insights (TTL 5 min) evitando chamadas repetidas ao Gemini
- Metodos: `get(key)`, `set(key, value, ttl)`, `invalidate(key)`
- `cache` e singleton importado pelas routes

## Prompts (prompts.py)

- `SYSTEM_PROMPT` — Define persona (Kuroko), regras de resposta, contexto do negocio, politica de conteudo (recusa de off-topic, redirecionamento de saudacoes, bloqueio de profanidade) e instrucoes anti-jailbreak
- `INSIGHTS_PROMPT` — Template que pede insights curtos e diretos (max 2 frases) em formato JSON especifico

## Invariantes

- Todas as chamadas ao Gemini DEVEM usar `_generate_with_fallback()` para aproveitar o fallback
- Todas as chamadas ao Gemini DEVEM ser async (`client.aio.models.generate_content`)
- `genai.errors.APIError` com code 429 DEVE ser tratado por model_manager e api_key_manager, NAO propagado direto
- `gemini.py`, `http_client.py`, `session_store`, `cache`, `model_manager` e `api_key_manager` sao singletons
- `http_client.py` e fechado via lifespan do FastAPI em `main.py`
- O modelo Gemini vem de `model_manager.get_model()`, NUNCA hardcodar

## Pitfalls

- `response.candidates` pode ser vazio (safety filter, quota) — sempre verificar antes de acessar
- Insights JSON do Gemini as vezes vem envolto em ```json ... ``` — o parser em assistant.py trata
- `MAX_TOOL_ROUNDS=5` existe pra evitar loop infinito
- Apos tools, se a resposta vier sem texto, `assistant` pode chamar `_recover_text_after_tools` (custo extra de uma geracao)
- Referencia a pedido na conversa (`Pedido #42`, `pedido 42`) e normalizada em `app.tools.order_ref` antes das chamadas HTTP em `actions` e `deep_orders`
- Sessoes sao in-memory — restart do servico perde todas as sessoes (aceitavel para projeto academico)
- Cache e in-memory — restart do servico limpa o cache (mesma razao acima)
- Cooldowns do model_manager tambem resetam no restart
