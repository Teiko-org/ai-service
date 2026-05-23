# Tools — Function Declarations do Gemini

Cada modulo neste diretorio define Function Declarations que o Gemini pode chamar e executores que traduzem essas chamadas em requests HTTP ao backend Java.

## Numero do pedido (`order_ref.py`)

O mesmo identificador do **Pedido #X** no app e do WhatsApp e o `id` do resumo.
[order_ref.py](order_ref.py) normaliza `order_id` / itens de `order_ids` aceitando
inteiro ou strings como `#42`, `pedido 42`, `nº 7`. Usado em `actions` e
`deep_orders`.

## Como adicionar uma nova tool

1. Escolha o modulo correto (ou crie um novo para um dominio novo):
   - `dashboard.py` — Metricas gerais, contagens, rankings, pedidos recentes
   - `orders.py` — Pedidos agrupados por periodo, itens por periodo (tendencias)
   - `products.py` — KPIs de fornada (aproveitamento, valor, por periodo, por ID)
   - `production.py` — Massas/recheios pendentes, entregas proximas
   - `reports.py` — Sinaliza geracao de PDF (tool sintetica, nao chama backend)
   - `actions.py` — Acoes destrutivas (PATCH/POST) com confirmacao two-step (V2)
   - `deep_orders.py` — Detalhes e filtros de pedidos via ResumoPedidoController (V2)
   - `batches.py` — Gestao de fornadas (V2)
   - `catalog.py` — Catalogo / cardapio (produtos cadastrados, decoracoes, tamanhos) (V2)
   - `writes/` — Tools de **escrita** (V3): criam/alteram entidades; uso obriga `ENABLE_WRITE_TOOLS=true` + `CONFIRM_TOKEN_SECRET`. Cada tool segue o fluxo two-step com HMAC preview-token, anti-replay, throttle por sessao, validacao local e auth Bearer obrigatorio. Modulos: `fornada.py` (`create_batch`, `add_batch_lines`), `pedido_bolo.py` (`create_pedido_bolo_full`), roteadas por `router.py`.

2. Adicione a `FunctionDeclaration` na lista `DECLARATIONS`:
   ```python
   genai.types.FunctionDeclaration(
       name="nome_snake_case",
       description="Descricao clara do que retorna — o Gemini usa isso pra decidir quando chamar",
       parameters=genai.types.Schema(
           type=genai.types.Type.OBJECT,
           properties={...},  # {} se nao tem parametros
           required=[...],     # omitir se nao tem required
       ),
   )
   ```

3. Adicione o tratamento no `execute()` do mesmo modulo. Assinatura obrigatoria:
   ```python
   async def execute(name: str, args: dict, base_url: str, token: str | None, client: httpx.AsyncClient) -> dict:
   ```

4. Atualize `registry.py`: importe o modulo novo, concatene as `DECLARATIONS` em `TOOL_DECLARATIONS` e registre os executores em `_EXECUTORS`.

## Contrato da funcao execute()

- Recebe `httpx.AsyncClient` compartilhado — NUNCA criar um novo
- Retorna `dict` (JSON serializavel)
- Status 204 do backend → retornar `{"data": [], "message": "Nenhum dado encontrado"}`
- Erros sao capturados pelo registry — nao precisa try/except dentro do executor
- Token Bearer e repassado ao backend via header Authorization
- Em tools que recebem **pedido / resumo**, prefira reutilizar `parse_resumo_order_id` / `parse_resumo_order_id_list` de `order_ref.py` para aceitar o mesmo formato que o usuario ve no app (`#42`, `pedido 42`).

## Padrao Two-Step Confirmation (tools de acao — V2)

As tools que ALTERAM estado seguem o padrao agentic com confirmacao humana:

1. Primeira chamada com `confirmed=False` (default): a tool NAO chama o backend. Retorna `requires_confirmation: True` + previa do pedido.
2. O modelo apresenta a previa ao usuario e aguarda confirmacao explicita ("sim", "confirma").
3. Segunda chamada com `confirmed=True`: a tool executa o PATCH/POST.

O SYSTEM_PROMPT em `app/core/prompts.py` instrui o modelo a SEMPRE seguir esse fluxo. Tools puramente READ (`generate_whatsapp_message`, deep_orders, batches, catalog) nao precisam de confirmacao.

## Mapeamento tool → endpoint backend

