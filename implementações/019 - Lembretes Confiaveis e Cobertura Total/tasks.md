# Tarefas: Lembretes Confiáveis e Cobertura Total

> **Implementação:** 019 - Lembretes Confiáveis e Cobertura Total
> **Spec:** [spec.md](./spec.md)
> **Progresso:** 13/13 tarefas concluídas (100%)
> **Última atualização:** 2026-06-23

---

## Legenda

- `[ ]` — Pendente · `[x]` — Concluída · `[!]` — Bloqueada · `[-]` — Cancelada

---

## Tarefas

### Fase 1: Preparação e Setup

- [x] **T-001:** Fixar baseline verde e mapear caminhos de descarte
  - **Critério de conclusão:** Suíte verde (562 após 018) e 5 pontos de descarte confirmados no código.
  - **Estimativa:** Pequena

- [x] **T-002:** Modelo de persistência da cobertura
  - **Descrição:** Criada a tabela aditiva `reminder_coverage` + índice e o `ReminderCoverageStore` (isolado). Decisão: tabela nova (não reuso de `appointment_confirmations`).
  - **Arquivos:** `src/infrastructure/persistence/connection.py`, `src/infrastructure/persistence/reminder_coverage_store.py`
  - **Estimativa:** Média

### Fase 2: Implementação Core

- [x] **T-003:** Registrar cada descarte com nome + motivo
  - **Descrição:** `_record_skip` acumula `skipped_details` em todos os pontos (sem telefone, dados inválidos, conversa em andamento, falha de envio, exceção). `stats["skipped_details"]` retornado.
  - **Arquivos:** `src/application/services/appointment_confirmation_service.py`
  - **Estimativa:** Grande

- [x] **T-004:** Telefone sem resolução vira pendência visível
  - **Descrição:** Consulta sem telefone resolvido é registrada como pulado observável (antes só `warning`); aparece no relatório e no `/admin`.
  - **Arquivos:** `appointment_confirmation_service.py`
  - **Estimativa:** Média

- [x] **T-005:** Relatório diário à clínica
  - **Descrição:** `_build_coverage_report` + `_send_coverage_report` enviam `enviados/pulados/falhas` + nome/motivo de cada não contatado ao `DOCTOR_PHONE`.
  - **Arquivos:** `appointment_confirmation_service.py`
  - **Estimativa:** Média

- [x] **T-006:** Re-tentativa de falhas — **ADAPTADA**
  - **Descrição:** Atendida pelo re-claim cross-run de `_try_claim_reminder_send` (status `failed` → reprocessado no ciclo seguinte) + resiliência de envio da impl 009 + falhas surfacadas no relatório para ação manual. Fila in-run dedicada não construída (over-engineering p/ cron diário). Ver `spec.md` §9.
  - **Arquivos:** `appointment_confirmation_service.py`
  - **Estimativa:** Grande

- [x] **T-007:** Relatório nunca falha em silêncio
  - **Descrição:** Falha ao enviar o relatório → `FailedAlertStore.record(reason="coverage_report_delivery_failed")` + `CRITICAL`.
  - **Arquivos:** `appointment_confirmation_service.py`
  - **Estimativa:** Pequena

- [x] **T-008:** Expor cobertura/pendentes no `/admin`
  - **Descrição:** `GET /admin/api/coverage` retorna enviados + pulados/falhas do dia (nome + motivo), via `ReminderCoverageStore`.
  - **Arquivos:** `src/interfaces/http/admin.py`
  - **Estimativa:** Média

- [x] **T-009:** Coerência com scheduler/catch-up
  - **Descrição:** Relatório + persistência ocorrem dentro de `send_next_day_confirmations`, então o catch-up (010) também os aciona; `record_misses` é idempotente por `run_date` (re-grava sem duplicar). Catch-up só roda se nada foi enviado, evitando relatório duplicado.
  - **Arquivos:** `appointment_confirmation_service.py`, `reminder_coverage_store.py`
  - **Estimativa:** Média

### Fase 3: Testes e Validação

- [x] **T-010:** Teste por caminho de descarte
  - **Descrição:** `TestReminderCoverage019`: sem telefone, dados inválidos, conversa em andamento, falha de envio — cada um registrado e observável.
  - **Arquivos:** `tests/test_appointment_confirmation_service.py`
  - **Estimativa:** Grande

- [x] **T-011:** Testes de relatório, resiliência e persistência
  - **Descrição:** Relatório com nomes/motivos; "todos contatados"; sem `DOCTOR_PHONE` não envia; falha do relatório persistida; cobertura persistida; endpoint `/admin/api/coverage`.
  - **Arquivos:** `tests/test_appointment_confirmation_service.py`, `tests/test_admin.py`
  - **Estimativa:** Média

- [x] **T-012:** Não-regressão (010/013) + suíte completa
  - **Critério de conclusão:** `pytest -q` = **562 passando, 0 falhas**.
  - **Estimativa:** Pequena

### Fase 4: Documentação e Finalização

- [x] **T-013:** Atualizar status e índice
  - **Descrição:** Spec → 🟢 Concluída; `README.md` (Fase 3) e memória de status atualizados.
  - **Estimativa:** Pequena

---

## Registro de Progresso

| Tarefa | Status | Data | Observações |
|--------|--------|------|-------------|
| T-001 | ✅ | 2026-06-23 | Baseline + mapa de descartes |
| T-002 | ✅ | 2026-06-23 | Tabela `reminder_coverage` + store |
| T-003 | ✅ | 2026-06-23 | `_record_skip` em todos os pontos |
| T-004 | ✅ | 2026-06-23 | Sem telefone = pendência visível |
| T-005 | ✅ | 2026-06-23 | Relatório diário |
| T-006 | ✅ (adaptada) | 2026-06-23 | Re-claim cross-run + relatório |
| T-007 | ✅ | 2026-06-23 | Relatório nunca silencioso |
| T-008 | ✅ | 2026-06-23 | `/admin/api/coverage` |
| T-009 | ✅ | 2026-06-23 | Catch-up/idempotência coerentes |
| T-010 | ✅ | 2026-06-23 | Teste por caminho de descarte |
| T-011 | ✅ | 2026-06-23 | Relatório/resiliência/persistência |
| T-012 | ✅ | 2026-06-23 | Suíte 562 verde |
| T-013 | ✅ | 2026-06-23 | Índice/spec/memória |

---

> **📌 NOTA:** Regra de ouro — nada concluído sem teste de regressão verde.
> Runner: `C:/Apps/WPP-DENTAL/.venv/Scripts/python.exe -m pytest -q`. Suíte: **562 passando**.
