# SPEC — Dra. Dental AI: Especificação Técnica

## 1. Arquitetura Geral

```
┌─────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  WhatsApp    │────▶│  Evolution API   │────▶│  Webhook Server  │
│  (Paciente)  │◀────│  (Bridge)        │◀────│  (FastAPI)       │
└─────────────┘     └──────────────────┘     └────────┬─────────┘
                                                       │
                                                       ▼
                                              ┌──────────────────┐
                                              │   CrewAI Engine   │
                                              │                  │
                                              │  ┌────────────┐  │
                                              │  │Recepcionista│  │
                                              │  └─────┬──────┘  │
                                              │        │         │
                                              │  ┌─────▼──────┐  │
                                              │  │  Agendador  │  │
                                              │  └─────┬──────┘  │
                                              │        │         │
                                              │  ┌─────▼──────┐  │
                                              │  │  Validador  │  │
                                              │  └─────┬──────┘  │
                                              │        │         │
                                              │  ┌─────▼──────┐  │
                                              │  │  Escalador  │  │
                                              │  └────────────┘  │
                                              └────────┬─────────┘
                                                       │
                                         ┌─────────────┼─────────────┐
                                         ▼             ▼             ▼
                                  ┌────────────┐ ┌──────────┐ ┌──────────┐
                                  │  Google     │ │  SQLite  │ │  Config  │
                                  │  Calendar   │ │  (DB)    │ │  (YAML)  │
                                  └────────────┘ └──────────┘ └──────────┘
```

---

## 2. Estrutura de Diretórios

```
wpp-dental/
├── README.md
├── PRD.md
├── SPEC.md
├── pyproject.toml
├── .env.example
├── config/
│   ├── plans.yaml              # Convênios e regras
│   ├── messages.yaml           # Templates de mensagens
│   └── settings.yaml           # Configurações gerais
├── src/
│   ├── __init__.py
│   ├── main.py                 # Entry point - Webhook server
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── receptionist.py     # Agente Recepcionista
│   │   ├── scheduler.py        # Agente Agendador
│   │   ├── validator.py        # Agente Validador de Regras
│   │   └── escalator.py        # Agente Escalador/Alertas
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── calendar_tool.py    # Google Calendar CRUD
│   │   ├── whatsapp_tool.py    # Evolution API wrapper
│   │   ├── patient_tool.py     # CRUD de pacientes (SQLite)
│   │   └── config_tool.py      # Leitura de configurações
│   ├── models/
│   │   ├── __init__.py
│   │   ├── patient.py          # Modelo Paciente
│   │   └── appointment.py      # Modelo Agendamento
│   ├── services/
│   │   ├── __init__.py
│   │   ├── calendar_service.py # Lógica do Google Calendar
│   │   ├── whatsapp_service.py # Lógica da Evolution API
│   │   └── alert_service.py    # Lógica de alertas
│   ├── crew/
│   │   ├── __init__.py
│   │   └── dental_crew.py      # Orquestração CrewAI
│   └── database/
│       ├── __init__.py
│       ├── connection.py       # Conexão SQLite
│       └── migrations.py       # Setup inicial do banco
└── tests/
    ├── __init__.py
    ├── test_calendar.py
    ├── test_agents.py
    └── test_config.py
```

---

## 3. Agentes CrewAI

### 3.1 Agente Recepcionista (`receptionist.py`)

**Papel:** Primeiro ponto de contato. Identifica o paciente e determina a intenção.

| Aspecto | Detalhe |
|---|---|
| **Trigger** | Toda mensagem recebida |
| **Responsabilidades** | Saudar, identificar paciente (novo/existente), coletar nome/telefone, identificar intenção (agendar, remarcar, cancelar, consultar) |
| **Tools** | `patient_tool` (busca/cadastro), `config_tool` (mensagens) |
| **Output** | Paciente identificado + intenção clara para o próximo agente |

**Comportamento:**
- Paciente conhecido → "Olá, [Nome]! Como posso ajudar?"
- Paciente novo → Coleta nome e telefone antes de prosseguir
- Intenção ambígua → Pergunta clarificadora

### 3.2 Agente Agendador (`scheduler.py`)

**Papel:** Gerencia toda a lógica de agenda com o Google Calendar.

| Aspecto | Detalhe |
|---|---|
| **Trigger** | Intenção de agendar/remarcar/cancelar/consultar recebida do Recepcionista |
| **Responsabilidades** | Consultar disponibilidade, sugerir horários, criar/alterar/remover eventos |
| **Tools** | `calendar_tool` (CRUD Google Calendar) |
| **Output** | Confirmação de agendamento ou opções disponíveis |

**Lógica de sugestão de horários:**
```
Períodos:
  Manhã:  07:00 - 12:00
  Tarde:  12:00 - 18:00
  Noite:  18:00 - 21:00

Regras:
  1. Buscar slots livres de 15 min no período solicitado
  2. Sugerir 2 horários (preferencialmente os mais próximos do horário atual)
  3. Se dia específico: listar todos os slots livres do período naquele dia
  4. Slot livre = sem evento E sem bloqueio no Google Calendar
```

