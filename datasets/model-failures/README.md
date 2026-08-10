# Traces do modelo no harness

Registro do que o modelo **fez**, não só do veredicto. Serve para fine-tuning
futuro: cada linha traz o pedido, as tools oferecidas, cada chamada emitida,
cada recusa que o harness devolveu, a resposta final e as asserções que o
oráculo reprovou.

Este diretório **não é contrato**. Não tem digest, não entra em
`scripts/validate-contracts.mjs` e não é lido por código de produção. O contrato
versionado continua sendo `evals/fixtures/regressions.json` — aqui é saída de
execução, não fonte de verdade.

## Por que existe um coletor separado

Uma execução de eval não guarda isso. `EvalStore` grava veredicto, TerminalOutcome
e contagens, por contrato de privacidade, e cada caso roda num workspace
temporário que é apagado no fim. A evidência existe apenas dentro do
`CaseRunResult`, em memória. O coletor re-executa as fixtures de modelo e
serializa essa evidência.

## Regenerar

```bash
.venv/bin/python scripts/collect-model-traces.py
.venv/bin/python scripts/collect-model-traces.py --seed 104729 --seed 7 --output outro.jsonl
```

Sem `--seed`, usa as três sementes registradas do protocolo. Repetir a mesma
semente produz cópias, não evidência nova: a semente é o que varia uma execução
local.

## Esquema, uma linha por caso

| Campo | Conteúdo |
|---|---|
| `fixture_id`, `fixture_type`, `tags`, `seed` | identidade do caso |
| `verdict` | `pass`, `fail` ou `inconclusive` |
| `prompt.system` | o system prompt real usado pelo runner |
| `prompt.user` | o pedido da fixture |
| `prompt.offered_tools` | as tools que o modelo tinha à disposição |
| `prompt.workspace` | os arquivos semeados antes do turno |
| `observed.tool_calls` | toda chamada emitida, com argumentos |
| `observed.tool_results` | status, erro, produtor, taints e um trecho do payload |
| `observed.final_response` | a resposta entregue |
| `observed.terminal_outcome` | por que o turno parou |
| `harness_refusals` | só as chamadas com `status: blocked`, com código e mensagem |
| `expected.typed_assertions` | o comportamento correto, na forma que o oráculo executa |
| `failed_assertions` | o que reprovou, com o detalhe do operador |
| `metrics` | inclui `rejected_model_attempts`, `extra_tool_calls`, `steps_to_terminal` |

## Casos que passam também entram

Numa mesma fixture, um trace que passa e um que falha diferem só na escolha do
modelo — é exatamente o par que um dataset de preferência precisa. Guardar só as
falhas jogaria fora a metade positiva.

## Limites honestos

- Amostra pequena: 9 fixtures de modelo × 3 sementes. Cresce quando o corpus
  cresce; não é volume de fine-tuning ainda.
- `payload` de resultado grande é truncado em 2000 caracteres, com o tamanho
  original registrado.
- `expected.typed_assertions` descreve o comportamento correto de forma
  verificável, mas **não** é uma resposta-alvo escrita. Transformar isso em alvo
  de treino é uma decisão separada, e inventar a resposta ideal aqui seria
  fabricar dado.
