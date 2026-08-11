#!/usr/bin/env bash
# Prepara um host novo para rodar o harness: deps Python/web, perfil Ollama e
# tokenizer.json. Idempotente — pode rodar de novo sem duplicar trabalho.
# Depois dele, só falta abrir http://127.0.0.1:8765/setup com `uv run harness`.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "==> uv sync"
uv sync

if command -v corepack >/dev/null; then
  echo "==> build do frontend (web/dist)"
  corepack enable
  (cd web && pnpm install && pnpm build)
else
  echo "==> corepack não encontrado, pulando build do frontend" >&2
fi

if command -v ollama >/dev/null; then
  echo "==> perfil Ollama (mitos)"
  if ! ollama show mitos >/dev/null 2>&1; then
    ollama create mitos -f Modelfile
  fi
  expected_digest=$(python3 -c '
import json
profiles = json.load(open("config/model-profiles.json"))
active = profiles["active_runtime_profile"]
for profile in profiles["runtime_profiles"]:
    if profile["id"] == active:
        print(profile["installation"]["installed_profile_digest_sha256"])
        break
')
  installed_digest=$(ollama show mitos --modelfile | sha256sum | cut -d' ' -f1)
  if [ "$installed_digest" != "$expected_digest" ]; then
    echo "aviso: digest do perfil Ollama instalado não bate com config/model-profiles.json" >&2
    echo "  esperado: $expected_digest" >&2
    echo "  instalado: $installed_digest" >&2
  fi
else
  echo "==> ollama não encontrado, pulando criação do perfil" >&2
fi

state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/harness-2"
tokenizer_path="$state_dir/tokenizer.json"
if [ ! -f "$tokenizer_path" ]; then
  echo "==> baixando tokenizer.json para $tokenizer_path"
  mkdir -p "$state_dir"
  curl -fsSL \
    "https://huggingface.co/huihui-ai/Huihui-Qwen3.5-4B-abliterated/resolve/main/tokenizer.json" \
    -o "$tokenizer_path"
else
  echo "==> tokenizer.json já existe em $tokenizer_path"
fi

echo "==> pronto. Suba o servidor com: uv run harness"
