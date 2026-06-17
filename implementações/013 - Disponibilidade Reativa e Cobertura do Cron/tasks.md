# Tarefas: Disponibilidade Reativa e Cobertura do Cron

> **Implementação:** 013 - Disponibilidade Reativa e Cobertura do Cron
> **Spec:** [spec.md](./spec.md)
> **Progresso:** 12/12 tarefas concluídas (100%)
> **Última atualização:** 2026-06-16

Legenda: [ ] Pendente, [x] Concluída, [!] Bloqueada, [-] Cancelada.

---

## Fase 1 — Preparação
### [x] T-001 — Mapear pontos de falha A/B/C
- **Descrição:** Confirmar file:line das 3 causas (feito na análise).
- **Critério:** Causas confirmadas. **Dependências:** — **Estimativa:** Pequena

## Fase 2 — Implementação
### [x] T-002 — (A) Vocabulário de recusa amplo
- **Descrição:** Ampliar `_REJECTION_TOKENS` ("nenhum", "nenhuma", "outro", "outra", "outros",
  "nao gostei", "mais opcoes", "mais horarios", "tem mais", "nenhum desses").
- **Arquivos:** `appointment_offer_service.py` **Dependências:** T-001 **Estimativa:** Pequena

### [x] T-003 — (B) Capturar horário e dia específicos
- **Descrição:** Adicionar `requested_time` e `requested_date` a `AppointmentRequestConstraints` e
  extraí-los em `extract_request_constraints` (horário avulso "11:00"/"as 18:30"; data "dia 23"/"23/06").
- **Arquivos:** `appointment_offer_service.py` **Dependências:** T-001 **Estimativa:** Média

### [x] T-004 — Núcleo de busca reutilizável
- **Descrição:** `CalendarService.find_next_available_slots(start_date, period, earliest_time,
  exclude_dates, exclude_slots, requested_time, limit, max_days)` retornando `{date_str, times}`.
- **Arquivos:** `calendar_service.py` **Dependências:** T-001 **Estimativa:** Média

### [x] T-005 — Persistir novas restrições no estado
- **Descrição:** `_capture_schedule_constraints` grava `requested_time`/`requested_date`; campos no
  `ConversationState`.
- **Arquivos:** `app.py`, `conversation_state_service.py` **Dependências:** T-003 **Estimativa:** Média

### [x] T-006 — Re-oferta determinística (A+B)
- **Descrição:** Em `_handle_offered_slot_selection`, substituir o beco sem saída por re-busca:
  ao recusar ou pedir horário/dia não ofertado, chamar `find_next_available_slots` com as restrições
  e ofertar; se nada, mensagem de "não encontrei nesse critério".
- **Arquivos:** `app.py` **Dependências:** T-002, T-004, T-005 **Estimativa:** Grande

### [x] T-007 — (C) Fallback de telefone por nome + logging no cron
- **Descrição:** `find_patient_appointments_for_date`: se evento sem telefone, tentar casar por nome
  único no cadastro (`PatientService`); logar eventos pulados.
- **Arquivos:** `calendar_service.py` **Dependências:** T-001 **Estimativa:** Média

## Fase 3 — Testes
### [x] T-008 — Testes A (recusa → nova oferta)
- **Arquivos:** `tests/test_reactive_availability_impl013.py` **Dependências:** T-006 **Estimativa:** Média
### [x] T-009 — Testes B (horário/dia específico)
- **Arquivos:** `tests/test_reactive_availability_impl013.py` **Dependências:** T-006 **Estimativa:** Média
### [x] T-010 — Testes núcleo de busca + cron (C)
- **Arquivos:** `tests/test_reactive_availability_impl013.py` **Dependências:** T-004, T-007 **Estimativa:** Média
### [x] T-011 — Suíte completa verde
- **Critério:** `pytest -q` sem falhas. **Dependências:** T-008..T-010 **Estimativa:** Pequena

## Fase 4 — Documentação
### [x] T-012 — Atualizar README e status
- **Arquivos:** `implementações/README.md`, `spec.md`, `tasks.md` **Dependências:** T-011 **Estimativa:** Pequena

---

## Registro de Progresso
| Tarefa | Descrição | Fase | Status | Concluída |
|---|---|---|---|---|
| T-001 | Mapear A/B/C | Preparação | [x] | 2026-06-16 |
| T-002 | Recusa ampla | Implementação | [x] | 2026-06-16 |
| T-003 | Horário/dia específico | Implementação | [x] | 2026-06-16 |
| T-004 | Núcleo de busca | Implementação | [x] | 2026-06-16 |
| T-005 | Persistir restrições | Implementação | [x] | 2026-06-16 |
| T-006 | Re-oferta determinística | Implementação | [x] | 2026-06-16 |
| T-007 | Cron fallback + log | Implementação | [x] | 2026-06-16 |
| T-008 | Testes A | Testes | [x] | 2026-06-16 |
| T-009 | Testes B | Testes | [x] | 2026-06-16 |
| T-010 | Testes núcleo + cron | Testes | [x] | 2026-06-16 |
| T-011 | Suíte verde | Testes | [x] | 2026-06-16 |
| T-012 | Documentação | Documentação | [x] | 2026-06-16 |
