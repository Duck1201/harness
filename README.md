# Harness 2.0

Harness de agente LLM local, web-first, com policy por efeito. O modelo é tratado
como não confiável: seleção de tool, argumentos e resultados passam por validação,
autorização, confirmação e sandbox do harness. Autorização é resolvida pelo efeito
declarado (`workspace_read`, `workspace_write`, `data_egress`), nunca pelo nome da
tool.

Backend FastAPI + SQLite, frontend React servido pelo próprio backend, modelo local
via Ollama. O contrato normativo está em [`docs/DECISOES-2.0.md`](docs/DECISOES-2.0.md);
o que deliberadamente não entra na primeira release está em
[`docs/RELEASE-PENDING.md`](docs/RELEASE-PENDING.md).

## Requisitos

| Componente | Versão | Observação |
|---|---|---|
| Python | 3.13 (`>=3.13,<3.14`) | A plataforma é contrato, não sugestão |
| Sistema | Linux x86_64 | Outras plataformas não são suportadas na v1 |
| Node.js | 22+ | Só para construir o frontend e validar contratos |
| pnpm | 10.13.1 | Via `corepack enable` |
| Ollama | 0.32.5 | Servindo em `http://127.0.0.1:11434` |
| Brave | qualquer | `brave-browser`, `brave-browser-stable` ou `brave` no PATH, para escalação do `web_fetch` |

## Instalação

```bash
uv sync                                   # dependências Python + .venv
corepack enable && cd web && pnpm install && pnpm build && cd ..   # gera web/dist
```

### Modelo local

O perfil de execução é reproduzível a partir do [`Modelfile`](Modelfile) da raiz.
O digest do perfil instalado é verificado contra `config/model-profiles.json` na
inicialização — divergência é erro, não aviso.

```bash
ollama create mitos -f Modelfile
ollama show mitos --modelfile | sha256sum   # deve bater installation.installed_profile_digest_sha256
```

### Tokenizer

O orçamento de contexto (24576 tokens) depende de uma contagem real de tokens, não
de estimativa por caractere. O harness carrega um `tokenizer.json` no formato
HuggingFace e **valida seu SHA-256** antes de aceitar qualquer turno: sem o arquivo,
a aplicação sobe com `EngineReadiness(ready=False, reason_code="tokenizer_file_missing")`
e recusa execuções.

Obtenha o `tokenizer.json` do repositório do modelo base
(`huihui-ai/Huihui-Qwen3.5-4B-abliterated`), calcule seu digest e informe ambos no
setup:

```bash
sha256sum /caminho/para/tokenizer.json
```

O arquivo não é versionado neste repositório (12 MB, e o digest correto depende de
qual revisão do modelo você instalou). O tokenizer autoritativo do modelo está
embutido no perfil Ollama; este arquivo existe apenas para a contagem de tokens do
orçamento de contexto.

## Primeira execução

```bash
harness              # ou: uv run uvicorn harness.api:create_app --factory --host 127.0.0.1 --port 8765
```

Na primeira execução o servidor imprime um **token de setup efêmero** no stderr
(TTL de 600 s). O setup é concluído por `POST /api/setup` com o header
`X-Harness-Setup-Token`, informando `allowed_workspace_roots`, `tokenizer_path`,
`tokenizer_digest`, `state_dir`, `allowed_origins` e, opcionalmente, a chave da
Brave Search. `GET /api/setup/status` diz se ainda é necessário.

Depois de configurado, o setup só reabre com `HARNESS_SETUP_REOPEN=1`.

## Configuração por ambiente

Todas as variáveis são opcionais; os valores efetivos vêm do HostConfig gravado no
setup, e a variável de ambiente tem precedência sobre ele.

| Variável | Default | Efeito |
|---|---|---|
| `HARNESS_HOST` | `127.0.0.1` | Interface do servidor. Ver "Exposição de rede" |
| `HARNESS_PORT` | `8765` | Porta do servidor |
| `HARNESS_CONFIG` | `config/harness.json` | Contrato normativo carregado |
| `HARNESS_STATE_DIR` | `$XDG_STATE_HOME/harness-2` | SQLite canônico, telemetria e tokenizer |
| `HARNESS_WORKSPACE_ROOTS` | do HostConfig | Raízes permitidas, separadas por `:` |
| `HARNESS_TOKENIZER_PATH` | `<state_dir>/tokenizer.json` | Caminho do tokenizer |
| `HARNESS_TOKENIZER_SHA256` | do HostConfig | Digest exigido do tokenizer |
| `HARNESS_OLLAMA_URL` | `http://127.0.0.1:11434` | Endpoint do runtime |
| `HARNESS_BRAVE_API_KEY` | — | Chave da Brave Search. Mutuamente exclusiva com a de arquivo |
| `HARNESS_BRAVE_API_KEY_FILE` | — | Caminho absoluto de arquivo privado (modo `0600`, sem symlink) com a chave |
| `HARNESS_HOST_CONFIG` | padrão do XDG | Caminho do HostConfig |
| `HARNESS_ALLOWED_ORIGINS` | `127.0.0.1` e `localhost` na porta do servidor | Allowlist de Origin, separada por vírgula |
| `HARNESS_SETUP_REOPEN` | `0` | Reabre o setup numa instalação já configurada |

