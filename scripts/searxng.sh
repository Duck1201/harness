#!/usr/bin/env bash
# Sobe e configura a instância SearXNG opcional do web_search. Idempotente — pode
# rodar de novo para reaplicar a configuração ou religar o container.
#
# Sem ela o web_search cai no DuckDuckGo, que não pede chave; ela existe para
# quem quer JSON estruturado e ranking próprio. Duas edições no settings.yml são
# obrigatórias, e é o que o script garante: `formats` com json (o padrão do
# SearXNG é só html) e o limiter desligado (o harness fala HTTP direto, não é
# navegador). Sem qualquer uma delas o provedor responde 403 e toda busca cai
# silenciosamente no fallback.
#
# Uso:
#   scripts/searxng.sh                # sobe/reconfigura em 127.0.0.1:8080
#   PORT=8888 scripts/searxng.sh      # outra porta
#   scripts/searxng.sh --down         # para e remove o container
set -euo pipefail

NAME=${NAME:-searxng}
PORT=${PORT:-8080}
CONFIG_DIR=${CONFIG_DIR:-$HOME/searxng}
IMAGE=${IMAGE:-docker.io/searxng/searxng:latest}
URL="http://127.0.0.1:${PORT}/search"

if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  RUNTIME=docker
elif command -v podman >/dev/null; then
  RUNTIME=podman
else
  echo "Nem docker (com daemon no ar) nem podman encontrados." >&2
  exit 1
fi

if [[ ${1:-} == "--down" ]]; then
  echo "==> removendo o container ${NAME}"
  $RUNTIME rm -f "$NAME" >/dev/null 2>&1 || true
  echo "Pronto. ${CONFIG_DIR} continua no disco; apague à mão se quiser."
  echo "Lembre de limpar a instância em Configurações → Host → Instância SearXNG."
  exit 0
fi

echo "==> runtime: ${RUNTIME}"
mkdir -p "$CONFIG_DIR"

if $RUNTIME ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
  echo "==> container ${NAME} já existe, reaproveitando"
  $RUNTIME start "$NAME" >/dev/null 2>&1 || true
else
  echo "==> subindo ${NAME} em 127.0.0.1:${PORT}"
  # Publicado só em loopback de propósito: o EgressGuard libera exatamente este
  # host:porta para o web_search, e nada mais.
  $RUNTIME run -d --name "$NAME" --restart unless-stopped \
    -p "127.0.0.1:${PORT}:8080" \
    -v "${CONFIG_DIR}:/etc/searxng" \
    "$IMAGE" >/dev/null
fi

echo "==> aguardando o primeiro boot escrever settings.yml"
for _ in $(seq 1 30); do
  [[ -f "${CONFIG_DIR}/settings.yml" ]] && break
  sleep 1
done
if [[ ! -f "${CONFIG_DIR}/settings.yml" ]]; then
  echo "settings.yml não apareceu em ${CONFIG_DIR}; veja '${RUNTIME} logs ${NAME}'." >&2
  exit 1
fi

# O secret_key é gerado no primeiro boot e não deve mudar a cada execução.
secret=$(sed -n 's/^ *secret_key: *"\(.*\)".*/\1/p' "${CONFIG_DIR}/settings.yml" | head -1)
if [[ -z "$secret" ]]; then
  secret=$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 32)
fi

echo "==> aplicando formats:json e limiter:false"
# O arquivo pertence ao usuário do container, então quem escreve é o container.
$RUNTIME exec -u 0 -i "$NAME" sh -c 'cat > /etc/searxng/settings.yml' <<EOF
# Gerado por scripts/searxng.sh. Edite à vontade, mas mantenha as duas linhas
# marcadas: sem elas o harness não consegue ler esta instância.
use_default_settings: true

server:
  secret_key: "${secret}"
  image_proxy: true
  # obrigatório: o harness fala HTTP direto e o limiter recusaria toda busca
  limiter: false

search:
  formats:
    - html
    # obrigatório: o padrão do SearXNG entrega só html
    - json
EOF

echo "==> reiniciando"
$RUNTIME restart "$NAME" >/dev/null

echo "==> verificando ${URL}"
for _ in $(seq 1 30); do
  body=$(curl -s --max-time 5 "${URL}?q=harness&format=json" || true)
  case "$body" in
    *'"results"'*)
      echo
      echo "SearXNG respondendo JSON."
      echo "Cole em Configurações → Host → Instância SearXNG e reinicie o harness:"
      echo
      echo "    ${URL}"
      echo
      exit 0
      ;;
  esac
  sleep 2
done

echo "A instância subiu mas não devolveu JSON em ${URL}." >&2
echo "Veja '${RUNTIME} logs ${NAME}' e confira ${CONFIG_DIR}/settings.yml." >&2
exit 1
