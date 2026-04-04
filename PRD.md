# PRD — Dra. Dental AI: Assistente de Agendamento via WhatsApp

## 1. Visão Geral

Sistema de IA para WhatsApp que gerencia **exclusivamente** o agendamento de consultas odontológicas da Dra. [Nome]. A IA atua como uma recepcionista virtual, sendo capaz de:

- Marcar, remarcar, cancelar e consultar agendamentos
- Reconhecer pacientes recorrentes
- Respeitar regras de convênios/planos
- Integrar-se ao Google Calendar da doutora
- Escalar situações fora do escopo para a doutora

> [!IMPORTANT]
> A IA **NÃO** deve fornecer preços, informações sobre procedimentos, diagnósticos ou qualquer orientação clínica. O escopo é exclusivamente **gestão de agenda**.

---

## 2. Público-Alvo

| Perfil | Descrição |
|---|---|
| **Pacientes** | Pessoas que entram em contato via WhatsApp para agendar consultas |
| **Dra. (Administradora)** | Controla a agenda via Google Calendar e recebe alertas via WhatsApp |
| **Equipe técnica** | Gerencia configurações e manutenção do sistema |

---

## 3. Funcionalidades

### 3.1 Agendamento de Consultas

**Marcar consulta:**
1. Paciente inicia contato no WhatsApp
2. IA coleta: **nome** e **telefone** (se não for paciente conhecido)
3. Paciente informa o **convênio/plano**
4. IA valida se o convênio é atendido pela doutora
5. Paciente escolhe o **período** desejado: manhã, tarde ou noite
6. IA consulta o Google Calendar e sugere **2 horários disponíveis** no período
7. Se o paciente pedir um **dia específico**, a IA mostra todos os horários disponíveis naquele dia dentro do período escolhido
8. Paciente confirma o horário
9. IA cria o evento no Google Calendar como: **"Nome - Telefone"**

> [!NOTE]
> Para preservar espaço para encaixes, a IA só deve sugerir automaticamente horários a partir de **2 dias úteis** após a data atual. Se a doutora quiser usar os próximos 2 dias para encaixe manual, esses dias ficam fora da sugestão automática.

**Remarcar consulta:**
1. Paciente solicita remarcação
2. IA localiza a consulta atual pelo nome/telefone
3. Segue o fluxo de agendamento para novo horário
4. Cancela o horário anterior e cria o novo

**Cancelar consulta:**
1. Paciente solicita cancelamento
2. IA localiza e confirma a consulta
3. Remove o evento do Google Calendar

**Consultar consulta:**
1. Paciente pergunta quando é sua próxima consulta
2. IA busca no Google Calendar pelo nome/telefone
3. Retorna data, horário e dia da semana

### 3.2 Gestão de Pacientes

- **Memória:** A IA reconhece pacientes que já entraram em contato anteriormente, usando o número de telefone como identificador principal
- **Histórico simplificado:** Armazena nome, telefone e convênio do paciente para agilizar futuros atendimentos
- **Saudação personalizada:** Pacientes conhecidos recebem saudação pelo nome

### 3.3 Gestão de Convênios/Planos

- A doutora atende atualmente **6-7 planos**
- Cada plano pode ter **restrições de procedimentos** (ex: Plano X não cobre clareamento)
- A configuração dos planos é feita via **arquivo de configuração** (YAML/JSON)
- Deve ser **trivial** adicionar, remover ou editar planos
- Certos convênios devem ser **encaminhados para outra(s) doutora(s)** — a lista de encaminhamentos é configurável

### 3.4 Google Calendar — Integração

- **Leitura:** Verifica horários disponíveis considerando slots de **15 minutos**
- **Escrita:** Cria eventos com o formato "Nome - Telefone"
- **Remoção:** Cancela/remove eventos
- **Bloqueios:** A doutora bloqueia horários/dias **diretamente no Google Calendar**, e a IA respeita esses bloqueios automaticamente
- **Horários variáveis:** Não há horário fixo. A disponibilidade é determinada pela **ausência de bloqueios** no calendário

> [!NOTE]
> A doutora controla sua disponibilidade criando bloqueios no Google Calendar. Se um slot de 15 min não está bloqueado e não tem consulta, ele está disponível.

### 3.5 Sistema de Alertas

| Situação | Ação |
|---|---|
| Pergunta fora do escopo (preços, procedimentos, dúvidas clínicas) | IA envia alerta à doutora via WhatsApp com o contexto da conversa |
| Convênio que deve ser encaminhado | IA alerta a doutora para encaminhar o paciente para outra profissional |
| Qualquer situação não prevista | IA informa o paciente: *"A doutora entrará em contato em breve"* e alerta a doutora |

**Formato do alerta para a doutora:**
- Número do paciente
- Nome (se disponível)
- Resumo da solicitação
- Motivo do escalonamento

---

## 4. Regras de Negócio Críticas

1. **Escopo restrito:** A IA JAMAIS deve oferecer preços, informações sobre procedimentos ou orientação clínica
2. **Horários de 15 min:** Todos os slots são de exatamente 15 minutos
3. **Sugestão de horários:** Sempre sugerir 2 opções dentro do período escolhido
4. **Bloqueios respeitados:** Bloqueos no Google Calendar são invioláveis
5. **Identificação no Calendar:** Formato obrigatório: "Nome - Telefone"
6. **Encaminhamento de convênios:** Convênios configurados para encaminhamento NUNCA devem ser agendados pela IA
7. **Escalação segura:** Na dúvida, escalar para a doutora. Nunca inventar informações.
8. **Janela mínima para sugestão automática:** A IA deve começar a sugerir horários apenas a partir de **2 dias úteis** após o dia atual, preservando os dias mais próximos para possíveis encaixes manuais.

---

## 5. Modelo LLM

| Aspecto | Decisão |
|---|---|
| **Provider** | OpenAI |
| **Modelo recomendado** | `gpt-4o-mini` |
| **Justificativa** | Melhor custo-benefício para tarefas estruturadas. ~$0.15/1M tokens input, ~$0.60/1M tokens output. Dentro do orçamento de R$50-100/mês. |
| **Fallback** | `gpt-4o` para casos complexos (orçamento permitindo) |

---

## 6. Stack Tecnológica

| Componente | Tecnologia |
|---|---|
| **Framework de Agentes** | CrewAI |
| **LLM** | OpenAI GPT-4o-mini |
| **WhatsApp** | Evolution API (já configurado) |
| **Calendário** | Google Calendar API |
| **Banco de dados** | SQLite (pacientes + histórico) |
| **Configuração** | YAML (planos, convênios, mensagens) |
| **Linguagem** | Python 3.11+ |
| **Hospedagem** | VPS própria |

---

## 7. Fora do Escopo (v1)

- Pagamento online
- Agendamento de múltiplos profissionais (exceto encaminhamento)
- Prontuário eletrônico
- Envio de lembretes automáticos (futuro)
- Dashboard/Painel administrativo web
- Atendimento por voz/áudio
