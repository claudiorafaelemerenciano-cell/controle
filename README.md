# Controle — secretária virtual por telefone (SIP + IA, pt-BR)

Uma "secretária" com inteligência artificial que **atende as ligações do seu número**, conversa
naturalmente em **português do Brasil**, responde dúvidas frequentes, **anota recados** e, quando
você pedir, **faz ligações** em seu nome. Tudo fica registrado (transcrição, resumo e recados) e
aparece num painel web.

- Telefonia: **Asterisk 20** registrado no seu provedor SIP (qualquer operadora VoIP com conta SIP).
- Ouvido: **Deepgram** (transcrição em streaming, pt-BR, com detecção de fim de fala).
- Cérebro: **Claude** (Anthropic), com ferramentas `anotar_recado`, `encerrar_ligacao` e `transferir`.
- Voz: **Azure** (vozes neurais nativas pt-BR — recomendado), **ElevenLabs** ou **OpenAI**.

## Arquitetura

```
                     Internet / PSTN
                           |
                 +---------v----------+
                 |  Provedor SIP/DID  |
                 +---------+----------+
                SIP 5060/udp | RTP 10000-10100/udp
+----------------------------v--------------------------------------------+
| host Linux (docker compose, network_mode: host)                          |
|                                                                          |
|  +----------------------+   AudioSocket TCP 127.0.0.1:9092             |
|  |  asterisk            |  (slin 8 kHz, frames de 20 ms) +-------------+ |
|  |  - pjsip (registro)  |<------------------------------>|   agent     | |
|  |  - dialplan          |   CURL /internal/* (UUID,      |  (Python)   | |
|  |  - AMI 127.0.0.1:5038|<---- caller id, hangup) -------|             | |
|  +----------------------+<---- AMI: Originate/Redirect --|  FastAPI    | |
|                                                          |  :8080      | |
|                                                          +--+---+---+--+ |
+-------------------------------------------------------------|---|---|----+
                                       WebSocket (STT) -------+   |   +---- HTTPS (TTS)
                                       Deepgram pt-BR             |         Azure/ElevenLabs/OpenAI
                                                       HTTPS (LLM)|
                                                      Anthropic Claude
         Painel (dashboard/) e API REST em http://SEU_HOST:8080/
```

Fluxo de uma ligação recebida:

1. O provedor entrega a chamada ao Asterisk (contexto `from-provider`).
2. O dialplan chama o agente via `CURL` (`/internal/inbound?from=...&to=...&channel=...`); o agente
   cria o registro da chamada e devolve um **UUID**. É assim que o agente sabe **quem está ligando**.
3. `Answer()` + `AudioSocket(UUID, 127.0.0.1:9092)`: o áudio passa a fluir em PCM 16-bit 8 kHz.
4. O agente fala a saudação, envia o áudio do interlocutor à Deepgram, manda cada fala final ao
   Claude (streaming), divide a resposta em frases e sintetiza cada uma assim que fica pronta (baixa
   latência). Se a pessoa começa a falar, a fala da secretária é interrompida (*barge-in*).
5. Ao fim, o agente gera um **resumo** com o Claude e salva tudo em SQLite.

Ligação de saída: `POST /api/calls` → AMI `Originate` (com `CALL_UUID`) → quando atendida, entra
no contexto `agent-outbound` → `AudioSocket` → o Claude conduz a conversa com o objetivo informado.

Transferência: o Claude usa `transferir` → AMI `Redirect` do canal para `agent-transfer` →
`Dial(PJSIP/OWNER_PHONE@provider)`.

## Estrutura

```
docker-compose.yml       serviços asterisk + agent
.env.example             todas as variáveis, documentadas
asterisk/                Dockerfile (compila Asterisk 20), entrypoint (envsubst) e templates
config/knowledge.md      perguntas frequentes / informações do negócio (edite!)
agent/                   código Python (asyncio)
  app/audiosocket.py     protocolo AudioSocket (servidor TCP)
  app/conversation.py    loop da conversa (STT -> LLM -> TTS, barge-in, ferramentas)
  app/providers/         Deepgram, Claude, Azure/ElevenLabs/OpenAI e provedores falsos
  app/api.py             API REST + painel estático
  app/asterisk.py        cliente AMI
  prompts/system_pt.md   prompt do sistema (pt-BR)
  tests/                 testes (rodam offline, sem chaves)
dashboard/               painel web (servido em /)
```

## Passo a passo

### 1. Pré-requisitos

- Um servidor **Linux** com Docker e Docker Compose (VPS com IP público é o ideal; o
  `network_mode: host` não funciona no Docker Desktop do macOS/Windows).
- Uma **conta SIP** com um número (DID) num provedor VoIP brasileiro ou internacional. Anote:
  servidor/domínio SIP, porta, usuário, senha e o número.
- Chaves de API:
  - **Anthropic** — <https://console.anthropic.com> → *API Keys*.
  - **Deepgram** — <https://console.deepgram.com> → *API Keys*.
  - **Azure Speech** (recomendado para voz) — portal Azure → criar recurso *Speech* (região
    `brazilsouth`) → *Keys and Endpoint*. Ou ElevenLabs / OpenAI (veja abaixo).

