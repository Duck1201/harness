# Avaliações do Harness 2.0

Este diretório transforma a herança empírica e o contrato vigente em avaliações
web repetíveis. As fixtures não copiam conteúdo de produção: reproduzem o
formato das falhas observadas e os invariantes atuais.

## Arquivos

- [`fixtures/regressions.json`](fixtures/regressions.json): casos determinísticos
  de seleção, segurança, parsing, loop, contexto, backend e automações;
- [`experiments.json`](experiments.json): braços, fatores fixos, métricas e gates
  para hipóteses ainda abertas.

## Unidade de avaliação

O resultado principal é a tarefa completa, não apenas a primeira tool call. Cada
runner deve registrar:

1. RuntimeProfile e digest completos;
2. ExecutionRoute, fixture, dataset_version e digests de contratos;
3. sequência de AgentSteps, decisões e model_tools;
4. validação de argumentos e efeitos realmente aplicados;
5. TerminalOutcome e TaskVerdict, sem conflar os dois;
6. tokens, latência, repetição e violações de segurança.

Conteúdo integral fica no Workspace/Conversation isolado da execução. O store de
métricas recebe somente IDs, hashes, tamanhos, contagens, classes e tempos.

## Protocolo

1. Crie os dois stores, uma Conversation e um Workspace exclusivos por braço.
2. Use path temporário curto e de mesmo comprimento entre os braços.
3. Fixe RuntimeProfile, ExecutionRoute, dataset_version e digests de dataset,
   harness e registry.
4. Randomize a ordem, registre seed/ordem e não reutilize cache entre braços.
5. Execute primeiro o piloto de 15 casos por braço.
6. Trate o piloto apenas como direção.
7. Para promoção, execute 50 casos por braço em três ordens/seeds e reporte
   intervalo, salvo falha de segurança, que reprova imediatamente.
8. Execute a tarefa fim a fim na superfície web e guarde os casos que falharam;
   não substitua o corpus por exemplos fáceis.

## Digests

Fixtures e experimentos usam SHA-256 de JSON canônico UTF-8, sem espaços e com
chaves ordenadas recursivamente. O campo `digest_contract.scope` enumera, em
ordem, os campos cobertos; o próprio campo de digest fica fora do cálculo. O
validador recalcula dataset, manifesto de experimentos e snapshots dos três
contratos JSON, portanto um digest não pode ser atualizado sem os bytes que ele
identifica.

## Estados de um experimento

- `reproduce_inherited_result`: repete primeiro uma observação do 1.0;
- `required_before_default_change`: impede alterar o baseline;
- `required_before_default_enablement`: impede ligar uma hipótese por padrão;
- `blocked_*`: faltam manifesto, parser ou outra pré-condição de experimento;
- `optional_after_baseline`: otimização que não bloqueia o primeiro release;
- `deferred_*`: só deve consumir engenharia quando seu gatilho ocorrer.

## Validação estrutural

Execute:

```bash
node scripts/validate-contracts.mjs
```

O comando valida JSON, RuntimeProfile/ExecutionRoute, as três coleções do
registry, efeitos e grants, ResultPayload, fixtures/oráculos, digests e links
Markdown locais. Resultados de experimento podem permanecer ausentes; digests e
gates contratuais não.
