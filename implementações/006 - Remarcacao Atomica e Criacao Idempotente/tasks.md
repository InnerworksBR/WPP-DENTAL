# Tarefas: Remarcacao Atomica e Criacao Idempotente

> **Implementação:** 006 - Remarcacao Atomica e Criacao Idempotente
> **Spec:** [spec.md](./spec.md)
> **Progresso:** 11/11 tarefas concluídas (100%)
> **Última atualização:** 2026-06-15

Legenda: [ ] Pendente, [x] Concluída, [!] Bloqueada, [-] Cancelada.

---

## Fase 1 — Preparação

### [x] T-001 — Mapear e fixar o comportamento atual da remarcacao nos dois motores
- **Descrição:** Documentar via testes-caracterizacao o estado atual: o caminho deterministico (`_handle_offered_slot_selection`, `app.py:1019-1093`) ja troca; o caminho LLM (`_run_loop`, `clean_agent_service.py:292-389`) cria sem cancelar. Servira de baseline anti-regressao.
- **Arquivos envolvidos:** `src/interfaces/http/app.py`, `src/application/services/clean_agent_service.py`, `tests/`
- **Critério de conclusão:** Testes-caracterizacao verdes que evidenciam o bug AG-02 (2 eventos pelo LLM) e o acerto do deterministico (1 evento).
- **Dependências:** —
- **Estimativa:** Pequena

### [x] T-002 — Definir contrato de idempotencia de criacao por (telefone, slot)
- **Descrição:** Especificar a regra de match (telefone normalizado via `_normalize_phone` + `start.dateTime` normalizado via `_normalize_datetime`) e o ponto de insercao dentro de `_APPOINTMENT_CREATION_LOCK` (`calendar_service.py:524`).
- **Arquivos envolvidos:** `src/infrastructure/integrations/calendar_service.py` (492-529, 595-627)
- **Critério de conclusão:** Documento curto/ADR no PR descrevendo a chave logica e a comparacao normalizada.
- **Dependências:** —
- **Estimativa:** Pequena

## Fase 2 — Implementação

### [x] T-003 — Guarda no motor LLM: bloquear `criar_agendamento` quando `intent == "reschedule"`
- **Descrição:** Em `_run_loop` (no laco `for call in response.tool_calls`, junto aos guardas 319-347), adicionar verificacao: se `call["name"] == "criar_agendamento"` e `state.intent == "reschedule"`, NAO executar a tool e devolver `ToolMessage` instrutiva. Logar em nivel `warning`.
- **Arquivos envolvidos:** `src/application/services/clean_agent_service.py` (292-389)
- **Critério de conclusão:** RF-001 implementado; LLM nunca cria evento durante remarcacao.
- **Dependências:** T-001
- **Estimativa:** Média

### [x] T-004 — Idempotencia em `create_appointment_if_available`
- **Descrição:** Dentro do `with _APPOINTMENT_CREATION_LOCK` (524), antes de `_slot_conflicts`, buscar evento existente para `(telefone, slot)` via `find_appointments_by_phone`; se encontrado (telefone batendo + `start.dateTime` == `start_sp` normalizado), retornar esse evento sem inserir. Envolver a busca em try/except (degrada para o fluxo atual). Logar reuso.
- **Arquivos envolvidos:** `src/infrastructure/integrations/calendar_service.py` (492-529, 595-627)
- **Critério de conclusão:** RF-004 implementado; chamadas repetidas com mesmo (telefone, slot) retornam o mesmo evento.
- **Dependências:** T-002
- **Estimativa:** Média

### [x] T-005 — Garantir ordem segura e reuso no caminho deterministico
- **Descrição:** Revisar `_handle_offered_slot_selection` (1019-1093) para confirmar que a criacao (1020) usa a versao idempotente e que a troca (criar→cancelar→preservar parcial em 1035-1065) permanece intacta. Ajustar somente se necessario para nao confirmar sucesso em falha parcial.
- **Arquivos envolvidos:** `src/interfaces/http/app.py` (1019-1093, 592-644)
- **Critério de conclusão:** RF-002 e RF-003 garantidos; 1 evento final em sucesso; mensagem parcial + alerta em falha de cancelamento.
- **Dependências:** T-004
- **Estimativa:** Média

### [x] T-006 — Ajustar mensagem do `CreateAppointmentTool` para reuso idempotente
- **Descrição:** Em `CreateAppointmentTool._run` (344-363), evitar texto enganoso quando o evento foi reutilizado (nao afirmar duplicacao); manter o `id` real do evento. Reforcar que a tool nunca e usada para remarcacao (descricao 336-341 + comportamento via guarda do LLM).
- **Arquivos envolvidos:** `src/interfaces/tools/calendar_tool.py` (324-363)
- **Critério de conclusão:** RF-006 atendido; mensagem coerente com o estado real.
- **Dependências:** T-004
- **Estimativa:** Pequena

