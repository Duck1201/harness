# O system prompt é derivado dos contratos

O prompt do sistema é construído por `build_system_prompt` a partir do RuntimeProfile ativo, não escrito à mão. As capacidades declaradas em [`model-profiles.json`](../../config/model-profiles.json) decidem o que o modelo é informado que a sessão não tem, então divergência entre contrato e prompt deixa de ser possível. O motivo é medido: pedido para ler uma imagem, o modelo gastou seis chamadas procurando o arquivo no workspace e na web, enquanto o perfil declarava `vision.support: unknown` desde sempre; e, depois de escolher a tool certa, chamava outra para conferir, porque nada dizia que um ResultPayload é a resposta.

Só entram capacidades cuja ausência muda o que o modelo deve fazer. `streaming` e `parallel_tool_calls` são assunto do harness e virariam ruído num modelo de 4B; capacidade sem consequência declarável fica de fora, e essas consequências vivem junto da função que monta o prompt.

O corpus e a produção usam a mesma função: um prompt de avaliação diferente do de produção mede outro sistema.
