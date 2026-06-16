# Tarefas: Identidade do Paciente e Normalizacao de Telefone

> **Implementação:** 004 - Identidade do Paciente e Normalizacao de Telefone
> **Spec:** [spec.md](./spec.md)
> **Progresso:** 12/12 tarefas concluídas (100%)
> **Última atualização:** 2026-06-15

Legenda: [ ] Pendente, [x] Concluída, [!] Bloqueada, [-] Cancelada.

---

## Fase 1 — Preparação

### [x] T-001 — Mapear e fixar contratos de telefone
- 6 consumidores confirmados: patient_service.py, patient_tool.py, calendar_service.py, connection.py, outbound_message_store.py, app.py.
- Novas funções: canonical_phone, is_valid_phone, phones_match.
- Assinaturas de normalize_internal_phone/build_phone_search_term preservadas (RNF-002).

---

## Fase 2 — Implementação

### [x] T-002 — Implementar forma canônica do telefone BR (PH-01)
- phone_service.py: canonical_phone(value) → DDD(2) + 8 dígitos; reconcilia 9o dígito; idempotente.
- phones_match(a, b) → canonical_phone(a) == canonical_phone(b).

### [x] T-003 — Validação de telefone e fim do prefixo "55" cego (PH-04, PH-05)
- phone_service.py: is_valid_phone(value) rejeita @g.us, @lid, números curtos.
- normalize_conversation_phone: prefixo "55" só aplicado se is_valid_phone(digits).

### [x] T-004 — Match exato no PatientService (PH-02)
- patient_service.py: find_by_phone usa WHERE phone = ? com canonical_phone; fallback em memória por phones_match.

### [x] T-005 — Upsert não-destrutivo no PatientService (PA-01)
- patient_service.py: upsert não sobrescreve nome válido por vazio/placeholder (len < 3 ou só dígitos); não zera plano quando plan=None.

### [x] T-006 — Match exato e merge não-destrutivo nas tools (PH-02, PA-02)
- patient_tool.py: FindPatientTool, SavePatientTool, SaveInteractionTool reutilizam PatientService.

### [x] T-007 — Corrigir casamento de eventos no Calendar (PH-03)
- calendar_service.py: find_appointments_by_phone usa phones_match em vez de endswith cruzado.

### [x] T-008 — Reconciliação de legados pelo canônico
- connection.py: _normalize_patient_phone_rows agrupa por canonical_phone; mescla nome/plano ao unir duplicatas.

---

## Fase 3 — Testes

### [x] T-009 — Testes de regressão do domínio (PH-01, PH-04, PH-05)
- tests/test_phone_service.py: 17 testes — canonical_phone (3 variações = mesma chave, idempotência), is_valid_phone, phones_match, normalize_conversation_phone.

### [x] T-010 — Testes de regressão de paciente (PH-02, PA-01, PA-02)
- tests/test_patient_identity.py: 13 testes — busca exata sem colisão; variantes 9o dígito; upsert merge de nome; SavePatientTool preserva plano e nome.

### [x] T-011 — Testes de regressão do Calendar e remarcação (PH-03, PH-01)
- tests/test_calendar_phone_match.py: 9 testes — pacientes distintos não casam, variantes do mesmo número casam, idempotência canônica.

---

## Fase 4 — Documentação

### [x] T-012 — Atualizar docs e registro de progresso
- spec.md: status → Concluída. tasks.md: 12/12. README.md: linha 004 → Concluída.
- Testes existentes atualizados para formato canônico (10 dígitos, 9o removido).

---

## Registro de Progresso

| Tarefa | Fase | Descrição curta | Findings | Estimativa | Status |
|---|---|---|---|---|---|
| T-001 | Preparação | Mapear contratos de telefone | — | Pequena | [x] |
| T-002 | Implementação | Forma canônica BR + phones_match | PH-01 | Média | [x] |
| T-003 | Implementação | is_valid_phone + fim do "55" cego | PH-04, PH-05 | Média | [x] |
| T-004 | Implementação | Match exato no PatientService | PH-02 | Média | [x] |
| T-005 | Implementação | Upsert não-destrutivo | PA-01 | Média | [x] |
| T-006 | Implementação | Match exato + merge nas tools | PH-02, PA-02 | Média | [x] |
| T-007 | Implementação | Casamento de eventos no Calendar | PH-03 | Média | [x] |
| T-008 | Implementação | Reconciliação de legados | PH-01 | Média | [x] |
| T-009 | Testes | Regressão domínio | PH-01, PH-04, PH-05 | Média | [x] |
| T-010 | Testes | Regressão paciente | PH-02, PA-01, PA-02 | Média | [x] |
| T-011 | Testes | Regressão Calendar/remarcação | PH-03, PH-01 | Média | [x] |
| T-012 | Documentação | Docs + registro de progresso | — | Pequena | [x] |

---

## Resultado da Execução (2026-06-15)

Branch: fix/004-identidade-paciente

**Linha de base:** 171 passed (impl 003)
**Resultado:** 221 passed, 0 failed, 0 warnings

**Arquivos de produção modificados:**
- src/domain/policies/phone_service.py — canonical_phone, is_valid_phone, phones_match, normalize_conversation_phone
- src/domain/policies/__init__.py — novos símbolos exportados
- src/application/services/patient_service.py — match exato + upsert não-destrutivo
- src/interfaces/tools/patient_tool.py — reutiliza PatientService
- src/infrastructure/integrations/calendar_service.py — phones_match em vez de endswith
- src/infrastructure/persistence/connection.py — _normalize_patient_phone_rows por canonical_phone

**Novos testes (39 verdes):**
- tests/test_phone_service.py (17): PH-01/04/05
- tests/test_patient_identity.py (13): PH-02/PA-01/PA-02
- tests/test_calendar_phone_match.py (9): PH-03/PH-01

**CA verificados:** CA-001..CA-011 todos verdes.