### [x] T-007 — Confirmar que reentrega 502 nao recria evento
- **Descrição:** Validar a interacao entre `_mark_message_failed`/reclaim (`app.py:1097-1106, 1420-1451`) e a idempotencia da criacao: apos 502 e reentrega, `create_appointment_if_available` deve reutilizar o evento ja criado.
- **Arquivos envolvidos:** `src/interfaces/http/app.py` (1095-1111, 1420-1475), `src/infrastructure/integrations/calendar_service.py` (492-529)
- **Critério de conclusão:** RF-005 garantido; 1 evento apos reentrega.
- **Dependências:** T-004
- **Estimativa:** Média

## Fase 3 — Testes

### [x] T-008 — Testes de regressao do guarda do LLM (AG-02/CA-02)
- **Descrição:** Unitarios cobrindo TU-1: `_run_loop` com `intent="reschedule"` bloqueia `criar_agendamento`; `CreateAppointmentTool` mockado nao e chamado.
- **Arquivos envolvidos:** `tests/test_reschedule_atomic.py`, `src/application/services/clean_agent_service.py`
- **Critério de conclusão:** CA-001 e CA-007 verdes.
- **Dependências:** T-003
- **Estimativa:** Média

### [x] T-009 — Testes de idempotencia de criacao (WH-01/IDEMPOTENCIA)
- **Descrição:** Unitarios TU-2/TU-3 e integracao TI-2: dupla chamada de `create_appointment_if_available` retorna 1 evento; reentrega pos-502 nao duplica.
- **Arquivos envolvidos:** `tests/test_reschedule_atomic.py`, `src/infrastructure/integrations/calendar_service.py`, `src/interfaces/http/app.py`
- **Critério de conclusão:** CA-004 e CA-005 verdes.
- **Dependências:** T-004, T-007
- **Estimativa:** Média

### [x] T-010 — Testes de troca atomica e remarcacao parcial (CA-05 + RF-002/003)
- **Descrição:** TI-1 (remarcacao feliz → 1 evento, antigo cancelado) e TU-4 (cancelamento do antigo falha → estado parcial + alerta, sem confirmacao). Cobrir casos de borda CB-1 (duas consultas) e CB-3 (concorrencia/lock).
- **Arquivos envolvidos:** `tests/test_main_webhook.py` (ja cobre estes cenarios)
- **Critério de conclusão:** CA-002, CA-003 e CA-006 verdes; CB-1/CB-3 cobertos.
- **Dependências:** T-005
- **Estimativa:** Grande

## Fase 4 — Documentação

### [x] T-011 — Atualizar documentacao e registrar decisoes
- **Descrição:** Atualizar README/tasks.md e notas de operacao descrevendo: remarcacao sempre deterministica, idempotencia por (telefone, slot), comportamento de reentrega 502. Marcar findings AG-02/CA-02, CA-05, WH-01 como resolvidos.
- **Arquivos envolvidos:** `implementações/006 - Remarcacao Atomica e Criacao Idempotente/`, docs do projeto
- **Critério de conclusão:** Documentacao revisada e checklist da spec atualizado.
- **Dependências:** T-003..T-010
- **Estimativa:** Pequena

---

## Registro de Progresso

| Tarefa | Descrição | Fase | Estimativa | Status |
|---|---|---|---|---|
| T-001 | Baseline/caracterizacao dos dois motores | Preparação | Pequena | [x] 2026-06-15 |
| T-002 | Contrato de idempotencia (telefone, slot) | Preparação | Pequena | [x] 2026-06-15 |
| T-003 | Guarda LLM bloqueia criar_agendamento em reschedule | Implementação | Média | [x] 2026-06-15 |
| T-004 | Idempotencia em create_appointment_if_available | Implementação | Média | [x] 2026-06-15 |
| T-005 | Ordem segura + reuso no deterministico | Implementação | Média | [x] 2026-06-15 |
| T-006 | Mensagem coerente no CreateAppointmentTool | Implementação | Pequena | [x] 2026-06-15 |
| T-007 | Reentrega 502 nao recria evento | Implementação | Média | [x] 2026-06-15 |
| T-008 | Testes regressao guarda LLM (AG-02/CA-02) | Testes | Média | [x] 2026-06-15 |
| T-009 | Testes idempotencia (WH-01) | Testes | Média | [x] 2026-06-15 |
| T-010 | Testes troca atomica + parcial (CA-05) | Testes | Grande | [x] 2026-06-15 |
| T-011 | Documentacao e decisoes | Documentação | Pequena | [x] 2026-06-15 |

**Total:** 11 tarefas | **Concluídas:** 11 | **Progresso:** 100%
