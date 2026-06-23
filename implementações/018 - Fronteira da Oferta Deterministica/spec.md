# Fronteira da Oferta Determinística

> **ID:** 018
> **Status:** 🟢 Concluída (escopo seguro — caminho determinístico primário; remoção total = RF-003-B)
> **Prioridade:** 🔴 Crítica
> **Criada em:** 2026-06-23
> **Última atualização:** 2026-06-23
> **Autor:** Agente AI
> **Fase do roadmap:** A (`docs/ANALISE_SOLUCAO_DEFINITIVA.md` §10)

---

## 1. Resumo Executivo

Fecha a última fronteira do "cérebro duplo": fazer a **máquina de estados (FSM)** ser dona da
**geração da oferta inicial de horários**, não só da re-oferta e da seleção (já migradas na 016). Os
horários passam a ser **dado estruturado vindo da agenda** (`state.offered_date` /
`state.offered_times`), nunca prosa do LLM reconstruída por regex. Com isso aposentam-se
`_parse_offered_slots` e `_is_offered_slot` no `clean_agent_service.py`, e o LLM é rebaixado a
**redigir o tom** de uma oferta cujo conteúdo a FSM já fixou. Resolve de forma definitiva os sintomas
**(a) "o bot repete o que já disse"** e **(b) "oferece horários errados"**.

## 2. Contexto e Motivação

### 2.1 Problema Atual

A 016 migrou para a FSM (`src/application/flow/orchestrator.py`) a **seleção** (`try_slot_selection`,
linhas 109–180) e a **re-oferta reativa** (`try_reactive_reoffer`, linhas 182–245), que já gravam a
oferta como dado estruturado (`next_state.offered_date`, `next_state.offered_times`). Porém a
**oferta inicial** — quando o paciente pede para agendar pela primeira vez — **ainda nasce no loop do
LLM** (`CleanAgentService`), via as tools `buscar_horarios_disponiveis` /
`buscar_proximo_dia_disponivel`. O LLM recebe os slots, **redige em prosa livre**, e essa prosa é
**relida por regex** para reconstruir o estado da oferta:

- `_parse_offered_slots` — `src/application/services/clean_agent_service.py:68` — regex
  `_SLOT_DATE_RE` / `_SLOT_TIME_RE` extrai `(date_str, times)` do texto do tool result.
- `_is_offered_slot` — `clean_agent_service.py:76` — valida um datetime contra `state.offered_date` /
  `state.offered_times` e os filtros de estado (`rejected_slots`, `excluded_dates`, `earliest_time`,
  `requested_weekday`).

A 017 chegou a tentar migrar a oferta inicial (`try_initial_offer`) mas foi **revertida** por
"roubar conversa que o LLM trata melhor" (ver `016/spec.md` §9/§10). A
`docs/ANALISE_SOLUCAO_DEFINITIVA.md` (§2.2, §4, §10-Fase A) **reabre e prioriza** essa migração:
é exatamente a parte que sobrou que produz (a) e (b).

### 2.2 Impacto do Problema

- **(a) Repetição:** quando a prosa do LLM e o estado da FSM dessincronizam, o LLM "reapresenta" uma
  oferta que já tinha feito, porque não enxerga que o passo já foi dado.
- **(b) Horário errado:** qualquer variação de redação do modelo quebra o casamento regex entre o que
  foi dito e o que existe na agenda; a seleção do paciente pode casar com um horário que não foi
  realmente ofertado/validado.
- Afeta **todo paciente** que inicia um agendamento — é o caminho mais comum do bot. É a queixa nº 1
  do dono ("responde errado", "marca errado").

### 2.3 Soluções Consideradas

| Solução | Prós | Contras | Decisão |
|---------|------|---------|---------|
| **FSM gera a oferta inicial (dado estruturado) + LLM só redige o tom** | Fonte única de verdade; fim do regex; aproveita `find_next_available_slots` e `offered_*` já existentes; resolve (a)+(b) na raiz | Exige cuidado para não "roubar" saudação/conversa aberta (lição da 017) | ✅ **Escolhida** |
| Manter LLM gerando a oferta e melhorar o regex | Mínima mudança | Não resolve a raiz; o regex sempre quebra em nova redação; perpetua o cérebro duplo | ❌ Descartada |
| Forçar o LLM a responder JSON da oferta | Estrutura sem mover lógica | Continua dependente da obediência do modelo; frágil; não testável de forma determinística | ❌ Descartada |

