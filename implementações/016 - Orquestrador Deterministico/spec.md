# Orquestrador Determinístico

> **ID:** 016
> **Status:** 🔵 Em Andamento
> **Prioridade:** 🔴 Crítica
> **Criada em:** 2026-06-22
> **Última atualização:** 2026-06-22
> **Autor:** Agente AI

---

## 1. Resumo Executivo

Cria o `ConversationOrchestrator` — uma máquina de estados explícita que passa a ser a **única dona
das decisões** de agendamento (agendar, remarcar, cancelar, confirmar, consultar). Ele consome o
`NluResult` (015) e os fatos da agenda (`CalendarService`) e decide a próxima ação de forma
determinística, absorvendo a lógica hoje dispersa nas dezenas de funções `_handle_*` do `app.py`.
É a fase que elimina o "cérebro duplo": acaba a disputa entre o loop do LLM e a máquina de estados
implícita. O LLM deixa de decidir agenda.

## 2. Contexto e Motivação

### 2.1 Problema Atual
Hoje existem **duas fontes de verdade** disputando a conversa:
- O `ConversationStateService` + ~15 interceptadores determinísticos no `app.py`
  (`_handle_pending_slot_name`, `_handle_pending_slot_plan`, `_handle_offered_slot_selection`,
  `_handle_reactive_reoffer`, `_handle_cancellation_intent`, `_handle_appointment_confirmation`,
  `_capture_schedule_constraints`, ...), e
- O loop de tool-calls do `CleanAgentService`, que decide por conta própria e precisa de
  guard-rails (ex.: bloquear `criar_agendamento` em remarcação, validar slot via regex no texto).

Elas dessincronizam — origem dos fluxos quebrados. Além disso, a verdade do que foi ofertado é
**reconstruída por regex na prosa do LLM** (`_parse_offered_slots`), o que é frágil por construção.

### 2.2 Impacto do Problema
Reincidência de bugs de fluxo (oferta presa, "não está entre as opções", confirmação em loop,
double-booking em remarcação) e um `app.py` de 2.256 linhas praticamente intestável como unidade.

### 2.3 Soluções Consideradas
| Solução | Prós | Contras | Decisão |
|---|---|---|---|
| FSM explícita como única dona da decisão; LLM só NLU/tom | Determinístico, testável, 1 verdade | Esforço alto (fase grande) | ✅ Escolhida |
| Manter LLM no comando e reforçar guard-rails | Menos reescrita | É a causa atual; não converge | ❌ Descartada |
| Reescrever do zero | Design limpo | Perde domínio das impls 000–013 | ❌ Descartada |

## 3. Especificação Técnica

### 3.1 Visão Geral da Arquitetura
Novo módulo `src/application/flow/`. O `ConversationOrchestrator.handle(inbound, state) ->
OrchestratorResult` recebe a `InboundMessage` (014), classifica via `IntentClassifier` (015), e
transiciona uma FSM explícita cujos estados substituem as strings de `stage` atuais. As decisões
de agenda chamam `CalendarService`/`PatientService` diretamente (fonte de verdade). A saída é uma
ação neutra: texto a enviar + novo estado + efeitos (registrar interação, alertar doutora).

```
Estados: IDLE → COLETANDO_INTENCAO → PRECISA_NOME → PRECISA_PLANO
         → OFERTANDO → AGUARDANDO_ESCOLHA → AGUARDANDO_CONFIRMACAO → (CRIA/REMARCA) → IDLE
Ramos:   CANCELAR_CONFIRMACAO · REMARCAR_IDENTIFICAR_ANTIGA · HANDOFF · FORA_ESCOPO
```

### 3.2 Componentes Afetados
| Componente | Tipo | Ação | Descrição |
|---|---|---|---|
| `src/application/flow/__init__.py` | Arquivo | Criar | Exporta `ConversationOrchestrator`, `OrchestratorResult` |
| `src/application/flow/states.py` | Arquivo | Criar | Enum de estados + tabela de transições |
| `src/application/flow/orchestrator.py` | Arquivo | Criar | FSM: decisão de agenda determinística |
| `src/application/services/conversation_state_service.py` | Arquivo | Modificar | `stage` passa a usar o enum de estados (compat preservada) |
| `src/interfaces/http/app.py` | Arquivo | Modificar | Webhook delega ao orquestrador; `_handle_*` migram para a FSM |
| `src/infrastructure/integrations/calendar_service.py` | Arquivo | Reusar | Fonte de verdade da agenda (inalterada) |
| `tests/test_orchestrator.py` | Arquivo | Criar | Testes de transição da FSM (o grupo "cérebro" reescrito) |

### 3.3 Interfaces e Contratos

#### Entradas
- `handle(inbound: InboundMessage, state: ConversationState) -> OrchestratorResult`.

#### Saídas
- `OrchestratorResult`: `reply_text: str | None`, `next_state: ConversationState`,
  `effects: list[Effect]` (ex.: `RegisterInteraction`, `AlertDoctor`, `ActivateHandoff`),
  `status: str` (espelha os status de resposta HTTP atuais para compat dos testes de webhook).

#### Contratos de API (se aplicável)
N/A externo. O `status` interno mapeia os strings de resposta hoje retornados
(`slot_confirmation_resolved`, `reactive_reoffer`, `awaiting_name`, ...).

### 3.4 Modelos de Dados (se aplicável)
Reusa `ConversationState` (campos atuais: `offered_date/times`, `pending_slot_*`, `intent`,
`reschedule_event_id`, `earliest_time`, `excluded_dates`, `rejected_slots`, ...). `stage` migra de
string livre para enum `FlowState` mantendo serialização compatível.

