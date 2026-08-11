# CLAUDE.md

Orientações para agentes que trabalham neste repositório. O README cobre
instalação e operação; aqui fica o que não é óbvio ao editar o código.

## Comandos

```bash
uv sync                                        # dependências + .venv
uv run pytest                                  # suíte Python (tests/)
uv run pytest tests/test_agent_engine.py -k nome   # teste único
uv run ruff check . && uv run ruff format --check .
uv run pyright                                 # strict, cobre src/ e tests/
node scripts/validate-contracts.mjs            # coerência docs <-> contratos JSON
node scripts/validate-contracts.mjs --write    # resela digests derivados
cd web && pnpm test && pnpm exec tsc -b && pnpm build
harness                                        # sobe o servidor (uvicorn)
```

CI (`.github/workflows/ci.yml`) roda três jobs: backend (ruff + pyright +
pytest), frontend (tsc + vitest + build) e contracts (validador, sem
dependências).

## Contratos são código

`config/*.json` e `evals/*.json` não são configuração ilustrativa: o código lê
esses arquivos e o validador exige coerência entre eles e os documentos.

- `config/tool-registry.json` é a **única** fonte de nomes de tool, schemas,
  efeitos, grants e envelope de resultado. Não existe tool declarada em Python.
- `config/harness.json` define ExecutionRoutes, limites de loop, policy, stores,
  rede e UX.
- `config/model-profiles.json` define RuntimeProfiles e a evidência de cada
  capacidade.
- `evals/fixtures/regressions.json` e `evals/experiments.json` carregam digests
  derivados do próprio conteúdo (`dataset_digest_sha256`, `manifest_digest_sha256`,
  `*_sha256` dos contratos).

**Depois de editar qualquer contrato ou fixture, rode
`node scripts/validate-contracts.mjs --write` e commite o digest junto.** Sem
isso o job `contracts` do CI falha.

O validador também checa links e âncoras de todo Markdown em `docs/` e `evals/`,
então renomear um heading quebra o CI.

## Invariantes que não se negociam

Vêm de `docs/DECISOES-2.0.md` — se uma mudança colide com um destes itens, ela
exige alterar o documento, os contratos e as fixtures no mesmo commit.

- **Autorização é por efeito, nunca por nome de tool**: `workspace_read` exige
  WorkspaceRootGrant; `workspace_write` exige também WriteGrant; `data_egress`
  exige WebAccessGrant. Adicionar um `if tool_name == ...` em caminho de policy
  está errado por construção. O gate vive em dois executores independentes
  (`local_tools.py` e `web_tools.py`): grant novo exige tocar os dois.
- **O modelo é não confiável**: seleção, argumentos e resultados passam por
  validação, autorização, confirmação e sandbox do harness. `blocked` só pode ser
  emitido pelo harness; recusa de provedor é `failed`; sucesso sem itens é `empty`.
- **CanonicalHistory é a fonte autoritativa**; ModelView e AG-UI são projeções
  reconstruídas. Reasoning transita ao vivo para a UI mas **nunca** é persistido
  em estado canônico, telemetria ou replay.
- **Dois stores separados**: estado conversacional e telemetria. A telemetria
  recebe só IDs, digests, classes, tamanhos, contagens e tempos — nunca conteúdo.
  Falha de telemetria é não fatal.
- **Todo ToolResult** tem `status`, `retryable`, `data`, `error`, `meta`.
- **Sem senha de Operator, só loopback direto é atendido**; com senha, toda rota
  exige sessão. Não há terceira opção nem flag que abra a porta sem autenticação.
- **Medição inventada é proibida**: experimento sem execução fica com
  `result: null`.

## Arquitetura

Composition root em `api._default_service`, chamado por `create_app` quando nada
é injetado — `__init__.py` só reexporta o pacote. Entrypoint em `__main__.py`
(uvicorn sobre `api.create_app`).

| Camada | Módulos |
|---|---|
| HTTP/SSE | `api.py`, `ag_ui.py`, `auth.py`, `setup.py` |
| Orquestração | `application_service.py`, `agent_engine.py`, `workspace_coordinator.py` |
| Portas | `ports.py` (Protocols), `domain.py` (tipos) |
| Tools | `local_tools.py`, `web_tools.py`, `composite_tools.py`, `brave_browser.py`, `page_verification.py` |
| Estado | `conversation_store.py` (SQLite canônico), `observability_store.py` |
| Contexto/modelo | `context_builder.py`, `system_prompt.py`, `token_estimator.py`, `ollama_runtime.py` |
| Config | `config.py` (contratos JSON), `host_config.py` (HostConfig do host) |
| Evals | `evals/` (runner, oracles, statistics, store, bench) |

Fluxo de um Turn: `PendingRequest` -> `AgentEngine` itera AgentSteps ->
`ModelView` reconstruída por `context_builder` a cada passo -> `ollama_runtime`
gera -> tool calls passam por preflight/policy -> `ToolResult` volta ao
CanonicalHistory -> exatamente um `TerminalOutcome`.

O `system_prompt` é **derivado dos contratos** (ADR 0007), não escrito à mão.

## Convenções

- Python 3.13, `ruff` com `line-length = 100`, regras `E,F,I,UP,B,SIM,RUF`.
- `pyright` em `typeCheckingMode = "strict"` — sem `Any` solto, sem ignore novo.
- Dataclasses `frozen=True, slots=True` para tipos de domínio; `Protocol` em
  `ports.py` para tudo que é substituível.
- Testes espelham o módulo (`tests/test_<modulo>.py`), sem framework extra além
  do pytest.
- Frontend em `web/`: React 19 + Vite + vitest, cliente em `web/src/client/` com
  implementação `Fetch` e `Mock` atrás da mesma interface.

## Idioma

Documentação, comentários e mensagens de erro voltadas ao Operator em pt-BR.
Identificadores, nomes de tool, campos de contrato e chaves JSON em inglês.
`config/tool-registry.json#model_facing_language` fixa o idioma exposto ao modelo.

## Documentação relevante

`CONTEXT.md` (glossário canônico), `docs/DECISOES-2.0.md` (contrato normativo),
`docs/RELEASE-PENDING.md` (o que ficou de fora e sob quais gates),
`docs/adr/` (fronteiras arquiteturais), `docs/THREAT-MODEL-AUTH.md`,
`evals/README.md` (protocolo de avaliação).
