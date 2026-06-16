# Confirmação Proativa, Cron e Handoff

> **ID:** 010
> **Status:** 🟢 Concluída
> **Prioridade:** 🟡 Média
> **Criada em:** 2026-06-16
> **Última atualização:** 2026-06-16 (concluída)
> **Autor:** Agente AI

---

## 1. Resumo Executivo

Corrige heurísticas de confirmação/handoff, cron de lembretes e janela de handoff. A soma dos 9 bugs tornava: confirmações equivocadas ("assim" disparava "sim"), handoff ativado por negação, cron destruindo conversas ativas, deduplica errada de consultas e loop de IA abortando prematuramente.

## 2. Contexto e Motivação

### 2.1 Problema Atual

Nove bugs coexistiam:

- **WE-08/CA-11:** `is_affirmative_confirmation` usa substring — "assim" ativa "sim", "okdoutora" ativa "ok".
- **WE-13:** Handoff auto-ativação não verifica negação — "nao vou encaminhar" ainda ativa handoff.
- **HO-02:** Paciente que envia mensagem durante handoff não estende a janela, deixando-a expirar antes.
- **CO-04:** Cron sem catch-up: reinício após as 20h perde o disparo daquele dia.
- **CO-05:** `_try_claim_reminder_send` só recupera `failed`, não `processing`; sem try/except por paciente.
- **CO-06:** `_select_unique_appointments` deduplica por phone apenas — segunda consulta do mesmo paciente descartada.
- **CO-07:** `send_next_day_confirmations` chama `ConversationStateService.clear` em estado "expirado", destruindo conversa em andamento.
- **AG-07:** `seen_calls` aborta na 1ª repetição — LLM legítimo que repete uma busca com mesmos args cai no guard cedo demais.
- **AG-10:** `_convert_history` descarta linhas `DENTISTA:`, perdendo contexto de intervenção manual.

### 2.2 Impacto

- Paciente diz "Pode ser assim!" → sistema confirma consulta incorretamente
- Doutora diz "Não vou encaminhar" na resposta → handoff ativado indevidamente
- Reinício pós-manutenção → pacientes não recebem lembrete daquele dia
- Paciente com duas consultas → segunda nunca recebe lembrete
- Conversa em andamento apagada pelo cron → confusão no atendimento

### 2.3 Soluções

| Solução | Prós | Contras | Decisão |
|---------|------|---------|---------|
| Word-boundary regex para afirmação | Elimina falsos positivos de substring | Mínimo overhead | ✅ |
| has_change_request() + remarcar branch | Detecta conflitos e redireciona para remarcar | Pode reclassificar mensagens ambíguas | ✅ |
| Negation window para handoff | Checa 30 chars antes do marcador | Edge cases de negação longa distância | ✅ |
| HandoffService.extend() | Estende janela sem ultrapassar teto | — | ✅ |
| run_catchup_if_missed() no startup | Catch-up simples e atômico | Só funciona se o processo reinicia antes do próximo dia | ✅ |
| try/except por paciente + reopen processing | Isolamento de falhas | — | ✅ |
| Dedup por (phone, event_id) | Preserva múltiplas consultas por paciente | — | ✅ |
| Skip em vez de clear para estado expirado | Não destrói conversa ativa | Paciente pode perder lembrete | ✅ |
| Counter dict para seen_calls | Tolera N repetições antes de abortar | — | ✅ |
| DENTISTA: prefix em _convert_history | Inclui intervenção manual como contexto | — | ✅ |

## 3. Especificação Técnica

### 3.1 Componentes Afetados

| Componente | Tipo | Ação |
|-----------|------|------|
| `src/domain/policies/appointment_offer_service.py` | Arquivo | Modificar — word-boundary, has_change_request |
| `src/application/services/handoff_service.py` | Arquivo | Modificar — extend(), MAX_WINDOW_MINUTES |
| `src/application/services/appointment_confirmation_service.py` | Arquivo | Modificar — dedup, try/except, catchup, no-clear |
| `src/application/services/clean_agent_service.py` | Arquivo | Modificar — loop threshold, DENTISTA prefix |
| `src/interfaces/http/app.py` | Arquivo | Modificar — negation check, extend, has_change_request, catchup |

### 3.2 Fluxo de Execução

**WE-08 — word-boundary:** `is_affirmative_confirmation` usa `re.search(r"\b" + re.escape(token) + r"\b", normalized)` para cada token da lista afirmativa.

**WE-08/T-003 — has_change_request + conflict detection:** `has_change_request()` retorna True para tokens de mudança. Em `_handle_appointment_confirmation`, o branch remarcar usa `has_change_request(text)` em vez de inline tokens. Após `is_affirmative_confirmation`, se `has_change_request` também for True, pede esclarecimento.

**WE-13 — negation check:** `_response_triggers_handoff(normalized_resp)` verifica 30 chars antes de cada marcador; se há negação nessa janela, o marcador não conta.

**HO-02 — extend:** `HandoffService.extend(phone)` chamado na entrada do bloco `if HandoffService.is_active(phone)`. `extend()` recalcula `now + WINDOW_MINUTES`, respeita teto `MAX_WINDOW_MINUTES = 120` e nunca reduz janela existente.

**CO-04 — catchup:** `run_catchup_if_missed()` verifica se hora atual ≥ 20h e se não há registros `sent/processing` para amanhã. Se não há, executa `send_next_day_confirmations`. Chamado em `lifespan` antes do scheduler task.

**CO-05 — try/except + processing:** `_try_claim_reminder_send` recupera `status in ("failed", "processing")`. O loop de `send_next_day_confirmations` envolve cada paciente em try/except; `asyncio.CancelledError` é re-levantado.