### 3.3 Agente Validador (`validator.py`)

**Papel:** Valida regras de negócio antes de confirmar agendamentos.

| Aspecto | Detalhe |
|---|---|
| **Trigger** | Antes de qualquer confirmação de agendamento |
| **Responsabilidades** | Validar convênio, verificar restrições de procedimentos, verificar encaminhamentos |
| **Tools** | `config_tool` (regras de planos) |
| **Output** | Aprovação ou rejeição com motivo |

**Validações:**
1. Convênio é atendido pela doutora?
2. Convênio deve ser encaminhado para outra profissional?
3. O procedimento solicitado é coberto pelo convênio?

### 3.4 Agente Escalador (`escalator.py`)

**Papel:** Gerencia situações que fogem do escopo da IA.

| Aspecto | Detalhe |
|---|---|
| **Trigger** | Pergunta fora do escopo, convênio para encaminhamento, situação não prevista |
| **Responsabilidades** | Alertar a doutora via WhatsApp, informar o paciente que será contatado |
| **Tools** | `whatsapp_tool` (envio de alerta), `config_tool` (número da doutora) |
| **Output** | Alerta enviado + paciente informado |

**Formato do alerta:**
```
🔔 *Alerta do Assistente*

👤 Paciente: [Nome]
📱 Telefone: [Número]
📋 Solicitação: [Resumo]
⚠️ Motivo: [Fora do escopo / Encaminhamento / Outro]

💬 Última mensagem:
"[mensagem do paciente]"
```

---

## 4. Tools (Ferramentas CrewAI)

### 4.1 `calendar_tool.py` — Google Calendar

```python
# Funções principais:
get_available_slots(date, period) -> list[TimeSlot]
create_appointment(patient_name, phone, datetime) -> Event
cancel_appointment(event_id) -> bool
reschedule_appointment(event_id, new_datetime) -> Event
find_appointment_by_patient(name, phone) -> Event | None
is_slot_available(datetime) -> bool
```

**Autenticação:** Service Account com acesso ao calendário da doutora.

### 4.2 `whatsapp_tool.py` — Evolution API

```python
# Funções principais:
send_message(phone, message) -> bool
send_alert_to_doctor(patient_info, reason, context) -> bool
get_contact_name(phone) -> str | None
```

**Base URL:** Configurável via `.env`

### 4.3 `patient_tool.py` — SQLite

```python
# Funções principais:
find_patient(phone) -> Patient | None
create_patient(name, phone, plan) -> Patient
update_patient(phone, **kwargs) -> Patient
get_patient_history(phone) -> list[Interaction]
```

### 4.4 `config_tool.py` — Configurações

```python
# Funções principais:
get_plans() -> list[Plan]
get_plan_restrictions(plan_name) -> PlanRestrictions
get_referral_plans() -> list[str]
get_message_template(key) -> str
get_doctor_phone() -> str
```

---

## 5. Banco de Dados (SQLite)

### 5.1 Tabela `patients`

| Coluna | Tipo | Descrição |
|---|---|---|
| id | INTEGER PK | Auto-incremento |
| phone | TEXT UNIQUE | Telefone (identificador principal) |
| name | TEXT | Nome do paciente |
| plan | TEXT | Convênio/plano atual |
| created_at | DATETIME | Data de cadastro |
| updated_at | DATETIME | Última atualização |

### 5.2 Tabela `interactions`

| Coluna | Tipo | Descrição |
|---|---|---|
| id | INTEGER PK | Auto-incremento |
| patient_id | INTEGER FK | Referência ao paciente |
| type | TEXT | Tipo: schedule, reschedule, cancel, query, escalation |
| summary | TEXT | Resumo da interação |
| created_at | DATETIME | Data da interação |

---

## 6. Configurações (YAML)

### 6.1 `config/plans.yaml`

```yaml
plans:
  - name: "Amil Dental"
    active: true
    referral: false
    restrictions:
      - "clareamento"
      - "implante"
  
  - name: "Bradesco Dental"
    active: true
    referral: false
    restrictions: []
  
  - name: "SulAmérica"
    active: true
    referral: true            # Encaminhar para outra doutora
    referral_message: "Este convênio é atendido pela Dra. [Nome]. A doutora entrará em contato para encaminhar."
  
  # Adicionar novos planos aqui seguindo o mesmo formato
```

### 6.2 `config/settings.yaml`

