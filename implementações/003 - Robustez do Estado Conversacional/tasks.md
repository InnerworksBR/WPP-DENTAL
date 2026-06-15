# Tarefas: Robustez do Estado Conversacional

> **Implementação:** 003 - Robustez do Estado Conversacional
> **Spec:** [spec.md](./spec.md)
> **Progresso:** 0/13 tarefas concluídas (0%)
> **Última atualização:** 2026-06-15

Legenda: [ ] Pendente, [x] Concluída, [!] Bloqueada, [-] Cancelada.

---

## Fase 1 — Preparação

### [ ] T-001 — Mapear call sites de estado e baseline de comportamento
- **Descrição:** Inventariar todos os pontos que leem/gravam estado e que setam `stage="idle"` ou `awaiting_*`, confirmando arquivo:linha (`app.py:215-250`, `616`, `866`, `987`, `1192`, `1243`, `861-863`; `conversation_state_service.py:60-64`; `handoff_service.py:30-43`). Registrar o comportamento atual de cada um para detectar regressão.
- **Arquivos envolvidos:** `src/interfaces/http/app.py`, `src/application/services/conversation_state_service.py`, `src/application/services/handoff_service.py`.
- **Critério de conclusão:** Lista revisada de call sites com arquivo:linha e nota de comportamento esperado, anexada ao PR/branch.
- **Dependências:** Implementações 001 e 002 concluídas.
- **Estimativa:** Pequena.

### [ ] T-002 — Definir TTL e contrato dos helpers
- **Descrição:** Definir o valor do TTL para stages `awaiting_*` (proposto 60 min), a convenção de borda (`>` expira / `==` não — EC-03) e as assinaturas de `reset_to_idle(state, keep=None)` e do filtro de campos baseado em `dataclasses.fields`.
- **Arquivos envolvidos:** `spec.md` (seções 3.3/3.5), `src/interfaces/http/app.py`.
- **Critério de conclusão:** Decisões registradas na spec/PR; constantes nomeadas definidas (ex.: `AWAITING_STATE_TTL_MINUTES`).
- **Dependências:** T-001.
- **Estimativa:** Pequena.

---

## Fase 2 — Implementação

### [ ] T-003 — CO-01: filtrar payload por campos válidos da dataclass
- **Descrição:** Em `ConversationStateService.get()`, calcular `valid = {f.name for f in dataclasses.fields(ConversationState)}` e filtrar `payload` antes de `ConversationState(**payload)` (`conversation_state_service.py:64`). Adicionar `try/except TypeError` como rede secundária com `logger.warning` retornando `ConversationState()`. Logar chaves descartadas.
- **Arquivos envolvidos:** `src/application/services/conversation_state_service.py`.
- **Critério de conclusão:** `get()` lê payload com chave desconhecida sem lançar `TypeError` (atende RF-001, CA-001/CA-002).
- **Dependências:** T-002.
- **Estimativa:** Pequena.

### [ ] T-004 — CO-02: sanear listas e strings na leitura
- **Descrição:** Em `get()`, coagir `offered_times`, `rejected_slots`, `excluded_dates` para `list[str]` (descartando itens não-string; `null`/tipo errado → `[]`) e converter campos `str` `None`→`""`, mantendo o saneamento de `metadata` existente (`conversation_state_service.py:60-62`).
- **Arquivos envolvidos:** `src/application/services/conversation_state_service.py`.
- **Critério de conclusão:** Listas sempre `list[str]` e strings nunca `None` (atende RF-002, CA-003/CA-004).
- **Dependências:** T-003.
- **Estimativa:** Pequena.

