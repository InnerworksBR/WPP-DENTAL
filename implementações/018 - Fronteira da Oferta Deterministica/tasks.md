# Tarefas: Fronteira da Oferta Determinística

> **Implementação:** 018 - Fronteira da Oferta Determinística
> **Spec:** [spec.md](./spec.md)
> **Progresso:** 10/10 tarefas do escopo concluídas (100%) · 3 tarefas deferidas para o follow-up RF-003-B
> **Última atualização:** 2026-06-23

---

## Legenda

- `[ ]` — Pendente
- `[x]` — Concluída
- `[!]` — Bloqueada (ver observação)
- `[-]` — Cancelada / deferida para follow-up

---

## Tarefas

### Fase 1: Preparação e Setup

- [x] **T-001:** Criar branch e fixar baseline verde
  - **Descrição:** Branch `fix/fase-3-correcoes-018-019` a partir de `main`. Suíte rodada.
  - **Critério de conclusão:** Baseline registrado: **538 passando + 4 date bombs pré-existentes** (datas fixas `23/06/2026` em `test_reschedule_atomic.py`, sem relação com 018).
  - **Dependências:** Nenhuma
  - **Estimativa:** Pequena
  - **Observações:** As 4 falhas pré-existentes foram saneadas (clock congelado) em T-001b.

- [x] **T-001b:** Sanear date bombs pré-existentes (higiene da catraca)
  - **Descrição:** `TestCreateAppointmentIdempotency` usava `datetime(2026,6,23,...)` (hoje) e quebrava na validação "horário no passado". Congelado o `now` do `calendar_service` em `_patch_calendar` para `22/06/2026` (determinístico, independente do relógio).
  - **Arquivos envolvidos:** `tests/test_reschedule_atomic.py`
  - **Critério de conclusão:** As 4 falhas somem; catraca volta a 100% verde. **Fora da lógica da 018** (higiene de teste).
  - **Dependências:** T-001
  - **Estimativa:** Pequena

- [x] **T-002:** Testes-alvo de regressão (a) e (b)
  - **Descrição:** Escritos os testes de `try_initial_offer` em `tests/test_orchestrator.py` (geração estruturada, mensagem == estado, não-repetição, deferimento de saudação/remarcar/erro).
  - **Arquivos envolvidos:** `tests/test_orchestrator.py`
  - **Critério de conclusão:** Testes guiando o TDD; verdes ao fim.
  - **Dependências:** T-001
  - **Estimativa:** Média

### Fase 2: Implementação Core

- [x] **T-003:** Implementar `try_initial_offer` no orquestrador
  - **Descrição:** Método que gera a oferta inicial a partir de `find_next_available_slots`, grava `offered_date`/`offered_times` e monta a mensagem por template (idêntica ao estado). Guardas: só IDLE, sem oferta/confirmação ativa, sem oferta recente no histórico, intenção `AGENDAR`.
  - **Arquivos envolvidos:** `src/application/flow/orchestrator.py`
  - **Critério de conclusão:** Retorna `OrchestratorResult` estruturado; testes da T-002 passam.
  - **Dependências:** T-002
  - **Estimativa:** Grande

- [x] **T-004:** Encadear `try_initial_offer` na ordem correta
  - **Descrição:** Inserido após cancelamento e antes do fallback ao LLM; saudação/dúvida aberta/remarcar/cancelar deferem (`handled=False`).
  - **Arquivos envolvidos:** `src/interfaces/http/app.py`
  - **Critério de conclusão:** Pedido de agendamento → FSM; saudação → LLM.
  - **Dependências:** T-003
  - **Estimativa:** Média

- [x] **T-005:** Rotear a oferta inicial no `app.py` pelo orquestrador
  - **Descrição:** Webhook chama `orchestrator.try_initial_offer` e responde via `_respond_orchestrator`; status legado `initial_offer`/`initial_offer_none` → `processed` em `_LEGACY_ORCH_STATUS`.
  - **Arquivos envolvidos:** `src/interfaces/http/app.py`
  - **Critério de conclusão:** A oferta inicial, no caminho feliz, não passa mais pelo loop de tools do LLM.
  - **Dependências:** T-004
  - **Estimativa:** Grande

- [-] **T-006:** Remover `_parse_offered_slots` e `_is_offered_slot` — **DEFERIDA → RF-003-B**
  - **Descrição:** Mantidos como fallback de borda. A remoção total muda comportamento em 5 arquivos de teste e degrada conversa fuzzy (`SAUDACAO`/`AMBIGUO`); por isso é follow-up após validação em produção. Ver `spec.md` §9.
  - **Arquivos envolvidos:** `src/application/services/clean_agent_service.py`
  - **Dependências:** observação de produção do caminho da FSM
  - **Estimativa:** Média

