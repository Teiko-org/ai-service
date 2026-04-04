# AI Service — Assistente Inteligente Carambolos

Microservico Python/FastAPI que funciona como Assistente Inteligente de Negocio para a confeitaria Carambolos. Consome a API REST do backend Java (Spring Boot) e usa Google Gemini com Function Calling para responder perguntas em linguagem natural e gerar insights proativos.

## Arquitetura

```
Mobile/Web → FastAPI (este servico) → Gemini API (Function Calling)
                                          ↓
                                    Backend Java (Spring Boot :8080)
                                          ↓
                                       MySQL 8
```

Este servico NAO acessa o banco diretamente. Toda leitura de dados passa pelas tools que chamam endpoints REST do backend Java.

## Estrutura

- `app/api/` — Rotas HTTP e seguranca (deps.py). Sem AGENTS.md proprio, coberto por este node.
- `app/core/` — Logica do assistente, client Gemini, prompts, sessoes, cache, gerenciador de modelos, rate limiter, http client compartilhado. Ver `app/core/AGENTS.md`.
- `app/tools/` — Function Declarations do Gemini mapeadas a endpoints do backend. Ver `app/tools/AGENTS.md`.
- `app/models/` — Schemas Pydantic (request/response). Arquivo unico, coberto por este node.
- `tests/` — Testes com pytest. Mocks para Gemini, TestClient do FastAPI.

## Endpoints

| Metodo | Rota                       | Rate Limit  | Auth    | Descricao                          |
|--------|----------------------------|-------------|---------|-------------------------------------|
| GET    | /api/v1/health             | 60/min      | Nao     | Health check                        |
| POST   | /api/v1/ask                | 15/min      | Bearer  | Pergunta em linguagem natural       |
| POST   | /api/v1/insights           | 10/min      | Bearer  | Insights proativos (com cache 5min) |
| GET    | /api/v1/suggested-prompts  | 60/min      | Nao     | Prompts pre-definidos (pills)       |
| GET    | /api/v1/models-status      | 60/min      | Nao     | Status/cooldown dos modelos Gemini  |

## Configuracao

Tudo via `.env` / variavel de ambiente. Validado por `pydantic-settings` em `app/config.py`:
- `GEMINI_API_KEY` (obrigatorio) — Chave do Google AI Studio
- `GEMINI_MODEL` — Modelo Gemini principal (default: `gemini-2.5-flash-lite`)
- `CARAMBOLOS_API_URL` — URL do backend Java (default: `http://localhost:8080`)
- `ALLOWED_ORIGINS` — CORS origins separados por virgula
- `LOG_LEVEL` — DEBUG, INFO, WARNING, ERROR

Modelos de fallback definidos em `FALLBACK_MODELS` no `config.py`:
- `gemini-2.5-flash-lite` (principal — 15 RPM, 1.000 RPD)
- `gemini-2.5-flash` (fallback — 10 RPM, 250 RPD)
- `gemini-2.5-pro` (ultimo recurso — 5 RPM, 100 RPD)

## Seguranca (Defense in Depth)

1. **CORS** — Origins restritas via config
2. **Rate limiting** — slowapi, limites por endpoint (mais restrito em /ask e /insights)
3. **Sanitizacao** — Input truncado a 1000 chars, strip
4. **Prompt guard** — Regex em `deps.py` bloqueia padroes de injection (PT e EN)
5. **Content guardrails** — Filtros de profanidade e conteudo off-topic em `deps.py`, retorna mensagem amigavel (HTTP 400)
6. **System prompt** — Instrucoes anti-jailbreak e politica de conteudo embutidas no prompt do Gemini
7. **Auth** — Bearer token repassado ao backend Java (validacao JWT e feita la)

## Fluxo principal (/ask)

1. Request chega → sanitize → check injection → check content policy → extract token
2. SessionStore busca/cria sessao e carrega historico (ultimas 10 mensagens)
3. `CarambolosAssistant.ask()` envia pergunta + historico + TOOL_DECLARATIONS ao Gemini
4. `_generate_with_fallback()` tenta o modelo principal; se 429, tenta fallback automatico
5. Se resposta contem `function_call(s)` → registry despacha para executor correto
6. Executor chama endpoint do backend Java via httpx compartilhado
7. Resultado volta pro Gemini como function_response
8. Loop ate max 5 rounds ou Gemini responder com texto final
9. Mensagens (user + assistant) sao salvas na sessao
10. Response com `answer`, `tools_used` e `session_id`

## Fluxo de insights (/insights)

1. Verifica cache (chave: `insights:{context}`, TTL 5 min)
2. Se cache hit, retorna direto sem chamar Gemini
3. Se cache miss, executa tools do contexto em paralelo (asyncio.gather)
4. Envia dados coletados ao Gemini com INSIGHTS_PROMPT
5. Parseia resposta JSON (trata markdown code fences)
6. Cacheia resultado e retorna

## Anti-patterns

- NUNCA acessar banco diretamente — toda leitura e via tools → backend Java
- NUNCA instanciar `httpx.AsyncClient` dentro de tools — usar `get_http_client()` do modulo compartilhado
- NUNCA hardcodar modelo Gemini — usar `settings.gemini_model` ou `model_manager.get_model()`
- NUNCA chamar `client.models.generate_content()` (sincrono) — usar `client.aio.models.generate_content()` (async)
- NUNCA expor detalhes de erro do Gemini pro usuario final — logar e retornar mensagem generica

## Testes

`pytest` com mocks. Rodar: `.venv/Scripts/python.exe -m pytest tests/ -v`
- `test_deps.py` — Sanitizacao, deteccao de injection e guardrails de conteudo
- `test_schemas.py` — Validacao Pydantic
- `test_routes.py` — Endpoints com mock do CarambolosAssistant
