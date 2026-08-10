# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

## [1.0.0] — 2026-08-10

Primeira release. O que ela exclui deliberadamente está em
[`docs/RELEASE-PENDING.md`](docs/RELEASE-PENDING.md).

### Adicionado

- **Confirmação de escrita com UntrustedWebTaint.** Um Turn que lê a web e pede
  para escrever agora para numa `ConfirmationGate` e espera a decisão do
  Operator, em vez de terminar em `blocked`. A decisão vale para aquela chamada:
  não cria grant, não persiste e não amplia autoridade. Registrada em
  CanonicalHistory como automação `write_confirmation`.
- **Autenticação do Operator.** A exposição de rede é derivada da credencial:
  sem senha, só loopback direto; com senha, toda rota exige sessão. Senha em
  PBKDF2-HMAC-SHA256 com 600.000 iterações, sessões opacas só em memória.
  Modelo de ameaça em [`docs/THREAT-MODEL-AUTH.md`](docs/THREAT-MODEL-AUTH.md).
- **Navegador Brave real**, dirigido por CDP sobre o `aiohttp` que já era
  dependência. Um processo e um `--user-data-dir` descartável por operação, e o
  destino é fixado com `--host-resolver-rules` a partir do endereço que o
  `EgressGuard` aprovou — o navegador não resolve DNS por conta própria.
- **Tiers de eval que faltavam.** `ModelCaseRunner` roda o AgentEngine real
  contra o RuntimeProfile e `BrowserBenchCaseRunner` replica a escalação contra
  uma bancada loopback determinística. Antes, `EvalService` bloqueava qualquer
  experimento com `runner_not_configured`.
- **Telas de setup e de login** no frontend; a primeira execução e o primeiro
  acesso deixaram de exigir `curl`.
- Ponto de entrada `harness`, README, CI, `scripts/run-experiment.py` e
  `scripts/validate-contracts.mjs --write`.

### Evidência

- `guarded_web_brave_escalation`, o único experimento
  `required_before_release`, rodou piloto de 15 casos por braço e promoção de 50
  por braço com três seeds, contra o perfil `mitos` e o Brave instalado. Ambos
  os braços passaram todos os casos, com zero violações de segurança, e o gate
  de promoção retornou `promoted`. Resultado e os sete digests congelados em
  `evals/experiments.json#results`.

### Corrigido

- `web_fetch` escalava para o navegador apenas em HTTP 401/403. Uma extração
  abaixo do limiar calibrado de 120 caracteres — o sintoma que
  `harness.json#network.web_fetch` declara, e exatamente como uma página
  renderizada por JavaScript se apresenta a um cliente HTTP — devolvia
  `empty_extraction` e desistia.
- O contador de violações de segurança marcava toda recusa do harness como
  violação, o que tornava o gate `zero_violations` impossível de passar por
  construção. Violação agora significa expectativa de segurança não cumprida.
- `config/harness.json` declarava `LangGraph SqliteSaver` e `assistant-ui`;
  nenhum dos dois existe no projeto.
- `CredentialStore` sobrescrevia uma credencial ao gravar a outra.
- O encerramento do navegador matava só o líder do grupo, deixando zygotes e o
  crashpad handler órfãos.
- `[tool.pyright]` não apontava para o `.venv`, e um checkout novo reportava
  1229 erros falsos.

### Limitações aceitas

Ollama local como único runtime, verificação de página desligada, um único
Operator sem papéis, sessão sem persistência, exceção de bancada restrita aos
tiers de eval, Python 3.13 em Linux x86_64 e UX web-first. Cada uma com seu gate
de saída em `docs/RELEASE-PENDING.md`.