**CO-06 — dedup:** `_select_unique_appointments` usa `dict[(phone, event_id)]` em vez de `dict[phone]`.

**CO-07 — no clear:** Bloco "estado expirado" agora faz `stats["skipped_busy"] += 1; continue` em vez de `clear + send`.

**AG-07 — threshold:** `seen_calls: set` substituído por `seen_call_counts: dict[tuple, int]`. Aborta quando `seen_call_counts[sig] > _LOOP_ABORT_THRESHOLD (= 2)`.

**AG-10 — DENTISTA:** `_convert_history` reconhece linhas `DENTISTA:` e as adiciona como `HumanMessage(content="[DENTISTA] ...")`.

### 3.3 Tratamento de Erros

- `run_catchup_if_missed` envolto em try/except no lifespan; falha logada como ERROR sem bloquear startup.
- Cada paciente no cron envolto em try/except; `_mark_reminder_failed` chamado e loop continua.
- `HandoffService.extend` retorna None silenciosamente se handoff não ativo.

## 4. Requisitos

### 4.1 Requisitos Funcionais

- **RF-001:** `is_affirmative_confirmation("assim")` retorna False; `is_affirmative_confirmation("sim")` retorna True
- **RF-002:** `has_change_request("outro dia")` retorna True; `has_change_request("confirmo")` retorna False
- **RF-003:** `_response_triggers_handoff` retorna False para "nao vou encaminhar para ninguem"
- **RF-004:** `HandoffService.extend` aumenta janela respeitando `MAX_WINDOW_MINUTES = 120`
- **RF-005:** `run_catchup_if_missed` retorna None antes das 20h ou se já há registros sent/processing
- **RF-006:** `_try_claim_reminder_send` recupera linhas com status `processing`
- **RF-007:** Exceção em um paciente do cron não aborta os demais
- **RF-008:** `_select_unique_appointments` mantém duas consultas distintas do mesmo paciente
- **RF-009:** Bloco de estado expirado no cron apenas pula sem chamar `clear`
- **RF-010:** Loop aborta apenas na 3ª repetição (não na 1ª) de mesma chamada+args
- **RF-011:** Linhas `DENTISTA:` do histórico aparecem como HumanMessage em `_convert_history`

### 4.2 Requisitos Não-Funcionais

- **RNF-001:** Nenhum teste existente deve ser quebrado
- **RNF-002:** `extend()` não deve reduzir uma janela maior que a calculada

## 5. Critérios de Aceitação

- [x] **CA-001:** `is_affirmative_confirmation("assim")` retorna False
- [x] **CA-002:** `is_affirmative_confirmation("sim")` retorna True
- [x] **CA-003:** `_response_triggers_handoff("nao vou encaminhar")` retorna False
- [x] **CA-004:** `HandoffService.extend` retorna None para phone sem handoff ativo
- [x] **CA-005:** `_select_unique_appointments` com dois eventos do mesmo paciente retorna 2
- [x] **CA-006:** `_try_claim_reminder_send` com status=processing retorna True
- [x] **CA-007:** `send_next_day_confirmations` com exceção no 1º paciente ainda envia o 2º
- [x] **CA-008:** estado expirado no cron resulta em `skipped_busy`, sem `clear()` chamado
- [x] **CA-009:** `run_catchup_if_missed` antes das 20h retorna None
- [x] **CA-010:** `run_catchup_if_missed` com registros sent retorna None
- [x] **CA-011:** `run_catchup_if_missed` após 20h sem registros retorna stats com `sent ≥ 1`
- [x] **CA-012:** `_LOOP_ABORT_THRESHOLD = 2`; aborta na 3ª ocorrência
- [x] **CA-013:** `_convert_history("DENTISTA: texto")` retorna `[HumanMessage("[DENTISTA] texto")]`

## 6. Plano de Testes

### 6.1 Testes Unitários (T-012)

- WE-08: `TestAffirmativeConfirmationWordBoundary` (10 casos)
- WE-08: `TestHasChangeRequest` (8 casos)
- WE-13: `TestResponseTriggersHandoff` (8 casos)
- HO-02: `TestHandoffServiceExtend` (4 casos)
- CO-06: `TestSelectUniqueAppointmentsByPhoneEvent` (5 casos)
- CO-05: `TestTryClaimReminderSendRecovery` (2 casos)
- AG-10: `TestConvertHistoryDentistaPrefix` (5 casos)
- AG-07: `TestLoopAbortThreshold` (2 casos)

### 6.2 Testes de Integração (T-013)

- CO-07: `TestCO07NoClearOnExpiredState` (1 caso)
- CO-04: `TestCO04CatchupIfMissed` (3 casos)
- CO-05: `TestCO05TryExceptPerAppointment` (1 caso)

## 7. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|-------|--------------|---------|-----------|
| has_change_request muito broad | Baixa | Médio | Tokens específicos; "diferente" excluído |
| Catchup dispara dobrado em race condition | Baixa | Baixo | INSERT OR IGNORE + check de sent_count |
| extend não detectar edge case de clock drift | Muito baixa | Baixo | ceiling relativo a now |

## 8. Dependências

### 8.1 Dependências Internas

- Impl. 002 (suíte de testes)
- Impl. 003 (estado conversacional)
- Impl. 004 (telefone)
- Impl. 005 (cancelamento)

### 8.2 Dependências Externas

- `asyncio` (padrão)
- `re` (padrão)

---

> **⚠️ NOTA:** Este documento é a fonte de verdade para esta implementação.
