# WPP-DENTAL

Assistente de agendamento odontologico via WhatsApp, integrado ao Google Calendar e organizado em camadas de arquitetura limpa.

## Objetivo

Atender o fluxo descrito em [`PRD.md`](./PRD.md) com um backend previsivel, testavel e pronto para operacao em VPS.

## Stack

- Python 3.11+
- FastAPI
- SQLite
- Google Calendar API
- Evolution API
- YAML para configuracao

## Estrutura

```text
wpp-dental/
|-- config/
|-- deploy/
|-- src/
|   |-- application/
|   |-- domain/
|   |-- infrastructure/
|   `-- interfaces/
|-- tests/
|-- .env.example
|-- pyproject.toml
|-- requirements.txt
`-- README.md
```

## Camadas

- `domain`: regras centrais, entidades e politicas de negocio.
- `application`: fluxo de conversa, servicos de caso de uso e orquestracao.
- `infrastructure`: banco, configuracao e integracoes externas.
- `interfaces`: HTTP e tools adaptadas para o mundo externo.

## Desenvolvimento local

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
uvicorn src.main:app --host 0.0.0.0 --port 3000 --reload
```

## Testes

```bash
.\.venv\Scripts\python -m pytest -q
```

## Deploy no EasyPanel

O projeto agora pode ser publicado direto pelo Git usando o `Dockerfile` da raiz.

### Configuracao recomendada

- Source: repositorio Git
- Port: `3000`
- Replicas/instances: `1`
- Volume persistente para `/app/data`
- Volume ou secret file para `/app/credentials/service-account.json` se usar arquivo JSON do Google

### Variaveis de ambiente

- `OPENAI_API_KEY`
- `EVOLUTION_API_URL`
- `EVOLUTION_API_KEY`
- `EVOLUTION_INSTANCE`
- `WEBHOOK_API_KEY`
- `GOOGLE_CALENDAR_ID`
- `GOOGLE_SERVICE_ACCOUNT_FILE=/app/credentials/service-account.json`
- `GOOGLE_SERVICE_ACCOUNT_JSON_BASE64` como alternativa mais pratica ao arquivo
- `GOOGLE_SERVICE_ACCOUNT_JSON` como alternativa ao arquivo
- `GOOGLE_SERVICE_ACCOUNT_EMAIL` e `GOOGLE_PRIVATE_KEY` como alternativa ao arquivo
- `DOCTOR_PHONE`
- `DATABASE_PATH=/app/data/dental.db`
- `HOST=0.0.0.0`
- `PORT=3000`
- `WORKERS=1`
- `ENABLE_APPOINTMENT_CONFIRMATION_SCHEDULER=1`
- `CONVERSATION_ENGINE=legacy` por padrao

### Observacoes importantes

- Como o banco atual e SQLite, mantenha apenas `1` replica no EasyPanel.
- O cron interno das 20h roda dentro da aplicacao, entao o container precisa permanecer online nesse horario.
- Se usar mais de um processo ou mais de uma replica, voce corre risco de comportamento concorrente indesejado com SQLite.

## Migracao para Rasa CALM

O repositorio agora inclui uma base de migracao em [`rasa_assistant/`](./rasa_assistant/).

### O que essa migracao cobre agora

- perguntas contextuais sobre convenios
- perguntas sobre endereco
- regras operacionais de procedimento
- encaminhamento enxuto para Dra. Tarcilia
- rephrase das respostas para soar mais natural
- fallback controlado para o workflow legado em agendamento, remarcacao, cancelamento e consulta

### Como o backend atual conversa com o Rasa

O webhook FastAPI continua sendo a porta de entrada da Evolution. Quando `CONVERSATION_ENGINE=rasa`, ele passa a enviar a mensagem para o Rasa via REST webhook e, se o Rasa estiver indisponivel, pode voltar automaticamente para o motor legado com `RASA_FALLBACK_TO_LEGACY=1`.

Variaveis novas:

- `CONVERSATION_ENGINE=rasa`
- `RASA_ASSISTANT_URL=http://wpp-dental-rasa:5005`
- `RASA_TIMEOUT_SECONDS=15`
- `RASA_FALLBACK_TO_LEGACY=1`
- `RASA_PRO_LICENSE=...`
- `RASA_OPENAI_MODEL=gpt-4o-mini`

### Servico extra no EasyPanel

Para rodar o Rasa em producao, suba um segundo servico apontando para este mesmo repositorio:

- Dockerfile: `Dockerfile.rasa`
- Porta interna: `5005`
- Replicas: `1`
- Volume persistente: `/app/data`

Variaveis do servico Rasa:

- `OPENAI_API_KEY`
- `RASA_PRO_LICENSE`
- `RASA_OPENAI_MODEL`
- `DATABASE_PATH=/app/data/dental.db`
- `GOOGLE_CALENDAR_ID`
- `GOOGLE_SERVICE_ACCOUNT_FILE` ou uma das alternativas em JSON
- `DOCTOR_PHONE`

No servico principal da API, aponte `RASA_ASSISTANT_URL` para o hostname interno do servico Rasa, por exemplo `http://wpp-dental-rasa:5005`.

### Limitacao importante

O projeto ja esta preparado para a migracao hibrida, mas eu nao validei o runtime do Rasa localmente neste ambiente porque o pacote/licenca do Rasa Pro nao estava instalado aqui. O que ficou validado nesta maquina foi a integracao do backend, o bridge HTTP, o fallback e a camada de contexto reaproveitada pelas actions.

## Deploy na VPS

### 1. Preparar a aplicacao

```bash
git clone <repo-url> /opt/wpp-dental
cd /opt/wpp-dental
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
chmod +x deploy/start.sh
```

### 2. Configurar ambiente

Preencha o arquivo `.env` com:

- `OPENAI_API_KEY`
- `EVOLUTION_API_URL`
- `EVOLUTION_API_KEY`
- `EVOLUTION_INSTANCE`
- `WEBHOOK_API_KEY`
- `GOOGLE_CALENDAR_ID`
- `DOCTOR_PHONE`
- `DATABASE_PATH`

Google Calendar pode ser configurado destas formas:

1. Arquivo JSON em `./credentials/service-account.json`
2. Variavel `GOOGLE_SERVICE_ACCOUNT_JSON_BASE64` com o conteudo completo do JSON em base64
3. Variavel `GOOGLE_SERVICE_ACCOUNT_JSON` com o conteudo completo do JSON
4. Variaveis `GOOGLE_SERVICE_ACCOUNT_EMAIL` e `GOOGLE_PRIVATE_KEY`

### 3. Subir manualmente

```bash
./deploy/start.sh
```

### 4. Subir com systemd

```bash
sudo cp deploy/wpp-dental.service /etc/systemd/system/wpp-dental.service
sudo systemctl daemon-reload
sudo systemctl enable wpp-dental
sudo systemctl start wpp-dental
sudo systemctl status wpp-dental
```

Antes de usar o arquivo de service, ajuste:

- `User`
- `WorkingDirectory`
- `EnvironmentFile`

## Validacao conversacional

O projeto possui uma bateria com 10 conversas humanas simuladas, com variacoes de escrita, erros de digitacao e manutencao de contexto:

```bash
.\.venv\Scripts\python -m pytest -q tests/test_conversation_context_validation.py
```

## Ponto de entrada

- API principal: `src.main:app`
- Health check: `/health`
- Webhook principal: `/webhook/message`

## Observacoes

- A configuracao operacional fica em `config/*.yaml`.
- O banco SQLite e inicializado automaticamente no startup.
- A fachada `src.main` foi mantida para preservar compatibilidade com deploy e testes.