### dashboard.py
| Tool                        | Endpoint backend                         |
|-----------------------------|------------------------------------------|
| get_unique_clients_count    | GET /dashboard/qtdClientesUnicos         |
| get_orders_count            | GET /dashboard/qtdPedidos                |
| get_cake_orders_count       | GET /dashboard/qtdPedidosBolo            |
| get_batch_orders_count      | GET /dashboard/qtdPedidosFornada         |
| get_top_products            | GET /dashboard/produtosMaisPedidos       |
| get_top_cakes               | GET /dashboard/bolosMaisPedidos          |
| get_top_batch_products      | GET /dashboard/produtosFornadasMaisPedidos|
| get_recent_orders           | GET /dashboard/ultimosPedidos            |

### orders.py
| Tool                        | Endpoint backend                              | Params             |
|-----------------------------|-----------------------------------------------|--------------------|
| get_cake_orders_by_period   | GET /dashboard/qtdPedidosBoloPorPeriodo       | periodo, ano       |
| get_batch_orders_by_period  | GET /dashboard/qtdPedidosFornadaPorPeriodo    | periodo, ano       |
| get_items_by_period         | GET /dashboard/itens-mais-pedidos-por-periodo | tipoItem, periodo, ano |

### products.py (KPIs fornada)
| Tool                        | Endpoint backend                          | Params             |
|-----------------------------|-------------------------------------------|--------------------|
| get_latest_batch_kpi        | GET /dashboard/kpi-fornada-mais-recente   | —                  |
| get_batch_kpi_by_period     | GET /dashboard/kpi-fornadas-por-periodo   | periodo, ano, mes  |
| get_batch_kpi_by_id         | GET /dashboard/kpi-fornada/{id}           | —                  |

### production.py
| Tool                        | Endpoint backend                          | Params             |
|-----------------------------|-------------------------------------------|--------------------|
| get_pending_doughs          | GET /dashboard/massas-pendentes           | —                  |
| get_pending_fillings        | GET /dashboard/recheios-pendentes         | —                  |
| get_upcoming_deliveries     | GET /dashboard/pedidos-proximos-entrega   | diasProximos       |

### reports.py
| Tool                       | Comportamento                                            |
|----------------------------|----------------------------------------------------------|
| generate_insights_report   | Sinaliza PDF — frontend baixa /relatorios/insights       |

### actions.py (V2 — two-step confirmation)
| Tool                        | Endpoint backend                            | Confirmacao? |
|-----------------------------|---------------------------------------------|--------------|
| mark_order_as_paid          | PATCH /resumo-pedido/{id}/pago              | sim          |
| mark_order_as_completed     | PATCH /resumo-pedido/{id}/concluido         | sim          |
| mark_order_as_cancelled     | PATCH /resumo-pedido/{id}/cancelado         | sim          |
| mark_order_as_pending       | PATCH /resumo-pedido/{id}/pendente          | sim          |
| generate_whatsapp_message   | POST /resumo-pedido/mensagens               | nao (read)   |

### deep_orders.py (V2)
| Tool                        | Endpoint backend                                         | Params                |
|-----------------------------|----------------------------------------------------------|-----------------------|
| get_order_summary_by_id     | GET /resumo-pedido/{id}                                  | order_id              |
| get_cake_order_details      | GET /resumo-pedido/pedido-bolo/detalhe/{id}              | order_id              |
| get_batch_order_details     | GET /resumo-pedido/pedido-fornada/detalhe/{id}           | order_id              |
| get_orders_by_status        | GET /resumo-pedido/status/{STATUS}                       | status                |
| get_orders_by_delivery_date | GET /resumo-pedido/pedido-bolo/por-data-entrega          | dataEntrega, status?  |
| get_orders_by_dough         | GET /resumo-pedido/pedido-bolo/por-massa/{massaId}       | dough_id, status?     |
| get_orders_by_filling       | GET /resumo-pedido/pedido-bolo/por-recheio/{recheioId}   | filling_id, status?   |

### batches.py (V2)
| Tool                        | Endpoint backend                            | Params       |
|-----------------------------|---------------------------------------------|--------------|
| get_next_batch              | GET /fornadas/proxima                       | —            |
| get_active_batches          | GET /fornadas                               | —            |
| get_all_batches             | GET /fornadas/todas                         | —            |
| get_batches_by_month        | GET /fornadas/com-itens                     | ano, mes     |
| get_products_in_batch       | GET /fornadas/da-vez/produtos/{batchId}     | batch_id     |
| get_latest_batch_products   | GET /fornadas/mais-recente/produtos         | —            |

### catalog.py (V2)
| Tool                       | Endpoint backend                | Params |
|----------------------------|---------------------------------|--------|
| get_registered_products    | GET /dashboard/produtosCadastrados | —    |
| get_decorations            | GET /decoracoes                 | —      |
| get_cake_sizes             | GET /bolos/tamanhos             | —      |
| get_cake_formats           | GET /bolos/formatos             | —      |
| get_doughs_catalog         | GET /bolos/massa                | —      |
| get_fillings_catalog       | GET /bolos/recheio-unitario     | —      |
| get_exclusive_fillings_catalog | GET /bolos/recheio-exclusivo | —      |

