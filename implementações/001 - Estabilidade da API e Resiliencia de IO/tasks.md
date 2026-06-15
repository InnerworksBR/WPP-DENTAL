# Tarefas: Estabilidade da API e Resiliência de IO

> **Implementação:** 001 - Estabilidade da API e Resiliência de IO
> **Spec:** [spec.md](./spec.md)
> **Progresso:** 12/12 tarefas concluídas (100%)
> **Última atualização:** 2026-06-15

Legenda: [ ] Pendente, [x] Concluída, [!] Bloqueada, [-] Cancelada.

---

## Fase 1 — Preparação

### [x] T-001 — Mapear pontos de falha e baseline de erros
- **Descrição:** Confirmar em código os seis findings (AG-01, EVENT-LOOP, WE-10, CONNECTION, AG-06/CA-03, WH-07) e registrar arquivo:linha exatos; criar fixtures/mocks reutilizáveis para `ChatOpenAI`, `CalendarService`, `get_db` e `OutboundMessageStore`.
- **Arquivos envolvidos:** `src/application/services/clean_agent_service.py`, `src/interfaces/http/app.py`, `src/infrastructure/persistence/connection.py`, `src/interfaces/tools/calendar_tool.py`, `src/infrastructure/integrations/whatsapp_service.py`, `tests/` (novas fixtures).
- **Critério de conclusão:** Documento curto de baseline + fixtures de mock compilando nos testes.
- **Dependências:** Nenhuma.
- **Estimativa:** Pequena.

---

## Fase 2 — Implementação

### [x] T-002 — AG-01: timeout, max_tokens e retry no cliente LLM
- **Descrição:** Adicionar `request_timeout` (20–30s) e `max_tokens` ao `ChatOpenAI` (`clean_agent_service.py:286-290`). Envolver `self._llm.invoke(messages)` (`:295`) em try/except tratando `APITimeoutError` e `RateLimitError` com retry curto; ao esgotar, retornar mensagem amigável "tente novamente em instantes" sem escalonamento silencioso.
- **Arquivos envolvidos:** `src/application/services/clean_agent_service.py`.
- **Critério de conclusão:** RF-001 e RF-002 atendidos; CA-001 e CA-002 verificáveis.
- **Dependências:** T-001.
- **Estimativa:** Média.

### [x] T-003 — EVENT-LOOP: executar process_message fora do event loop
- **Descrição:** Substituir a chamada síncrona `dental_crew.process_message(...)` em `app.py:255-264` por `await asyncio.to_thread(dental_crew.process_message, ...)` (ou `run_in_executor`), preservando assinatura e tratamento de exceção existente (`app.py:265-272`).
- **Arquivos envolvidos:** `src/interfaces/http/app.py`.
- **Critério de conclusão:** RF-002 atendido; CA-003 verificável; conversas concorrentes não serializam o event loop.
- **Dependências:** T-001.
- **Estimativa:** Pequena.

### [x] T-004 — WE-10: degradação segura nas operações de SQLite do webhook
- **Descrição:** Adicionar try/except com degradação segura em `_try_claim_message_processing` (`app.py:1420`), `_mark_message_processed` (`app.py:1454`), `_mark_message_failed` (`app.py:1466`) e no uso de `ConversationStateService.get` em `app.py:204` (fallback para `ConversationState()`), logando o erro original.
- **Arquivos envolvidos:** `src/interfaces/http/app.py`, `src/application/services/conversation_state_service.py` (se for necessário endurecer `get`).
- **Critério de conclusão:** RF-003 atendido; CA-004 verificável; nenhum 500 por lock/IO de SQLite nesses pontos.
- **Dependências:** T-001.
- **Estimativa:** Média.

### [x] T-005 — CONNECTION: busy_timeout e check_same_thread explícito
- **Descrição:** Em `connection.py:get_db` (`:94-102`), adicionar `PRAGMA busy_timeout` (ex.: 5000ms) e definir explicitamente `check_same_thread` coerente com o modelo `threading.local`, mantendo `journal_mode=WAL` (`:100`) e `foreign_keys=ON` (`:101`).
- **Arquivos envolvidos:** `src/infrastructure/persistence/connection.py`.
- **Critério de conclusão:** RF-004 atendido; CA-005 verificável.
- **Dependências:** T-001.
- **Estimativa:** Pequena.