### 2. Configurar

```bash
git clone <este repositório> controle && cd controle
cp .env.example .env
nano .env        # preencha SIP_*, chaves, OWNER_NAME, AMI_SECRET, INTERNAL_TOKEN, API_TOKEN...
nano config/knowledge.md   # horários, endereço, serviços, preços que podem ser informados
```

Gere segredos fortes:

```bash
openssl rand -hex 16   # use para AMI_SECRET, INTERNAL_TOKEN e API_TOKEN
```

### 3. Subir

```bash
docker compose up -d --build     # a 1ª compilação do Asterisk leva alguns minutos
docker compose logs -f
```

Confira o registro SIP:

```bash
docker compose exec asterisk asterisk -rx "pjsip show registrations"   # deve mostrar "Registered"
curl -s -H "Authorization: Bearer $API_TOKEN" http://localhost:8080/api/status
# {"sip_registered": true, "active_calls": 0, "owner_name": "..."}
```

Abra o painel em `http://SEU_HOST:8080/`.

### 4. Apontar o número (DID) para a secretária

O Asterisk **registra-se** no provedor com `SIP_USERNAME`/`SIP_PASSWORD`; com isso, o provedor
passa a entregar as chamadas do DID para este servidor. No painel do provedor:

- Garanta que o DID está associado à conta SIP usada aqui (às vezes chamado de "ramal", "linha"
  ou "rota de entrada para conta SIP").
- Se o provedor oferecer entrega por **IP** (sem registro), cadastre o IP público do servidor; as
  chamadas vindas do IP de `SIP_SERVER` são aceitas pelo `identify` do PJSIP.
- Para manter seu número atual (celular/fixo) e usar a secretária só quando não atender, configure
  no seu celular/operadora o **desvio de chamadas** (quando ocupado / não atende) para o DID.

Ligue para o DID: a secretária deve atender com a saudação.

### 5. Fazer uma ligação de saída

```bash
curl -X POST http://localhost:8080/api/calls \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"to": "5511988887777", "purpose": "Confirmar a consulta da Maria amanhã às 15h e perguntar se ela precisa remarcar."}'
# {"id": "6f1c...", "status": "ringing"}

curl -s -H "Authorization: Bearer $API_TOKEN" http://localhost:8080/api/calls/6f1c...   # transcrição e resumo
```

O formato do número em `to` é o que o seu provedor aceita discar (ex.: `5511988887777`,
`011988887777` ou `+5511988887777`).

## API

| Método | Rota | Resposta |
|---|---|---|
| GET | `/api/status` | `{"sip_registered": bool, "active_calls": int, "owner_name": str}` |
| GET | `/api/calls?limit=50` | lista de `{id, direction, from_number, to_number, started_at, ended_at, status, summary}` |
| GET | `/api/calls/{id}` | o mesmo objeto + `transcript: [{role: "caller"\|"agent", text, ts}]` |
| POST | `/api/calls` | corpo `{"to", "purpose"}` → `{"id", "status": "ringing"}` |
| GET | `/api/messages` | lista de `{id, call_id, name, phone, message, created_at}` |

Datas em ISO 8601 UTC (`2026-10-14T15:04:05Z`). `status`: `ringing` → `active` → `completed`
(ou `failed` se não atendeu/falhou). Com `API_TOKEN` definido, todas as rotas `/api/*` exigem
`Authorization: Bearer <token>`. As rotas `/internal/*` são para o dialplan (protegidas por
`INTERNAL_TOKEN`).

## Vozes em português do Brasil

A secretária fala **sempre em pt-BR**: o prompt obriga respostas em português, frases curtas no
estilo falado, sem markdown/emojis, com números e datas escritos como se fala. Opções de voz
(`TTS_PROVIDER`):