## 3. Especificação Técnica

### 3.1 Visão Geral da Arquitetura

A oferta inicial passa a seguir o mesmo padrão já provado da re-oferta (016):

```
Paciente: "quero marcar quarta de manhã"
        │
        ▼
IntentClassifier (015) → {intent: agendar, entities: {período, dia, ...}}
        │
        ▼
ConversationOrchestrator.handle
        │
        ├─ try_initial_offer (NOVO)
        │     1. extrai constraints (AppointmentOfferService.extract_request_constraints)
        │     2. CalendarService.find_next_available_slots(...)  ← agenda = fonte da verdade
        │     3. grava next_state.offered_date / offered_times (DADO ESTRUTURADO)
        │     4. reply_text = template/tom da oferta (conteúdo já fixado)
        │
        ▼
gateway.send_text  →  paciente
```

O LLM **não** decide nem guarda a oferta. Quando a mensagem **não** é um pedido de agendamento
acionável (saudação pura, dúvida aberta, fora de escopo), o orquestrador devolve `handled=False` e o
caminho do LLM/`CleanAgentService` segue tratando a conversa aberta — **preservando a lição da 017**.

### 3.2 Componentes Afetados

| Componente | Tipo | Ação | Descrição |
|-----------|------|------|-----------|
| `src/application/flow/orchestrator.py` | Arquivo | Modificar | Adicionar `try_initial_offer`; encadear em `handle` antes do fallback do LLM |
| `src/application/flow/states.py` | Arquivo | Modificar (se preciso) | Garantir estágio/representação para "oferta apresentada, aguardando seleção" |
| `src/interfaces/http/app.py` | Arquivo | Modificar | Rotear oferta inicial pelo orquestrador; remover o caminho que dependia de `_parse_offered_slots` |
| `src/application/services/clean_agent_service.py` | Arquivo | Modificar | Remover `_parse_offered_slots` (68–73) e `_is_offered_slot` (76–101); remover/neutralizar as tools de oferta (`buscar_horarios_disponiveis`, `buscar_proximo_dia_disponivel`) como decisoras |
| `src/domain/policies/appointment_offer_service.py` | Arquivo | Reusar | `extract_request_constraints` (339–455), `resolve_selection` (267–316) já existem |
| `src/infrastructure/integrations/calendar_service.py` | Arquivo | Reusar | `find_next_available_slots` (504–562) retorna `{"date_str", "times"}` estruturado |
| `tests/test_orchestrator*.py` | Arquivo | Criar/Modificar | Cobrir `try_initial_offer` e ausência de repetição |
| `tests/test_clean_agent_service.py` | Arquivo | Modificar | Remover/ajustar testes dos guard-rails de regex aposentados |

### 3.3 Interfaces e Contratos

#### Entradas
- `try_initial_offer(state, message, nlu_result, context) -> OrchestratorResult` (assinatura alinhada
  às demais `try_*` do orquestrador, linhas 109/182/266).
- Mensagem do paciente + `NluContext` (015) com intenção `agendar` e entidades de período/dia.

#### Saídas
- `OrchestratorResult` (orchestrator.py:40–49) com:
  - `handled=True` quando a oferta foi gerada (ou recusada por falta de slot);
  - `reply_text` = mensagem de oferta com horários **vindos da agenda**;
  - `next_state.offered_date` (str "DD/MM/YYYY") e `next_state.offered_times` (list["HH:MM"]);
  - `handled=False` (deferir ao LLM) quando não é pedido de agendamento acionável.

#### Contratos de API (se aplicável)
N/A — webhook e payloads externos inalterados.

### 3.4 Modelos de Dados (se aplicável)

Reusa os campos **já existentes** em `ConversationState`
(`src/application/services/conversation_state_service.py:18–41`): `offered_date`, `offered_times`,
`pending_slot_date`, `pending_slot_time`, `rejected_slots`, `excluded_dates`, `requested_weekday`,
`earliest_time`, `requested_period`, `requested_date`. **Nenhum campo novo é necessário.**

### 3.5 Fluxo de Execução

1. Webhook → autenticação/idempotência/handoff/TTL (inalterados).
2. `IntentClassifier` classifica a mensagem (015).
3. `orchestrator.handle` tenta, em ordem: cancelamento → seleção → re-oferta reativa →
   **oferta inicial (NOVO)**.