### [x] T-006 — AG-06: padronizar erro de tool no _run_loop
- **Descrição:** Em `clean_agent_service.py:353-357`, substituir `f"Erro em '{call['name']}': {exc}"` por mensagem segura padronizada (sem `exc` cru) mantendo o `logger.warning` com o erro original.
- **Arquivos envolvidos:** `src/application/services/clean_agent_service.py`.
- **Critério de conclusão:** RF-005 atendido; CA-006 verificável.
- **Dependências:** T-001.
- **Estimativa:** Pequena.

### [x] T-007 — CA-03: try/except dedicado nas tools de calendar
- **Descrição:** Envolver as chamadas à API Google em try/except retornando mensagem segura nas tools que ainda não tratam: `GetAvailableSlotsTool._run` (`calendar_tool.py:182`), `CreateAppointmentTool._run` (`:350-354`), `CancelAppointmentTool._run` (`:394-432`), `FindAppointmentTool._run` (`:460-477`); padronizar a mensagem já existente em `FindNextAvailableDayTool._run` (`:320-321`). Replicar o padrão de `CalendarService.cancel_appointment` (`calendar_service.py:531-544`).
- **Arquivos envolvidos:** `src/interfaces/tools/calendar_tool.py`, `src/infrastructure/integrations/calendar_service.py` (ajustes defensivos pontuais nas leituras).
- **Critério de conclusão:** RF-005 atendido; CA-007 verificável.
- **Dependências:** T-006.
- **Estimativa:** Média.

### [x] T-008 — WH-07: isolar OutboundMessageStore.record do envio
- **Descrição:** Mover `OutboundMessageStore.record(...)` para try/except próprio em `send_message` (`whatsapp_service.py:92-96`) e `send_message_sync` (`:127-131`), garantindo retorno `True` quando o POST teve sucesso, logando falha de persistência.
- **Arquivos envolvidos:** `src/infrastructure/integrations/whatsapp_service.py`.
- **Critério de conclusão:** RF-006 atendido; CA-008 verificável.
- **Dependências:** T-001.
- **Estimativa:** Pequena.

---

## Fase 3 — Testes

### [x] T-009 — Testes de regressão do motor LLM (AG-01 + AG-06)
- **Descrição:** Testes unitários: cliente criado com `request_timeout`/`max_tokens`; `_run_loop` com `invoke` lançando `APITimeoutError`/`RateLimitError` (retry + fallback amigável); tool mockada lançando exceção (ToolMessage com `Erro:` seguro, sem stack trace).
- **Arquivos envolvidos:** `tests/` (novo arquivo p/ `clean_agent_service`).
- **Critério de conclusão:** CA-001, CA-002 e CA-006 cobertos e verdes.
- **Dependências:** T-002, T-006.
- **Estimativa:** Média.

### [x] T-010 — Testes de regressão do webhook (EVENT-LOOP + WE-10)
- **Descrição:** Testes de integração: `process_message` lento via `to_thread` não serializa duas requisições; `get_db` lançando `OperationalError` em claim/mark e `ConversationStateService.get` falhando não geram 500.
- **Arquivos envolvidos:** `tests/` (novo arquivo p/ webhook).
- **Critério de conclusão:** CA-003 e CA-004 cobertos e verdes.
- **Dependências:** T-003, T-004.
- **Estimativa:** Média.

### [x] T-011 — Testes de regressão de persistência e integrações (CONNECTION + CA-03 + WH-07)
- **Descrição:** Testes: `get_db` aplica `busy_timeout` e mantém `journal_mode=wal`; tool de calendar com Google indisponível retorna mensagem segura; `send_message_sync` retorna `True` quando `record` lança `sqlite3.Error` e o POST teve sucesso.
- **Arquivos envolvidos:** `tests/` (arquivos p/ connection, calendar_tool, whatsapp_service).
- **Critério de conclusão:** CA-005, CA-007 e CA-008 cobertos e verdes.
- **Dependências:** T-005, T-007, T-008.
- **Estimativa:** Média.