| Opção | Sotaque | Configuração |
|---|---|---|
| **`azure`** (recomendado) | Nativo brasileiro | `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION=brazilsouth`, `AZURE_TTS_VOICE`: `pt-BR-FranciscaNeural` (padrão, feminina), `pt-BR-ThalitaNeural` (feminina), `pt-BR-AntonioNeural` (masculina). SSML com `xml:lang="pt-BR"` e saída `raw-8khz-16bit-mono-pcm` — já no formato do telefone, sem reamostragem. Baixa latência e custo baixo. |
| `elevenlabs` | Nativo **se** você escolher uma voz brasileira | `ELEVENLABS_VOICE_ID`: na [Voice Library](https://elevenlabs.io/app/voice-library) filtre *Portuguese* + sotaque *Brazilian*, adicione à conta e copie o ID. `ELEVENLABS_MODEL=eleven_multilingual_v2` (mais natural) ou `eleven_turbo_v2_5`/`eleven_flash_v2_5` (mais rápidos; nestes o idioma é forçado com `language_code=pt`). |
| `openai` | Não nativo (sotaque estrangeiro leve) | `gpt-4o-mini-tts` com `OPENAI_TTS_INSTRUCTIONS` pedindo sotaque brasileiro. Use só se já tiver conta OpenAI. |

A transcrição (Deepgram) usa `language=pt-BR` com o modelo `nova-3` (ou `nova-2`).

## NAT e firewall

- **Portas a liberar (entrada, UDP)**: `5060` (SIP) e `RTP_START`–`RTP_END` (padrão 10000–10100,
  ~4 portas por chamada simultânea). De preferência, libere **apenas para os IPs do provedor**.
- **8080/tcp** (painel/API): exponha só se precisar acessar de fora — de preferência atrás de um
  proxy HTTPS (Caddy/Nginx) ou VPN, e sempre com `API_TOKEN`.
- **Nunca** exponha `9092` (AudioSocket, sem autenticação) nem `5038` (AMI); ambos ficam em
  `127.0.0.1` por padrão.
- **Atrás de NAT** (roteador doméstico, AWS/GCP com IP elástico): defina `EXTERNAL_IP` com o IP
  público e `LOCAL_NET` com as redes internas. O Asterisk passará a anunciar o IP público no SIP/SDP
  (`external_media_address`/`external_signaling_address`). No roteador, encaminhe 5060/udp e a faixa
  RTP para o servidor. Sintoma clássico de NAT errado: a chamada conecta mas **sem áudio** (ou áudio
  em um sentido só).
- Desative o "SIP ALG" do roteador, se houver — ele costuma corromper o SIP.

## Segurança

- **Nunca deixe a 5060 aberta para a internet inteira** sem proteção: robôs varrem a porta o tempo
  todo tentando fraudar ligações. Use firewall liberando só os IPs do provedor e/ou **fail2ban**
  com o filtro `asterisk` lendo `/var/log/asterisk/messages.log` (volume `asterisk-logs`).
- Esta configuração não tem ramais nem endpoints anônimos: só o endpoint do provedor (identificado
  pelo IP) entra no dialplan. Ligações de saída só são feitas pelo agente via AMI local.
- Defina `API_TOKEN` (qualquer um que acesse a API pode fazer ligações em seu nome), `INTERNAL_TOKEN`
  e `AMI_SECRET` fortes. O `.env` está no `.gitignore` — não o versione.
- As transcrições contêm dados pessoais (LGPD): proteja o volume `agent-data` e o acesso ao painel, e
  informe quem liga que o atendimento é feito por assistente virtual (a saudação padrão já faz isso).

## Custos e latência

- Latência típica de resposta: ~0,3 s fim de fala (endpointing) + ~0,5–1 s primeiro trecho do LLM
  + ~0,1–0,3 s primeiro áudio do TTS. Dicas:
  - `ANTHROPIC_MODEL=claude-sonnet-5-5` com `ANTHROPIC_EFFORT=low` (padrão): o agente desliga o
    raciocínio estendido nesse modelo para responder mais rápido. O prompt é cacheado (prompt caching).
  - `DEEPGRAM_ENDPOINTING_MS` menor (200) responde mais rápido, mas pode cortar quem fala pausado.
  - Azure (`brazilsouth`) ou ElevenLabs `eleven_flash_v2_5` têm o primeiro áudio mais rápido.
  - Hospede o servidor perto do provedor SIP e das APIs (ex.: São Paulo ou costa leste dos EUA).
- Custos variáveis por minuto de ligação: STT (cobrado por minuto de áudio), TTS (por caractere
  falado), LLM (por token — o histórico da ligação é reenviado a cada turno; o cache reduz o custo) e
  a tarifa do provedor SIP. Mantenha `config/knowledge.md` objetivo e use `MAX_CALL_SECONDS` e
  `SILENCE_TIMEOUT_S` para evitar ligações esquecidas abertas.

## Desenvolvimento e testes

```bash
cd agent
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

Os testes rodam **offline, sem chaves**, com provedores falsos (protocolo AudioSocket, reamostragem,
contrato da API, loop de conversa com barge-in/ferramentas, requisições pt-BR de cada TTS, cliente AMI).

Para rodar o agente localmente sem chaves: `STT_PROVIDER=fake LLM_PROVIDER=fake TTS_PROVIDER=fake
DB_PATH=./dev.db python -m app.main` (a API sobe em :8080; sem Asterisk não há ligações reais).

### Personalização

- Prompt: `agent/prompts/system_pt.md` (marcadores `{{OWNER_NAME}}`, `{{BUSINESS_DESCRIPTION}}`,
  `{{KNOWLEDGE}}`, `{{CALL_CONTEXT}}`, `{{ASSISTANT_NAME}}`).
- Provedores: implemente as interfaces de `agent/app/providers/base.py` e registre em `factory.py`.

## Limitações conhecidas

- AudioSocket do Asterisk 20 trafega apenas 8 kHz (qualidade de telefone); tudo é convertido para
  esse formato.
- Um único tronco/conta SIP (`provider`). Para TLS/SRTP ou TCP, ajuste `asterisk/templates/pjsip.conf.tmpl`.
- Detecção de caixa postal em ligações de saída fica a cargo do LLM (pela transcrição).
- O barge-in depende da transcrição parcial; ruído alto no ambiente pode interromper a secretária
  (ajuste `BARGE_IN_MIN_CHARS`).
