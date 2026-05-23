"""Trechos de prompt reutilizados em system prompt, descriptions e instructions.

Centralizar aqui evita que a mesma regra (numero de pedido, two-step, fornada
ativa) seja reescrita em 5 lugares e fique fora de sincronia. Texto enxuto,
sem acentos, alinhado ao restante de `prompts.py`.
"""

ORDER_NUMBER_RULE = (
    "Numero de pedido visivel ao usuario = id do resumo (Pedido #X no Kanban, "
    "app e WhatsApp). Use sempre esse numero em order_id; nunca cite "
    "pedido_bolo_id, numeroPedido ou outros ids internos."
)

WRITE_TWO_STEP_RULE = (
    "Acoes destrutivas seguem two-step: 1) chame com confirmed=False e mostre "
    "a previa (message). 2) aguarde sim/Confirmar em NOVA mensagem do usuario. "
    "3) so entao chame de novo com confirmed=True + o mesmo confirm_token."
)

ACTIVE_BATCH_RULE = (
    "So pode haver uma fornada ativa. Se existir, use replace_active_batch "
    "(troca em uma confirmacao) ou close_batch (encerra)."
)

BATCH_STATUS_LANGUAGE = (
    "ATIVA/ativo=true = aberta no sistema (nao encerrada), NAO significa 'forno "
    "rolando agora'. Sempre separe: status no sistema (aberta vs encerrada) e "
    "periodo no calendario (futuro, em curso, ja passou). get_next_batch pode "
    "retornar fornada aberta com periodo futuro — diga que esta aberta no app e "
    "que o periodo comeca na data X. Evite 'esta ativa' ou 'em andamento' sozinhos."
)

CAKE_FILLING_MODES = (
    "Recheio: exatamente um modo — recheio_exclusivo_id/recheio_exclusivo_nome, "
    "OU recheio_unitario_id/recheio_nome, OU par recheio_unitario_1 + "
    "recheio_unitario_2."
)

NEVER_ASK_IDS = (
    "NUNCA peca IDs ao usuario. Resolva por nome (massa_nome, recheio_nome, "
    "produto_nome) ou consulte catalogo internamente."
)