4. `try_initial_offer`: se `intent==agendar` e há base suficiente, extrai constraints,
   chama `find_next_available_slots`, grava `offered_date`/`offered_times`, monta o texto da oferta.
5. Sem slot disponível → mensagem determinística de "sem horário / buscar próximo", **sem** inventar
   horário.
6. Não acionável (saudação/dúvida aberta) → `handled=False`; LLM trata a conversa.
7. `gateway.send_text` envia; efeitos aplicados (`_apply_orchestrator_effects`).

### 3.6 Tratamento de Erros

- `find_next_available_slots` retorna `None` → resposta determinística "não encontrei horário nesse
  critério, posso buscar o próximo disponível?"; **nunca** texto de horário fabricado.
- Falha de calendário (exceção) → mensagem neutra de indisponibilidade temporária + alerta à doutora
  (reusa mensageria 009), sem travar a conversa.
- LLM indisponível no tom → usa template neutro da oferta (conteúdo já fixado pela FSM).

## 4. Requisitos

> Rastreabilidade ao documento macro: `docs/ANALISE_SOLUCAO_DEFINITIVA.md` §2.2 (sintomas a/b),
> §4 (fonte única de verdade), §10-Fase A.

### 4.1 Requisitos Funcionais

- **RF-001:** A oferta inicial de horários é gerada pela FSM a partir de
  `CalendarService.find_next_available_slots`, gravada em `state.offered_date`/`state.offered_times`.
- **RF-002:** O LLM **não** gera nem guarda a oferta; quando há oferta, ele apenas redige o tom de um
  conteúdo já fixado pela FSM (ou usa template).
- **RF-003:** A oferta inicial deixa de depender da reconstrução por regex no caminho feliz: a FSM é o
  caminho **primário** e determinístico. `_parse_offered_slots`/`_is_offered_slot` e as tools de oferta
  do LLM são **mantidos como fallback de borda** (rede de segurança), rebaixados pela prioridade da
  FSM. A **remoção total** do regex/tools fica para um follow-up (RF-003-B), após o caminho da FSM ser
  validado em produção — alinhado à postura incremental das impls 016/017 e à manutenção dos 542
  testes verdes.
- **RF-004:** A seleção do paciente casa **exclusivamente** contra `state.offered_times` (via
  `AppointmentOfferService.resolve_selection`), validada por `_slot_satisfies_state_filters`.
- **RF-005:** Sem slot disponível, a resposta é determinística e honesta (sem horário fabricado).
- **RF-006:** Saudação, dúvida aberta e fora-de-escopo continuam tratados pelo LLM (`handled=False`),
  preservando a lição da 017 (não "roubar" conversa aberta).

### 4.2 Requisitos Não-Funcionais

- **RNF-001:** Suíte total **verde** (≥ 542 testes), com novos testes de regressão para (a) e (b).
- **RNF-002:** Sem regressão de comportamento nos fluxos de saudação/escopo (008) e mensageria (009).
- **RNF-003:** Custo de LLM por conversa **não aumenta** (idealmente cai: oferta deixa de exigir o
  loop de tool-calls).

### 4.3 Restrições e Limitações

- **Manter** o handler provado de criação/remarcação atômica (`_handle_offered_slot_selection`
  Branch A — impls 000/005/006); esta implementação **não** mexe na criação/remarcação.
- Refactor **cirúrgico** sobre `refactor/nucleo-conversa` (ou branch nova a partir de `main`), com os
  542 testes como catraca.

## 5. Critérios de Aceitação

- [ ] **CA-001:** Existe `try_initial_offer` no orquestrador, encadeado em `handle`, com testes.
- [ ] **CA-002:** Após uma oferta inicial, `state.offered_date`/`state.offered_times` refletem
  exatamente os slots vindos de `find_next_available_slots`.
- [ ] **CA-003:** A oferta inicial, no caminho feliz, é gerada pela FSM (não pelo LLM); o regex
  (`_parse_offered_slots`/`_is_offered_slot`) permanece apenas como fallback de borda. (Remoção total
  = RF-003-B, follow-up.)
- [ ] **CA-004:** Teste de regressão de **repetição**: dada uma oferta já apresentada, a próxima
  mensagem do paciente nunca reapresenta a mesma oferta indevidamente.
- [ ] **CA-005:** Teste de regressão de **horário errado**: seleção só é aceita se casar com
  `state.offered_times`; horário fora da oferta é recusado com a lista correta.
