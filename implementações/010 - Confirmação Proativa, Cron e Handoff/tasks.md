# Tarefas: Confirmação Proativa, Cron e Handoff

> **Implementação:** 010 - Confirmação Proativa, Cron e Handoff
> **Spec:** [spec.md](./spec.md)
> **Progresso:** 14/14 tarefas concluídas (100%)
> **Última atualização:** 2026-06-16

Legenda: [ ] Pendente, [x] Concluída, [!] Bloqueada, [-] Cancelada.

---

## Fase 1 — Preparação

### [x] T-001 — Mapear baseline e confirmar escopo
- **Descrição:** Leitura dos arquivos-fonte confirmando 9 bugs com file:line exatos.
- **Arquivos envolvidos:** `src/domain/policies/appointment_offer_service.py`, `src/application/services/handoff_service.py`, `src/application/services/appointment_confirmation_service.py`, `src/application/services/clean_agent_service.py`, `src/interfaces/http/app.py`
- **Critério de conclusão:** Lista de pontos exatos confirmada.
- **Dependências:** Nenhuma
- **Estimativa:** Pequena

---

## Fase 2 — Implementação

### [x] T-002 — (WE-08/CA-11) Word-boundary em `is_affirmative_confirmation`
- **Descrição:** Substituiu `token in normalized` por `re.search(r"\b" + re.escape(token) + r"\b", normalized)` para cada token afirmativo. Evita "assim" ativar "sim" e "okdoutora" ativar "ok".
- **Arquivos envolvidos:** `src/domain/policies/appointment_offer_service.py`
- **Critério de conclusão:** `is_affirmative_confirmation("assim")` retorna False.
- **Dependências:** T-001
- **Estimativa:** Pequena

### [x] T-003 — (WE-08/CA-11) `has_change_request()` + conflict detection
- **Descrição:** Adicionada classe `_CHANGE_REQUEST_TOKENS` e método `has_change_request()`. Em `_handle_appointment_confirmation`, branch remarcar usa `has_change_request(text)` em vez de inline tokens. Após `is_affirmative_confirmation`, se `has_change_request` também for True, bot pede esclarecimento.
- **Arquivos envolvidos:** `src/domain/policies/appointment_offer_service.py`, `src/interfaces/http/app.py`
- **Critério de conclusão:** `has_change_request("outro dia")` → True; conflito gera `status: ambiguous_confirmation_clarification`.
- **Dependências:** T-002
- **Estimativa:** Média

### [x] T-004 — (WE-13) Negation check em handoff auto-activation
- **Descrição:** Adicionada `_response_triggers_handoff(normalized_resp)` em `app.py` (módulo-level). Verifica 30 chars antes de cada marcador; prefixos de negação bloqueiam a ativação. Inline check substituído por chamada à função.
- **Arquivos envolvidos:** `src/interfaces/http/app.py`
- **Critério de conclusão:** "nao vou encaminhar" não ativa handoff.
- **Dependências:** T-001
- **Estimativa:** Pequena

### [x] T-005 — (HO-02) `HandoffService.extend()` + chamada no bloco ativo
- **Descrição:** Adicionado `MAX_WINDOW_MINUTES = 120` e método `extend(phone, duration_minutes=None)` ao `HandoffService`. Chamado no bloco `if HandoffService.is_active(phone)` em `app.py` antes de `ConversationService.add_message`.
- **Arquivos envolvidos:** `src/application/services/handoff_service.py`, `src/interfaces/http/app.py`
- **Critério de conclusão:** `extend()` retorna None para phone sem handoff; aumenta janela sem ultrapassar teto.
- **Dependências:** T-001
- **Estimativa:** Média

### [x] T-006 — (CO-04) `run_catchup_if_missed()` + chamada no startup
- **Descrição:** Adicionado método assíncrono `run_catchup_if_missed(now=None)` a `AppointmentConfirmationService`. Retorna None antes das 20h ou se já há sent/processing. Chamado no `lifespan` de `app.py` dentro do bloco `scheduler_enabled()`, antes de criar o task.
- **Arquivos envolvidos:** `src/application/services/appointment_confirmation_service.py`, `src/interfaces/http/app.py`
- **Critério de conclusão:** Catchup retorna None antes das 20h e None se já enviou; retorna stats se enviou agora.
- **Dependências:** T-001
- **Estimativa:** Média

### [x] T-007 — (CO-05) Recover `processing` + try/except por paciente
- **Descrição:** `_try_claim_reminder_send` agora aceita `status in ("failed", "processing")` (antes só "failed"). Loop de `send_next_day_confirmations` envolve cada paciente em try/except; `asyncio.CancelledError` é re-levantado; falha individual loga ERROR e continua.
- **Arquivos envolvidos:** `src/application/services/appointment_confirmation_service.py`
- **Critério de conclusão:** Exceção em um paciente não abortam os demais; status=processing é recuperado.
- **Dependências:** T-001
- **Estimativa:** Média

### [x] T-008 — (CO-07) Pular estado expirado sem chamar `clear`
- **Descrição:** Bloco "estado expirado" no loop de `send_next_day_confirmations` agora faz `stats["skipped_busy"] += 1; continue` em vez de `ConversationStateService.clear(phone)` + continuar enviando.
- **Arquivos envolvidos:** `src/application/services/appointment_confirmation_service.py`
- **Critério de conclusão:** `ConversationStateService.clear` não é chamado no path expirado.
- **Dependências:** T-001
- **Estimativa:** Pequena