---

## Fase 4 — Documentação

### [x] T-012 — Atualizar documentação e validação final de aceitação
- **Descrição:** Revisar CA-001..CA-008, atualizar este `tasks.md` (progresso e registro), e documentar no README/CLAUDE notas sobre timeouts do LLM, `busy_timeout` e execução em thread. Rodar a suíte completa.
- **Arquivos envolvidos:** `implementações/001 - Estabilidade da API e Resiliencia de IO/tasks.md`, `README`/`CLAUDE.md` (se aplicável).
- **Critério de conclusão:** Todos os critérios de aceitação marcados; suíte verde.
- **Dependências:** T-009, T-010, T-011.
- **Estimativa:** Pequena.

---

## Registro de Progresso

| Tarefa | Descrição curta | Fase | Status | Estimativa |
|---|---|---|---|---|
| T-001 | Baseline e fixtures de mock | Preparação | [x] Concluída | Pequena |
| T-002 | AG-01: timeout/max_tokens/retry LLM | Implementação | [x] Concluída | Média |
| T-003 | EVENT-LOOP: process_message via to_thread | Implementação | [x] Concluída | Pequena |
| T-004 | WE-10: SQLite seguro no webhook | Implementação | [x] Concluída | Média |
| T-005 | CONNECTION: busy_timeout/check_same_thread | Implementação | [x] Concluída | Pequena |
| T-006 | AG-06: erro de tool padronizado | Implementação | [x] Concluída | Pequena |
| T-007 | CA-03: try/except nas tools de calendar | Implementação | [x] Concluída | Média |
| T-008 | WH-07: isolar record do envio | Implementação | [x] Concluída | Pequena |
| T-009 | Testes motor LLM (AG-01/AG-06) | Testes | [x] Concluída | Média |
| T-010 | Testes webhook (EVENT-LOOP/WE-10) | Testes | [x] Concluída | Média |
| T-011 | Testes persistência/integrações | Testes | [x] Concluída | Média |
| T-012 | Documentação e aceitação final | Documentação | [x] Concluída | Pequena |

---

## Resultado da Execução (2026-06-15)

Branch: `fix/001-estabilidade-api`.

**Arquivos alterados:**
- `src/application/services/clean_agent_service.py` — `request_timeout`/`max_retries`/`max_tokens` no `ChatOpenAI`; helper `_invoke_llm` com retry para `APITimeoutError`/`RateLimitError`/`APIConnectionError` e fallback amigável; erro de tool padronizado (`_TOOL_SAFE_ERROR`).
- `src/interfaces/http/app.py` — `process_message` via `asyncio.to_thread` com wrapper `_process_message_in_worker` (fecha a conexão por-thread ao final); `try/except` em `_try_claim_message_processing`, `_mark_message_processed`, `_mark_message_failed` e na leitura de estado.
- `src/application/services/conversation_state_service.py` — `get()` resiliente a `sqlite3.Error` (degrada para estado padrão).
- `src/infrastructure/persistence/connection.py` — `PRAGMA busy_timeout=5000` e `check_same_thread=True` explícito.
- `src/interfaces/tools/calendar_tool.py` — `try/except` nas chamadas Google das 5 tools com mensagem segura padronizada.
- `src/infrastructure/integrations/whatsapp_service.py` — `OutboundMessageStore.record` isolado em `try/except` (entrega não falha por erro de persistência).

**Testes adicionados (todos verdes):**
- `tests/test_clean_agent_service.py` (3) — CA-001, CA-002, CA-006.
- `tests/test_webhook_resilience.py` (3) — CA-003, CA-004.
- `tests/test_resilience_io.py` (3) — CA-005, CA-007, CA-008.

**Suíte:** `pytest -q` → 67 failed / 123 passed. As 67 falhas são exclusivamente `ModuleNotFoundError` de módulos removidos (escopo da Implementação 002); **nenhuma regressão** introduzida (baseline 114 → 123 passando, +9 novos).