### [ ] T-005 — CO-08: criar helper `reset_to_idle` e aplicar nos call sites
- **Descrição:** Criar `reset_to_idle(state, keep=None)` que zera `intent`, `pending_event_id/label`, `reschedule_event_id/label`, `pending_slot_date/time`, `offered_date`, `offered_times`, `rejected_slots`, `excluded_dates`, `requested_*`, `earliest_time`, preserva `patient_name`/`plan_name`/`metadata` e seta `stage="idle"`. Aplicar nos call sites `app.py:866`, `987`, `1192`, `1243`. **NÃO** aplicar em `_preserve_partial_reschedule_state` (616).
- **Arquivos envolvidos:** `src/interfaces/http/app.py`.
- **Critério de conclusão:** Transições a `idle` (exceto reschedule parcial) não deixam resíduos (atende RF-005, CA-009).
- **Dependências:** T-002.
- **Estimativa:** Média.

### [ ] T-006 — CO-03: implementar handler `_handle_pending_slot_name`
- **Descrição:** Criar `_handle_pending_slot_name(phone, text, contact_name, message_id)` espelhando `_handle_pending_slot_plan` (`app.py:828-892`): se faltar `pending_slot_date/time` → `clear` e retorna `None`; valida o nome (rejeitar dígitos/telefone, ref. `app.py:855-859`/`923`); faz `PatientService.upsert(phone, nome, state.plan_name)`; aplica `reset_to_idle`; envia `_build_slot_confirmation_request_message` (`app.py:647`); trata falha de entrega com `_mark_message_failed` + `HTTPException(502)`.
- **Arquivos envolvidos:** `src/interfaces/http/app.py`.
- **Critério de conclusão:** Paciente em `awaiting_name_for_slot_confirmation` consegue concluir o agendamento (atende RF-003, CA-005/CA-006).
- **Dependências:** T-005.
- **Estimativa:** Média.

### [ ] T-007 — CO-03: rotear novo stage no dispatcher do webhook
- **Descrição:** No dispatcher (`app.py:215-233`), adicionar ramo para `current_state.stage == "awaiting_name_for_slot_confirmation"` chamando `_handle_pending_slot_name` e retornando se não-`None`, junto ao bloco de `awaiting_plan_for_slot_confirmation`.
- **Arquivos envolvidos:** `src/interfaces/http/app.py`.
- **Critério de conclusão:** Stage roteado corretamente; CA-005 passa fim-a-fim.
- **Dependências:** T-006.
- **Estimativa:** Pequena.

### [ ] T-008 — CO-07: TTL para stages `awaiting_*` no dispatcher
- **Descrição:** Após `current_state = ConversationStateService.get(phone)` (`app.py:204`), se `stage` ∈ {`awaiting_plan_for_slot_confirmation`, `awaiting_name_for_slot_confirmation`, `CONFIRMATION_STAGE`}, comparar `get_updated_at(phone)` com `utcnow()`; se exceder o TTL, `clear(phone)` e recarregar `current_state`. Logar expiração (RNF-003). Reusar padrão de `appointment_confirmation_service.py:283-303`.
- **Arquivos envolvidos:** `src/interfaces/http/app.py`, `src/application/services/conversation_state_service.py` (apenas leitura de `get_updated_at`).
- **Critério de conclusão:** Estado `awaiting_*` antigo é limpo antes do roteamento; recente continua tratado (atende RF-004, CA-007/CA-008).
- **Dependências:** T-007.
- **Estimativa:** Média.

### [ ] T-009 — HO-01: preservar contexto de agenda em `HandoffService.activate`
- **Descrição:** Em `activate` (`handoff_service.py:30-43`), carregar o estado atual via `ConversationStateService.get(phone)` e copiar `pending_slot_date/time`, `intent`, `reschedule_event_id/label`, `pending_event_id/label` para o novo `ConversationState(stage=STAGE, metadata={...})` antes do `save`, sem destruir o contexto de agendamento.
- **Arquivos envolvidos:** `src/application/services/handoff_service.py`.
- **Critério de conclusão:** Handoff preserva campos de agenda (atende RF-006, CA-010).
- **Dependências:** T-004.
- **Estimativa:** Média.

---

## Fase 3 — Testes