- [-] **T-007:** Neutralizar as tools de oferta do LLM — **DEFERIDA → RF-003-B**
  - **Descrição:** Idem T-006: as tools de oferta seguem como fallback até a FSM ser validada em produção.
  - **Arquivos envolvidos:** `src/application/services/clean_agent_service.py`
  - **Dependências:** T-006
  - **Estimativa:** Média

- [x] **T-008:** Resposta determinística para "sem horário"
  - **Descrição:** `find_next_available_slots` → `None` responde honestamente (status `initial_offer_none`), `offered_times` vazio, sem horário fabricado.
  - **Arquivos envolvidos:** `src/application/flow/orchestrator.py`
  - **Critério de conclusão:** Teste `test_initial_offer_no_slots_is_honest` verde.
  - **Dependências:** T-003
  - **Estimativa:** Pequena

### Fase 3: Testes e Validação

- [x] **T-009:** Teste de regressão — não-repetição da oferta
  - **Descrição:** `test_initial_offer_defers_when_offer_already_active` e `test_initial_offer_defers_with_recent_offer_in_history` (CA-004).
  - **Arquivos envolvidos:** `tests/test_orchestrator.py`
  - **Critério de conclusão:** Verde, cobrindo (a).
  - **Dependências:** T-004
  - **Estimativa:** Média

- [x] **T-010:** Teste de regressão — mensagem casa com o estado
  - **Descrição:** `test_initial_offer_message_matches_state_exactly` (a mensagem só mostra os slots salvos) + testes de seleção existentes (`test_slot_selection_not_among_options_is_rejected`) cobrindo (b).
  - **Arquivos envolvidos:** `tests/test_orchestrator.py`
  - **Critério de conclusão:** Verde, cobrindo (b).
  - **Dependências:** T-003
  - **Estimativa:** Média

- [-] **T-011:** Migrar testes dos guard-rails aposentados — **DEFERIDA → RF-003-B**
  - **Descrição:** Como T-006/T-007 foram deferidas, os guard-rails (e seus testes) permanecem. Migração ocorre junto da remoção.
  - **Arquivos envolvidos:** `tests/test_clean_agent_service.py`
  - **Dependências:** T-006
  - **Estimativa:** Média

- [x] **T-012:** Não-regressão + suíte completa
  - **Descrição:** Saudação/escopo/mensageria intactos; suíte total verde.
  - **Arquivos envolvidos:** suíte completa
  - **Critério de conclusão:** `pytest -q` = **552 passando, 0 falhas** (542 baseline + 10 novos testes 018).
  - **Dependências:** T-005, T-009, T-010
  - **Estimativa:** Pequena

### Fase 4: Documentação e Finalização

- [x] **T-013:** Atualizar status e índice
  - **Descrição:** CA-001/002/004/005/006/007 atendidos; CA-003 parcial (regex mantido como fallback, removal = RF-003-B). Status do `spec.md` → 🟢 Concluída (escopo seguro). `README.md` (Fase 3) e memória de status atualizados.
  - **Arquivos envolvidos:** `implementações/018 .../spec.md`, `implementações/README.md`
  - **Critério de conclusão:** Índice e spec refletindo a conclusão; suíte verde.
  - **Dependências:** T-012
  - **Estimativa:** Pequena

---

## Registro de Progresso

| Tarefa | Status | Data de Conclusão | Observações |
|--------|--------|-------------------|-------------|
| T-001  | ✅ Concluída | 2026-06-23 | Baseline 538+4 date bombs |
| T-001b | ✅ Concluída | 2026-06-23 | Date bombs saneados (clock freeze) |
| T-002  | ✅ Concluída | 2026-06-23 | Testes-alvo escritos |
| T-003  | ✅ Concluída | 2026-06-23 | `try_initial_offer` |
| T-004  | ✅ Concluída | 2026-06-23 | Encadeado no `handle`/dispatch |
| T-005  | ✅ Concluída | 2026-06-23 | Roteado no `app.py` |
| T-006  | ⏭️ Deferida | — | → RF-003-B (fallback mantido) |
| T-007  | ⏭️ Deferida | — | → RF-003-B (fallback mantido) |
| T-008  | ✅ Concluída | 2026-06-23 | "Sem horário" honesto |
| T-009  | ✅ Concluída | 2026-06-23 | Regressão (a) |
| T-010  | ✅ Concluída | 2026-06-23 | Regressão (b) |
| T-011  | ⏭️ Deferida | — | → RF-003-B |
| T-012  | ✅ Concluída | 2026-06-23 | Suíte 552 verde |
| T-013  | ✅ Concluída | 2026-06-23 | Índice/spec/memória |

---

> **📌 NOTA:** Regra de ouro do projeto — nada concluído sem teste de regressão verde.
> Runner: `C:/Apps/WPP-DENTAL/.venv/Scripts/python.exe -m pytest -q`. Suíte: **552 passando**.