### [x] T-009 — (CO-06) Dedup por `(phone, event_id)`
- **Descrição:** `_select_unique_appointments` substituiu `dict[phone]` por `dict[(phone, event_id)]`. Mesmo paciente com duas consultas distintas agora gera dois lembretes.
- **Arquivos envolvidos:** `src/application/services/appointment_confirmation_service.py`
- **Critério de conclusão:** Dois eventos do mesmo paciente retornam ambos.
- **Dependências:** T-001
- **Estimativa:** Pequena

### [x] T-010 — (AG-07) Loop guard com threshold de N ocorrências
- **Descrição:** `seen_calls: set` substituído por `seen_call_counts: dict[tuple, int]`. Constante `_LOOP_ABORT_THRESHOLD = 2` adicionada. Aborta quando `seen_call_counts[sig] > threshold` (3ª+ ocorrência).
- **Arquivos envolvidos:** `src/application/services/clean_agent_service.py`
- **Critério de conclusão:** Primeira e segunda ocorrências não abortam; terceira aborta.
- **Dependências:** T-001
- **Estimativa:** Pequena

### [x] T-011 — (AG-10) `_convert_history` reconhece prefixo `DENTISTA:`
- **Descrição:** Adicionado branch `elif line.startswith("DENTISTA:"):` que cria `HumanMessage(content="[DENTISTA] ...")`.
- **Arquivos envolvidos:** `src/application/services/clean_agent_service.py`
- **Critério de conclusão:** Linha `DENTISTA: texto` resulta em HumanMessage com `[DENTISTA] texto`.
- **Dependências:** T-001
- **Estimativa:** Pequena

---

## Fase 3 — Testes

### [x] T-012 — Testes unitários (44 testes)
- **Descrição:** 44 testes unitários em `tests/test_confirmation_cron_handoff_impl010.py` cobrindo T-002..T-011 (exceto CO-07/CO-04/CO-05 que são integração). Todos passando.
- **Arquivos envolvidos:** `tests/test_confirmation_cron_handoff_impl010.py`
- **Critério de conclusão:** 44/44 passando.
- **Dependências:** T-002..T-011
- **Estimativa:** Grande

### [x] T-013 — Testes de integração (5 testes)
- **Descrição:** 5 testes de integração cobrindo CO-07 (no-clear), CO-04 (catchup 3 casos), CO-05 (try/except por paciente). Todos passando. Total: 49 testes na classe.
- **Arquivos envolvidos:** `tests/test_confirmation_cron_handoff_impl010.py`
- **Critério de conclusão:** 49/49 passando; suíte completa 461/463.
- **Dependências:** T-006..T-009
- **Estimativa:** Grande

---

## Fase 4 — Documentação

### [x] T-014 — Atualizar documentação e status
- **Descrição:** Criados `spec.md` e `tasks.md`. Atualizado `implementações/README.md` (linha 010 → 🟢 Concluída).
- **Arquivos envolvidos:** `implementações/010 - Confirmação Proativa, Cron e Handoff/spec.md`, `implementações/README.md`
- **Critério de conclusão:** Documentação reflete o comportamento implementado.
- **Dependências:** T-012, T-013
- **Estimativa:** Pequena

---

## Registro de Progresso

| Tarefa | Descrição | Findings | Fase | Estimativa | Status | Concluída |
|---|---|---|---|---|---|---|
| T-001 | Mapear baseline | Todos | Preparação | Pequena | [x] | 2026-06-16 |
| T-002 | Word-boundary em is_affirmative | WE-08/CA-11 | Implementação | Pequena | [x] | 2026-06-16 |
| T-003 | has_change_request + conflict | WE-08/CA-11 | Implementação | Média | [x] | 2026-06-16 |
| T-004 | Negation check no handoff | WE-13 | Implementação | Pequena | [x] | 2026-06-16 |
| T-005 | HandoffService.extend | HO-02 | Implementação | Média | [x] | 2026-06-16 |
| T-006 | run_catchup_if_missed | CO-04 | Implementação | Média | [x] | 2026-06-16 |
| T-007 | Recover processing + try/except | CO-05 | Implementação | Média | [x] | 2026-06-16 |
| T-008 | Pular sem clear no expirado | CO-07 | Implementação | Pequena | [x] | 2026-06-16 |
| T-009 | Dedup por (phone, event_id) | CO-06 | Implementação | Pequena | [x] | 2026-06-16 |
| T-010 | Loop guard por threshold | AG-07 | Implementação | Pequena | [x] | 2026-06-16 |
| T-011 | DENTISTA: em _convert_history | AG-10 | Implementação | Pequena | [x] | 2026-06-16 |
| T-012 | Testes unitários (44 testes) | Todos | Testes | Grande | [x] | 2026-06-16 |
| T-013 | Testes integração (5 testes) | CO-04/05/07 | Testes | Grande | [x] | 2026-06-16 |
| T-014 | Documentação | — | Documentação | Pequena | [x] | 2026-06-16 |

> Total: 14 tarefas concluídas.