Não existe `.env`: segredos entram pelo setup e ficam no CredentialStore, gravado
com escrita atômica e permissão privada.

### Exposição de rede

A exposição é derivada da credencial, não de uma flag:

| Estado | Comportamento |
|---|---|
| Sem senha de Operator | Só conexões de loopback direto são atendidas. Qualquer outra recebe `401 authentication_required`. |
| Com senha de Operator | Toda rota da API exige o header `X-Harness-Session` obtido em `POST /api/session`. |

Não existe configuração que abra a porta para a rede sem autenticação. Exceções
de rota, e por quê: `GET /api/health` (responde a supervisor antes de haver
login), `/api/setup*` (guardada pelo token efêmero e restrita a loopback) e
`POST /api/session` (é o login).

Definir a primeira senha:

```bash
curl -X PUT http://127.0.0.1:8765/api/admin/operator-password \
  -H 'Content-Type: application/json' \
  -H 'Origin: http://127.0.0.1:8765' \
  -d '{"password": "pelo-menos-doze-caracteres"}'
```

Rotacionar exige a senha atual em `current_password`, além da sessão:

```bash
curl -X PUT http://127.0.0.1:8765/api/admin/operator-password \
  -H 'Content-Type: application/json' \
  -H 'Origin: http://127.0.0.1:8765' \
  -H "X-Harness-Session: $SESSION" \
  -d '{"current_password": "a-senha-de-agora", "password": "a-nova-senha"}'
```

Trocar a senha invalida todas as sessões abertas. O hash é PBKDF2-HMAC-SHA256
com 600.000 iterações; a senha original nunca é gravada nem devolvida.

O modelo de ameaça está em [`docs/THREAT-MODEL-AUTH.md`](docs/THREAT-MODEL-AUTH.md).

### Reverse proxy

Para publicar além do loopback, configure a senha e coloque um proxy com TLS à
frente. O harness recusa qualquer requisição que traga `Forwarded`, `Via`,
`X-Real-IP` ou `X-Forwarded-*` como se fosse local, então o proxy nunca consegue
se passar por conexão direta — mas você precisa incluir a origem pública em
`HARNESS_ALLOWED_ORIGINS`:

```nginx
server {
  listen 443 ssl;
  server_name harness.example;

  location / {
    proxy_pass http://127.0.0.1:8765;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_http_version 1.1;
    proxy_buffering off;              # o stream AG-UI é SSE
    proxy_read_timeout 930s;          # acima de loop.max_turn_duration_seconds
  }
}
```

```bash
HARNESS_ALLOWED_ORIGINS=https://harness.example harness
```

## Uso

Abra `http://127.0.0.1:8765`. O produto é a superfície web — CLI e desktop estão
fora do roadmap da v1 (`docs/RELEASE-PENDING.md`).

Cada Conversation começa sem autoridade nenhuma. Ler arquivos exige um
`WorkspaceRootGrant`, escrever exige também `WriteGrant`, e qualquer saída de dados
exige `WebAccessGrant`. O modelo não concede nem amplia acesso: quem concede é o
Operator, pela interface.

As 8 tools expostas: `read_file`, `write_file`, `edit`, `list_directory`, `glob`,
`grep_search`, `web_search`, `web_fetch`.

## Desenvolvimento

```bash
node scripts/validate-contracts.mjs        # coerência entre docs e contratos JSON
node scripts/validate-contracts.mjs --write  # resela os digests derivados após editar contratos
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run pytest
cd web && pnpm test && pnpm exec tsc -b
```

Os JSON de `config/` e `evals/` carregam digests derivados do próprio conteúdo.
Depois de editar qualquer contrato ou fixture, rode o validador com `--write` para
reselar e commite a mudança de digest junto.

## Avaliações

`evals/fixtures/regressions.json` guarda o corpus de regressões determinísticas e
`evals/experiments.json` a matriz de experimentos. O protocolo está em
[`evals/README.md`](evals/README.md): piloto de 15 execuções por braço (direcional),
promoção com 50 execuções por braço e 3 seeds registradas. Violação de segurança
reprova imediatamente, independente do resto.

Dos experimentos declarados, apenas `guarded_web_brave_escalation` é
`required_before_release`. Os demais travam **mudanças de baseline** (temperatura,
thinking, limite de passos, idioma do prompt, verificação de página) e não são
dívida de release: enquanto não rodarem, o baseline vigente permanece o que está
no contrato.

Medição inventada para preencher contrato é proibida (`docs/DECISOES-2.0.md`). Um
experimento sem execução fica com `result: null`.

## Licença

[Apache-2.0](LICENSE).

## Documentação

Ordem de leitura em [`docs/README.md`](docs/README.md). Em resumo: o glossário
canônico está em [`CONTEXT.md`](CONTEXT.md), o baseline normativo em
`docs/DECISOES-2.0.md`, as exclusões da release em `docs/RELEASE-PENDING.md` e as
fronteiras arquiteturais em `docs/adr/`.
