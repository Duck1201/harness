# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

## [Não lançado]

### Adicionado

- Ponto de entrada de console `harness`, com bind em `127.0.0.1` por padrão.
- `README.md` com requisitos, instalação, fluxo de setup, variáveis de ambiente e
  provisionamento do tokenizer.
- `scripts/validate-contracts.mjs --write`, que resela os digests derivados
  (dataset, `contract_digests` e manifesto) após edição de contratos ou fixtures.
- Integração contínua com os checks de backend, frontend e contratos.

### Corrigido

- `config/harness.json` declarava `LangGraph SqliteSaver` como implementação do
  store canônico e `assistant-ui` como cliente da interface. Nenhum dos dois existe
  no projeto: o store é `sqlite3` puro e a SPA é React escrita à mão. Os campos
  agora nomeiam o que roda.
- `[tool.pyright]` não apontava para o `.venv`, e um checkout novo reportava 1229
  erros falsos.