### 3.5 Fluxo de Execução
1. Webhook (após idempotência/handoff/TTL — inalterados) chama `orchestrator.handle`.
2. Orquestrador monta `NluContext` do estado e classifica a mensagem (015).
3. A FSM transiciona conforme `(estado_atual, intent, entidades, fatos_da_agenda)`.
4. Slots ofertados vêm **estruturados** do `CalendarService` e são gravados no estado (fim do
   `_parse_offered_slots`/regex na prosa).
5. Decisões de criação/remarcação/cancelamento chamam o `CalendarService` (atômico, como hoje).
6. Retorna `OrchestratorResult`; o webhook envia `reply_text` via gateway e aplica `effects`.

### 3.6 Tratamento de Erros
- Indisponibilidade de slot na confirmação: mensagem de indisponível + oferta de novas opções
  (preserva o comportamento atual de `_handle_offered_slot_selection`).
- Remarcação parcial (novo criado, antigo não cancelado): preserva o alerta à doutora e o estado
  de remarcação parcial (lógica das impls 000/006 mantida, agora dentro da FSM).
- Intenção fora de escopo/ambígua: delega ao caminho de tom/escalação (refinado em 017).
- Falha inesperada: fallback seguro + log (sem vazar técnico ao paciente).

## 4. Requisitos

### 4.1 Requisitos Funcionais
- **RF-001:** A FSM decide agendar/remarcar/cancelar/confirmar/consultar sem depender do loop do LLM.
- **RF-002:** Slots ofertados são obtidos e armazenados de forma estruturada (sem regex em prosa).
- **RF-003:** Remarcação só via troca atômica determinística (preserva impls 000/006).
- **RF-004:** Cancelamento exige confirmação real antes de cancelar (preserva impl 005).
- **RF-005:** Re-oferta reativa (recusa/horário/dia específico) preserva impl 013.
- **RF-006:** Coleta de nome/plano antes de confirmar (preserva regras atuais).
- **RF-007:** Os `status` retornados batem com as expectativas dos testes de webhook existentes
  (ou os testes são migrados conscientemente para o orquestrador).

### 4.2 Requisitos Não-Funcionais
- **RNF-001:** Toda transição da FSM é coberta por teste unitário.
- **RNF-002:** Nenhuma decisão de agenda depende de texto livre do LLM.
- **RNF-003:** Suíte total verde ao fim (com o grupo "cérebro" reescrito como testes de FSM).

### 4.3 Restrições e Limitações
- O `CleanAgentService` ainda existe ao fim de 016 (aposentado de fato em 017), mas **não decide
  mais agenda** — fica restrito a tom/fallback enquanto 017 não conclui.
- Não alterar `CalendarService`/cron/handoff.

## 5. Critérios de Aceitação
- [ ] **CA-001:** Fluxo feliz de agendamento (novo paciente → nome → plano → oferta → escolha →
  confirma → criado) coberto por teste de FSM, verde.
- [ ] **CA-002:** Remarcação atômica e cancelamento seguro preservados (testes 005/006 verdes).
- [ ] **CA-003:** Re-oferta reativa do 013 preservada (teste 013 verde ou migrado).
- [ ] **CA-004:** `_parse_offered_slots` não é mais usado para validar oferta.
- [ ] **CA-005:** Suíte total verde; `app.py` reduz substancialmente (handlers migrados).

## 6. Plano de Testes

### 6.1 Testes Unitários
`test_orchestrator.py`: cada transição de estado; coleta de nome/plano; oferta/escolha; confirmação;
recusa/re-oferta; cancelamento; remarcação atômica; remarcação parcial.

### 6.2 Testes de Integração
`test_main_webhook.py` e `test_webhook_state_flows.py` adaptados para o orquestrador (mesmas
asserções de comportamento ponta-a-ponta, possivelmente novos `status`).

### 6.3 Testes de Aceitação
CA-001..CA-005; suíte total verde.

### 6.4 Casos de Borda (Edge Cases)
- Paciente muda restrição após pergunta de confirmação (descarta confirmação — regra atual).
- Slot ofertado fica indisponível entre oferta e confirmação.
- Remarcação sem consulta antiga identificada.
- TTL de estágio expirado (reset para IDLE) — preserva CO-07.

## 7. Riscos e Mitigações
| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Escopo passar de 15 tarefas | Média | Médio | Se exceder, dividir em 016 (núcleo) + 016b (ramos avançados) |
| Regressão sutil ao migrar `_handle_*` | Alta | Alto | Migrar um handler por vez, suíte verde a cada passo; testes de webhook como catraca |
| Mismatch de `status` quebrando testes de webhook | Média | Médio | Mapear `status` 1:1 ou migrar o teste conscientemente |

## 8. Dependências

### 8.1 Dependências Internas
- 014 (Gateway) e 015 (NLU) concluídas.
- Reusa `CalendarService`, `PatientService`, `ConversationStateService`, `HandoffService`.

### 8.2 Dependências Externas
- Nenhuma nova.

## 9. Observações e Decisões de Design
- A FSM é a **fronteira anti-regressão**: o LLM nunca mais decide agenda, só descreve (NLU) e
  conversa (tom). É isso que impede o "cérebro duplo" de ressurgir.
- Manter `ConversationState` (em vez de um novo modelo) reduz risco de migração de dados e mantém os
  testes de estado relevantes.
- Se a fase crescer demais, `016b - Orquestrador: Ramos Avançados` cobre remarcação parcial e
  consultas múltiplas, mantendo cada implementação ≤15 tarefas.

---

> **⚠️ NOTA:** Contrato vivo. Alterações de escopo refletidas aqui antes do código.
