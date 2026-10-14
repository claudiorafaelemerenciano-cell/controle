#!/bin/bash
# Renderiza os templates de /etc/asterisk/templates/*.tmpl com as variáveis de
# ambiente (envsubst com lista explícita, para não tocar nas variáveis do
# dialplan como ${EXTEN}) e inicia o Asterisk em primeiro plano.
set -euo pipefail

require() {
    for v in "$@"; do
        if [ -z "${!v:-}" ]; then
            echo "ERRO: variável obrigatória ${v} não definida (veja .env.example)" >&2
            exit 1
        fi
    done
}
require SIP_SERVER SIP_USERNAME SIP_PASSWORD AMI_USERNAME AMI_SECRET

export SIP_PORT="${SIP_PORT:-5060}"
export SIP_LOCAL_PORT="${SIP_LOCAL_PORT:-5060}"
export SIP_AUTH_USERNAME="${SIP_AUTH_USERNAME:-$SIP_USERNAME}"
export SIP_DID="${SIP_DID:-$SIP_USERNAME}"
export SIP_CALLER_ID="${SIP_CALLER_ID:-$SIP_DID}"
export RTP_START="${RTP_START:-10000}"
export RTP_END="${RTP_END:-10100}"
export AGENT_HOST="${AGENT_HOST:-127.0.0.1}"
export AGENT_HTTP_PORT="${HTTP_PORT:-8080}"
export AGENT_AUDIO_PORT="${AUDIOSOCKET_PORT:-9092}"
export INTERNAL_TOKEN="${INTERNAL_TOKEN:-}"
export OWNER_PHONE="${OWNER_PHONE:-}"

if [[ ! "${INTERNAL_TOKEN}" =~ ^[A-Za-z0-9_-]*$ ]]; then
    echo "ERRO: INTERNAL_TOKEN deve conter apenas letras, números, _ ou -" >&2
    exit 1
fi

# NAT: endereço público anunciado no SDP/Contact e redes locais (separadas por vírgula).
NAT_CONFIG=""
if [ -n "${EXTERNAL_IP:-}" ]; then
    NAT_CONFIG+="external_media_address=${EXTERNAL_IP}"$'\n'
    NAT_CONFIG+="external_signaling_address=${EXTERNAL_IP}"$'\n'
fi
IFS=',' read -ra NETS <<< "${LOCAL_NET:-}"
for net in "${NETS[@]}"; do
    net="$(echo "$net" | xargs)"
    [ -n "$net" ] && NAT_CONFIG+="local_net=${net}"$'\n'
done
export NAT_CONFIG

VARS='${SIP_SERVER} ${SIP_PORT} ${SIP_LOCAL_PORT} ${SIP_USERNAME} ${SIP_AUTH_USERNAME} ${SIP_PASSWORD}
${SIP_DID} ${SIP_CALLER_ID} ${NAT_CONFIG} ${RTP_START} ${RTP_END} ${AMI_USERNAME} ${AMI_SECRET}
${AGENT_HOST} ${AGENT_HTTP_PORT} ${AGENT_AUDIO_PORT} ${INTERNAL_TOKEN} ${OWNER_PHONE}'

for tmpl in /etc/asterisk/templates/*.tmpl; do
    out="/etc/asterisk/$(basename "$tmpl" .tmpl)"
    envsubst "$VARS" < "$tmpl" > "$out"
    chmod 640 "$out"
    chown asterisk:asterisk "$out"
done

echo "Asterisk: registrando ${SIP_USERNAME}@${SIP_SERVER}:${SIP_PORT}, RTP ${RTP_START}-${RTP_END}, agente em ${AGENT_HOST}"
exec /usr/sbin/asterisk -f -U asterisk -G asterisk -vvv