### batches.py extras
| Tool                              | Endpoint backend                            |
|-----------------------------------|---------------------------------------------|
| get_active_batch_with_products    | GET /fornadas + /fornadas/da-vez/produtos/{id} |

### writes/fornada.py (V3 — escrita, two-step com HMAC)
Disponivel apenas quando `ENABLE_WRITE_TOOLS=true`. Cada commit valida HMAC do `confirm_token`, anti-replay, mensagem nova do usuario entre preview e commit, throttle por sessao e Bearer obrigatorio (salvo `ALLOW_ANONYMOUS_WRITES=true` em dev local).

| Tool                  | Endpoint backend                | Params                                                | Confirmacao? |
|-----------------------|---------------------------------|-------------------------------------------------------|--------------|
| create_batch          | POST /fornadas                  | data_inicio, data_fim (yyyy-MM-dd ou dd/MM/yyyy)      | sim (HMAC)   |
| add_batch_lines       | POST /fornadas/da-vez (loop)    | fornada_id?, lines[{produto_nome ou produto_fornada_id, quantidade}] | sim (HMAC)   |
| close_batch           | DELETE /fornadas/{id}           | fornada_ids[] (1..20)                                 | sim (HMAC)   |
| replace_active_batch  | DELETE ativa + POST nova        | data_inicio, data_fim                                 | sim (HMAC)   |

Helpers compartilhados em `writes/`:

- `_helpers.py` — auth, throttle, HMAC issue/verify, idempotency, post/get/delete sanitizados (logam payload completo, devolvem mensagem curta).
- `schema_shared.py` — `confirmed_param`, `confirm_token_param` para FunctionDeclarations.
- `dates.py` — `parse_user_date` aceita `yyyy-MM-dd` e `dd/MM/yyyy`.
- `text_match.py` — `normalize_text`, `match_by_name`, `pick_highest_id` para resolvers.
- `batch_overlap.py` — `pick_active_batch` (compartilhada com `tools/batches.py`), `find_active_batch`.
- `product_resolve.py` — nome → `produto_fornada_id` (catalogo /dashboard/produtosCadastrados, tipo=FORNADA).
- `bolo_catalog_resolve.py` — nome → massa/recheio unitario/recheio exclusivo + `apply_catalog_names` (preenche IDs + rotulos para a previa).

Validacoes locais antes de tocar o backend:

- `create_batch`: datas `yyyy-MM-dd`, `data_inicio >= hoje`, `data_fim >= data_inicio`, `data_fim <= hoje + 365d`.
- `add_batch_lines`: `fornada_id` int positivo, `lines` 1..50 itens, cada `produto_fornada_id` int positivo, `quantidade` 1..10000.

### writes/pedido_bolo.py (V3 — cadeia unica, rollback best-effort)

| Tool                     | Cadeia backend (commit unico)                                                                 | Confirmacao? |
|--------------------------|-----------------------------------------------------------------------------------------------|--------------|
| create_pedido_bolo_full  | POST recheio-pedido → POST bolo → POST pedido → POST resumo-pedido (+ POST enderecos se ENTREGA com `endereco`) | sim (HMAC)   |

Em falha apos passo parcial, o executor tenta DELETE reverso (resumo → pedido → bolo → recheio-pedido → endereco criado). Nao e transacao atomica — preferir endpoint agregador no Java se quota/robustez forem criticas.

Validacoes locais: enums `formato`/`tamanho`/`tipo_entrega`, recheio exclusivo XOR unitarios, ENTREGA exige endereco, RETIRADA exige `horario_retirada`, datas `yyyy-MM-dd`, `cobertura_id` opcional (busca primeira cobertura ou cria padrao no commit).

## Anti-patterns

- NUNCA usar `httpx.AsyncClient()` direto — o client vem como parametro via registry
- NUNCA retornar dados que nao vieram do backend — o Gemini nao deve receber dados inventados
- Descricao da FunctionDeclaration deve ser precisa — o Gemini decide qual tool chamar baseado NELA
- Nomes de tools em snake_case, parametros em snake_case (mapeados pra camelCase do backend dentro do executor)
- Tools de acao SEMPRE precisam do parametro `confirmed` e do fluxo two-step — nunca executar PATCH/POST direto
- Tools de escrita V3 (writes/) SEMPRE precisam dos helpers em `writes/_helpers.py` (issue_preview, verify_commit, check_throttle, require_auth) — nunca implementar o fluxo manualmente
