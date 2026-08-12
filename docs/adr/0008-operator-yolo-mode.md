# O Operator pode desligar toda confirmação

Sem uma forma de parar de perguntar, o harness não é um agente: cada escrita e cada acesso à rede param o Turn até alguém clicar, e uma tarefa de dez passos vira dez interrupções. O modo yolo é a decisão permanente do Operator de responder sim antes da pergunta: enquanto estiver ligado, o gate aprova toda confirmação e concede os grants que faltarem, sem anunciar nada.

Ele cobre também a chamada sob `UntrustedWebTaint`, que é o caso que a confirmação existia para segurar: uma página hostil que o modelo leu pode passar a dirigir uma escrita no Workspace. Essa consequência é o preço declarado da autonomia, e o desenho a mantém visível em vez de escondê-la — o modo nasce desligado, é ligado por ato explícito, vale por host com opt-out por Conversation, e cada chamada assim aprovada entra no CanonicalHistory como `waived`, nunca como `approved`, de modo que ler o histórico distingue o que o Operator olhou do que ele autorizou de antemão.

O que não muda: o modelo continua sem poder conceder nada a si mesmo. Quem concede é a decisão permanente do Operator, registrada em [`config/harness.json#policy.operator_confirmation.yolo`](../../config/harness.json), e desligar o modo volta a valer imediatamente porque o estado é resolvido a cada pergunta, não copiado para as Conversations.