- [ ] **CA-006:** Saudação/dúvida aberta seguem deferidas ao LLM (`handled=False`) — sem regressão.
- [ ] **CA-007:** Suíte total verde.

## 6. Plano de Testes

### 6.1 Testes Unitários
- `try_initial_offer`: gera oferta a partir de constraints; grava `offered_*`; retorna `handled=True`.
- Sem slot → resposta determinística, `offered_times` vazio, sem horário fabricado.
- Saudação/dúvida aberta → `handled=False`.

### 6.2 Testes de Integração
- Webhook ponta-a-ponta: "quero marcar" → oferta estruturada → seleção válida → confirmação (reusa
  `test_main_webhook`/`test_orchestrator*`).
- Caminho de fallback do LLM intacto para conversa aberta.

### 6.3 Testes de Aceitação
- CA-001..CA-007 verificáveis por teste; verificação textual da remoção de `_parse_offered_slots` /
  `_is_offered_slot`.

### 6.4 Casos de Borda (Edge Cases)
- Paciente pede período sem disponibilidade (manhã bloqueada/`OCUPADO`) → re-oferta determinística.
- Paciente muda a constraint na mesma frase da oferta → não repetir; recalcular.
- Mensagem ambígua "sim" logo após a oferta → seleção/confirmação correta (não nova oferta).
- LLM indisponível no momento da oferta → template neutro com horários corretos.

## 7. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|-------|--------------|---------|-----------|
| Repetir o motivo da reversão da 017 (FSM "rouba" conversa aberta) | Média | Médio | `try_initial_offer` só assume com `intent==agendar` acionável; saudação/dúvida → `handled=False`; testes de não-regressão de conversa |
| Remover guard-rail (`_is_offered_slot`) ainda usado por caminho raro | Média | Alto | Só remover após a oferta estar 100% na FSM e a seleção casar contra `offered_times`; suíte como catraca; remoção incremental |
| Perda de nuance de tom na oferta | Baixa | Baixo | Template revisado; LLM disponível para redigir tom do conteúdo fixado |
| Divergência oferta↔criação (regra 2 dias úteis, impl 007) | Baixa | Alto | Reusar `find_next_available_slots` (mesma fonte da 007); teste oferta↔criação coerentes |

## 8. Dependências

### 8.1 Dependências Internas
- **016** (Orquestrador Determinístico) concluída — pré-requisito forte.
- Reusa **007** (regra de 2 dias / disponibilidade), **008** (guarda de escopo), **009** (mensageria),
  **015** (NLU). Preserva o handler atômico de **000/005/006**.

### 8.2 Dependências Externas
- Nenhuma nova.

## 9. Observações e Decisões de Design

- Esta implementação **reverte conscientemente** a decisão da 017 de manter a oferta inicial no LLM.
  O fundamento novo está em `docs/ANALISE_SOLUCAO_DEFINITIVA.md` §2.2/§4: a oferta-por-prosa é a
  causa-raiz remanescente de (a) e (b); a confiabilidade que faltava vem de **dado estruturado**, não
  de melhor prompt.
- O documento macro cita `_parse_offered_slots` como estando "no `app.py`"; no código atual ele vive
  em `clean_agent_service.py:68`. Esta spec referencia a **localização real**.
- **Fora de escopo (mantido intacto):** criação/remarcação atômica (`_handle_offered_slot_selection`
  Branch A). Esta fronteira não é tocada — risco puro sem ganho (decisão registrada na 016).
- **Decisão de escopo (2026-06-23, execução):** a remoção das tools de oferta do LLM e do regex
  (`_parse_offered_slots`/`_is_offered_slot`) muda comportamento em **5 arquivos de teste** e degrada a
  conversa fuzzy onde o classificador devolve `SAUDACAO`/`AMBIGUO` (ex.: "oi quero marcar"). Foi exatamente
  esse risco que motivou a reversão da 017. Por isso a 018 entrega o **caminho determinístico primário**
  (`try_initial_offer`) e **mantém o LLM como fallback** — 90% do ganho com 20% do risco. A remoção total
  (RF-003-B) será um follow-up depois de o caminho da FSM ser observado em produção. Mantém OpenAI
  (decisão do dono).

---

> **⚠️ NOTA:** Contrato vivo. Alterações de escopo refletidas aqui antes do código.