```yaml
# Configurações gerais
doctor:
  name: "Dra. [Nome]"
  phone: "+5511999999999"      # WhatsApp da doutora (para alertas)
  calendar_id: "primary"       # ID do Google Calendar

# Horários dos períodos
periods:
  morning:
    start: "07:00"
    end: "12:00"
  afternoon:
    start: "12:00"
    end: "18:00"
  evening:
    start: "18:00"
    end: "21:00"

# Configurações de agendamento
scheduling:
  slot_duration_minutes: 15
  suggestions_count: 2          # Quantos horários sugerir
  max_days_ahead: 30            # Agendar até X dias no futuro

# Evolution API
evolution_api:
  base_url: "http://localhost:8080"
  instance: "dental-bot"
  api_key: "${EVOLUTION_API_KEY}"  # Variável de ambiente

# OpenAI
openai:
  model: "gpt-4o-mini"
  temperature: 0.3              # Baixo para respostas consistentes
  max_tokens: 500
```

### 6.3 `config/messages.yaml`

```yaml
# Templates de mensagens personalizáveis
greeting:
  new_patient: "Olá! 😊 Eu sou a assistente virtual da {doctor_name}. Para começar, poderia me informar seu nome completo?"
  returning_patient: "Olá, {patient_name}! 😊 Que bom ter você de volta! Como posso ajudar hoje?"

scheduling:
  ask_period: "Para qual período você prefere? \n\n🌅 *Manhã* (7h - 12h)\n☀️ *Tarde* (12h - 18h)\n🌙 *Noite* (18h - 21h)"
  suggest_slots: "Encontrei esses horários disponíveis:\n\n1️⃣ {slot_1}\n2️⃣ {slot_2}\n\nQual prefere?"
  confirmed: "✅ Consulta agendada!\n\n📅 *Data:* {date}\n🕐 *Horário:* {time}\n👩‍⚕️ *Dra.* {doctor_name}\n\nCaso precise remarcar ou cancelar, é só me chamar!"
  cancelled: "❌ Consulta cancelada com sucesso.\n\n📅 Era em: {date} às {time}\n\nSe precisar agendar novamente, estou à disposição!"
  no_slots: "Infelizmente não há horários disponíveis nesse período. Gostaria de tentar outro período ou outro dia?"

escalation:
  to_patient: "Entendo sua dúvida! Vou encaminhar para a {doctor_name} que entrará em contato com você em breve. 😊"
  referral: "Este convênio é atendido por outra profissional parceira. A {doctor_name} entrará em contato para realizar o encaminhamento."

errors:
  plan_not_found: "Desculpe, não encontrei esse convênio em nosso sistema. Poderia verificar o nome do plano?"
  general: "Desculpe, tive um probleminha. A {doctor_name} será notificada e entrará em contato."
```

---

## 7. Fluxo de Webhook

```
Evolution API → POST /webhook/message
                     │
                     ▼
              ┌──────────────┐
              │ Parse Message │
              │ Extract:      │
              │  - phone      │
              │  - text       │
              │  - name       │
              └──────┬───────┘
                     │
                     ▼
              ┌──────────────┐
              │  CrewAI Crew  │
              │  kickoff()    │
              │               │
              │  Sequential:  │
              │  1.Receptionist│
              │  2.Validator   │
              │  3.Scheduler   │
              │  4.Escalator   │
              └──────┬───────┘
                     │
                     ▼
              ┌──────────────┐
              │ Send Response │
              │ via Evolution │
              └──────────────┘
```

---

## 8. Variáveis de Ambiente (`.env`)

```env
# OpenAI
OPENAI_API_KEY=sk-...

# Evolution API
EVOLUTION_API_URL=http://localhost:8080
EVOLUTION_API_KEY=your-api-key
EVOLUTION_INSTANCE=dental-bot

# Google Calendar
GOOGLE_CALENDAR_ID=primary
GOOGLE_SERVICE_ACCOUNT_FILE=./credentials/service-account.json

# Database
DATABASE_URL=sqlite:///./data/dental.db

# Doctor
DOCTOR_PHONE=+5511999999999
```

---

## 9. Dependências Principais

```toml
[project]
name = "wpp-dental"
requires-python = ">=3.11"
dependencies = [
    "crewai[tools]>=0.108.0",
    "fastapi>=0.115.0",
    "uvicorn>=0.34.0",
    "google-api-python-client>=2.160.0",
    "google-auth>=2.38.0",
    "httpx>=0.28.0",
    "pyyaml>=6.0",
    "pydantic>=2.10.0",
    "python-dotenv>=1.0.0",
]
```

---

## 10. Considerações de Custo (GPT-4o-mini)

| Métrica | Estimativa |
|---|---|
| Tokens médios por conversa | ~2.000 (input + output) |
| Custo por conversa | ~$0.0005 (~R$0.003) |
| Conversas/dia estimadas | 30-50 |
| Custo diário | ~$0.025 (~R$0.15) |
| **Custo mensal estimado** | **~$0.75 (~R$4.50)** |

> [!TIP]
> Com `gpt-4o-mini` o custo é extremamente baixo, bem dentro do orçamento de R$50-100/mês. Isso deixa margem para eventuais picos ou uso de `gpt-4o` em casos complexos.
