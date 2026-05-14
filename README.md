# WPP-DENTAL

Assistente de agendamento odontologico via WhatsApp, integrado ao Google Calendar e organizado em camadas de arquitetura limpa.

## Objetivo

Atender o fluxo descrito em [`PRD.md`](./PRD.md) com um backend previsivel, testavel e pronto para operacao em VPS.

## Documentacao tecnica

A documentacao tecnica completa da solucao esta em [`docs/TECHNICAL_DOCUMENTATION.md`](./docs/TECHNICAL_DOCUMENTATION.md).

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
- `ADMIN_API_KEY` opcional para proteger o painel `/admin` com uma chave separada
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
- `LANGGRAPH_OPENAI_MODEL=gpt-4o-mini`
- `LANGGRAPH_FALLBACK_TO_LEGACY=1`

### Observacoes importantes

- Como o banco atual e SQLite, mantenha apenas `1` replica no EasyPanel.
- O cron interno das 20h roda dentro da aplicacao, entao o container precisa permanecer online nesse horario.
- Se usar mais de um processo ou mais de uma replica, voce corre risco de comportamento concorrente indesejado com SQLite.

## Camada conversacional com LangGraph

O projeto agora pode usar LangGraph dentro da propria API, sem servico extra e sem licenca separada.

### Como funciona

- `CONVERSATION_ENGINE=legacy`: usa apenas o workflow deterministico atual
- `CONVERSATION_ENGINE=langgraph`: usa um grafo para rotear perguntas contextuais e reescrever respostas informativas com mais naturalidade
- em caso de falha do grafo, `LANGGRAPH_FALLBACK_TO_LEGACY=1` faz a API voltar automaticamente para o motor legado

### O que o LangGraph assume agora

- perguntas sobre endereco
- perguntas sobre convenio
- perguntas sobre regras operacionais de procedimento
- mensagens sociais curtas como `obrigado`, `ok`, `valeu`

### O que continua no workflow legado

- agendamento
- remarcacao
- cancelamento
- consulta da proxima consulta
- confirmacao automatica
- encaminhamento e estados operacionais sensiveis

Isso deixa a migracao mais segura: o LangGraph melhora a sensibilidade da conversa, mas a parte critica da agenda continua no fluxo que ja conhece calendario, banco e regras da clinica.

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
- `ADMIN_API_KEY` opcional
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
- Painel administrativo: `/admin`

## Painel administrativo

O painel web fica em `/admin` e permite acompanhar status, erros, conversas, marcacoes futuras e bloqueios de agenda.

Os endpoints do painel aceitam a chave `ADMIN_API_KEY`. Se ela nao estiver configurada, usam `WEBHOOK_API_KEY` ou `EVOLUTION_WEBHOOK_API_KEY` como fallback. Em desenvolvimento local sem nenhuma dessas chaves, o painel fica aberto.

Bloqueios de dia sao criados como eventos de dia inteiro no Google Calendar. A regra de disponibilidade ja interpreta eventos de dia inteiro como agenda bloqueada, entao o agente deixa de sugerir horarios nessas datas.

## Observacoes

- A configuracao operacional fica em `config/*.yaml`.
- O banco SQLite e inicializado automaticamente no startup.
- A fachada `src.main` foi mantida para preservar compatibilidade com deploy e testes.