### [ ] T-010 — Testes de regressão CO-01/CO-02 (leitura robusta)
- **Descrição:** UT-01..UT-04 e IT-03: payload com chave legada não lança; `try/except` retorna estado vazio com log; matriz de listas inválidas saneada; strings `null`→`""`; e2e com chave legada injetada no banco de teste sem `HTTPException 500`.
- **Arquivos envolvidos:** `tests/` (novo: `test_conversation_state_service.py`), `src/application/services/conversation_state_service.py`.
- **Critério de conclusão:** Testes verdes cobrindo CA-001..CA-004; EC-01/EC-02 incluídos.
- **Dependências:** T-003, T-004.
- **Estimativa:** Média.

### [ ] T-011 — Testes de regressão CO-03/CO-07 (handler + TTL)
- **Descrição:** IT-01/IT-02 + EC-04: webhook em `awaiting_name_for_slot_confirmation` conclui agendamento; sem slot pendente segue sem travar; estado `awaiting_*` antigo é limpo (manipular `updated_at`); nome só com dígitos é rejeitado. Mockar `WhatsAppService`/`CalendarService`/`PatientService`.
- **Arquivos envolvidos:** `tests/` (novo: `test_webhook_state_flows.py`), `src/interfaces/http/app.py`.
- **Critério de conclusão:** Testes verdes cobrindo CA-005..CA-008.
- **Dependências:** T-007, T-008.
- **Estimativa:** Média.

### [ ] T-012 — Testes de regressão CO-08/HO-01 (reset + handoff)
- **Descrição:** UT-05/UT-06, IT-04, EC-05/EC-06: `reset_to_idle` zera satélites e preserva identidade; reschedule parcial intocado; `activate` preserva campos de agenda e seta `metadata[handoff_until_utc]`; handoff sem contexto fica limpo; CA-011 garante que fluxos existentes (`awaiting_plan`, `CONFIRMATION_STAGE`, `awaiting_cancel_confirmation`) não regridem.
- **Arquivos envolvidos:** `tests/` (novo: `test_reset_and_handoff.py`), `src/interfaces/http/app.py`, `src/application/services/handoff_service.py`.
- **Critério de conclusão:** Testes verdes cobrindo CA-009..CA-011.
- **Dependências:** T-005, T-009.
- **Estimativa:** Média.

---

## Fase 4 — Documentação

### [ ] T-013 — Atualizar spec, progresso e changelog
- **Descrição:** Marcar checkboxes de CA atendidos na spec, atualizar o cabeçalho de progresso deste arquivo, registrar a constante de TTL final escolhida e anotar a exceção de design do `_preserve_partial_reschedule_state` (816→616) no changelog/commit.
- **Arquivos envolvidos:** `spec.md`, `tasks.md`, mensagem de commit.
- **Critério de conclusão:** Documentação coerente com o código entregue; progresso = 13/13.
- **Dependências:** T-010, T-011, T-012.
- **Estimativa:** Pequena.

---

## Registro de Progresso

| Tarefa | Fase | Finding(s) | Status | Estimativa |
|---|---|---|---|---|
| T-001 | Preparação | Todos | [ ] | Pequena |
| T-002 | Preparação | CO-07/CO-08 | [ ] | Pequena |
| T-003 | Implementação | CO-01 | [ ] | Pequena |
| T-004 | Implementação | CO-02 | [ ] | Pequena |
| T-005 | Implementação | CO-08 | [ ] | Média |
| T-006 | Implementação | CO-03 | [ ] | Média |
| T-007 | Implementação | CO-03 | [ ] | Pequena |
| T-008 | Implementação | CO-07 | [ ] | Média |
| T-009 | Implementação | HO-01 | [ ] | Média |
| T-010 | Testes | CO-01/CO-02 | [ ] | Média |
| T-011 | Testes | CO-03/CO-07 | [ ] | Média |
| T-012 | Testes | CO-08/HO-01 | [ ] | Média |
| T-013 | Documentação | Todos | [ ] | Pequena |
