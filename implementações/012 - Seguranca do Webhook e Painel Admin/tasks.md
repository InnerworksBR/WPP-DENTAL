# Tarefas: Segurança do Webhook e Painel Admin

> **Implementação:** 012 - Segurança do Webhook e Painel Admin
> **Spec:** [spec.md](./spec.md)
> **Progresso:** 0/13 tarefas concluídas (0%)
> **Última atualização:** 2026-06-15

Legenda: [ ] Pendente, [x] Concluída, [!] Bloqueada, [-] Cancelada.

---

## Fase 1 — Preparação

### [ ] T-001 — Confirmar pontos de mudança e baseline de testes
- **Descrição:** Reler e mapear no código os pontos exatos: `_authenticate_request` (`src/interfaces/http/app.py:1354-1406`), `_extract_request_api_key` (`464-486`), `receive_message` logs (`144` e `179`), `_require_admin` (`src/interfaces/http/admin.py:62-69`), `delete_block` (`339-349`), `delete_day_block` (`src/infrastructure/integrations/calendar_service.py:363-369`), `event_is_day_block` (`308-318`), `_calendar_error_payload` (`83-88`). Rodar a suíte atual (`tests/test_main_webhook.py`, `tests/test_admin.py`) para registrar o baseline e identificar o teste `test_message_webhook_accepts_request_without_valid_auth_header` (`test_main_webhook.py:63-82`) que mudará.
- **Arquivos envolvidos:** `src/interfaces/http/app.py`, `src/interfaces/http/admin.py`, `src/infrastructure/integrations/calendar_service.py`, `tests/test_main_webhook.py`, `tests/test_admin.py`.
- **Critério de conclusão:** Lista de arquivos:linha confirmada e baseline de testes registrado (verde/vermelho atual).
- **Dependências:** Implementações 001 e 002.
- **Estimativa:** Pequena.

---

## Fase 2 — Implementação

### [ ] T-002 — Fechar webhook: rejeitar 401 com chave configurada (WE-03)
- **Descrição:** Ajustar `_authenticate_request` para que, havendo chaves aceitas e mismatch, levante `HTTPException(401, "Unauthorized webhook request")` em vez de tolerar via `allow_unauthorized` (`src/interfaces/http/app.py:1398-1405`). Quando NÃO houver nenhuma chave, processar mas emitir `logger.critical(...)` (controlado por flag única, no padrão de `_webhook_auth_*_warning_logged`). Atualizar a chamada em `receive_message` (`src/interfaces/http/app.py:137-143`) conforme a nova política.
- **Arquivos envolvidos:** `src/interfaces/http/app.py`.
- **Critério de conclusão:** Mismatch com chave configurada → 401; ausência total de chave → 200 + log crítico (RF-001, RF-002).
- **Dependências:** T-001.
- **Estimativa:** Média.

### [ ] T-003 — Restringir origem da chave a header/query (WE-09)
- **Descrição:** Remover de `_extract_request_api_key` o bloco que lê a chave do corpo do payload (`src/interfaces/http/app.py:480-484`), mantendo header (`apikey`/`x-api-key`/`x-webhook-key`/`Authorization: Bearer`) e query string.
- **Arquivos envolvidos:** `src/interfaces/http/app.py`.
- **Critério de conclusão:** Chave só no corpo é ignorada; header/query continuam funcionando (RF-003).
- **Dependências:** T-001.
- **Estimativa:** Pequena.

### [ ] T-004 — Redigir PII e payload nos logs do webhook (CO-08/WE-09)
- **Descrição:** Criar helper(s) de redação (ex.: `_redact_payload`/`_redact_phone`) e substituir `logger.debug("Webhook recebido: %s", payload)` (`src/interfaces/http/app.py:144`) por log redigido, e ajustar `logger.info("Mensagem de %s (%s): %s...", ...)` (`:179`) para mascarar telefone/texto e não vazar nome/chave.
- **Arquivos envolvidos:** `src/interfaces/http/app.py`.
- **Critério de conclusão:** Nenhum log contém payload completo, telefone completo, nome, texto ou chave em texto claro (RF-004, RNF-004).
- **Dependências:** T-001.
- **Estimativa:** Média.

### [ ] T-005 — Exigir chave forte no painel e detectar produção (AD-01)
- **Descrição:** Criar helpers `_is_production` (lê `ENVIRONMENT`) e `_is_strong_key` (reprova vazio/ausente e o placeholder `your-admin-panel-key`). Ajustar `_require_admin` (`src/interfaces/http/admin.py:62-69`): em produção sem chave forte → `HTTPException(503, "Admin panel authentication not configured")`; fora de produção sem chave → mantém aberto. Atualizar `get_auth_config` se necessário para refletir o estado.
- **Arquivos envolvidos:** `src/interfaces/http/admin.py`.
- **Critério de conclusão:** Produção sem chave/placeholder → 503; produção com chave forte → 200/401 conforme header; dev sem chave → 200 (RF-005, RF-006).
- **Dependências:** T-001.
- **Estimativa:** Média.

### [ ] T-006 — Proteger exclusão de eventos no endpoint (AD-02)
- **Descrição:** Em `delete_block` (`src/interfaces/http/admin.py:339-349`), antes de excluir, carregar o evento e exigir `CalendarService.event_is_day_block(event)`; se não for bloqueio, retornar `{"ok": false, "error": "Evento nao e um bloqueio.", "items": []}` sem apagar.
- **Arquivos envolvidos:** `src/interfaces/http/admin.py`.
- **Critério de conclusão:** ID de consulta real não é apagado; ID de bloqueio é apagado (RF-007).
- **Dependências:** T-001.
- **Estimativa:** Média.

### [ ] T-007 — Defesa em profundidade em `delete_day_block` (AD-02)
- **Descrição:** Em `CalendarService.delete_day_block` (`src/infrastructure/integrations/calendar_service.py:363-369`), buscar o evento (`events().get`) e só chamar `events().delete` se `event_is_day_block` for verdadeiro; caso contrário retornar `False` sem apagar. Manter retorno `False` para `event_id` vazio.
- **Arquivos envolvidos:** `src/infrastructure/integrations/calendar_service.py`.
- **Critério de conclusão:** Método nunca apaga não-bloqueio mesmo se chamado fora do endpoint (RF-008).
- **Dependências:** T-001.
- **Estimativa:** Média.

### [ ] T-008 — Tratar erros nos endpoints do painel (AD-03)
- **Descrição:** Envolver `get_summary` (`103-138`), `list_patients` (`141-177`), `list_conversations` (`180-219`) e `list_errors` (`252-274`) em `try/except`; logar o erro server-side (com redação) e responder de forma controlada (sem stack trace / 500 cru).
- **Arquivos envolvidos:** `src/interfaces/http/admin.py`.
- **Critério de conclusão:** Erro de banco em qualquer um dos quatro endpoints não vira 500 não tratado (RF-009).
- **Dependências:** T-001.
- **Estimativa:** Média.

### [ ] T-009 — Recusar data no passado em `create_block` (AD-04)
- **Descrição:** Em `create_block`/`_parse_date` (`src/interfaces/http/admin.py:326-336`, `76-80`), rejeitar datas anteriores a hoje no fuso `SAO_PAULO_TZ` com `HTTPException(422, "Nao e possivel bloquear uma data no passado.")`.
- **Arquivos envolvidos:** `src/interfaces/http/admin.py`.
- **Critério de conclusão:** Data passada → 422; hoje/futuro → cria normalmente (RF-010).
- **Dependências:** T-001.
- **Estimativa:** Pequena.

### [ ] T-010 — Sanear campo `error` dos endpoints de agenda/bloqueio (AD-06)
- **Descrição:** Ajustar `_calendar_error_payload` (`src/interfaces/http/admin.py:83-88`) para retornar mensagem genérica (ex.: `"Falha ao consultar a agenda."`) e logar `str(exc)` apenas server-side. Conferir os usos em `list_appointments` (`286-287`), `list_blocks` (`319-322`), `create_block` (`334-335`) e `delete_block` (`345-346`).
- **Arquivos envolvidos:** `src/interfaces/http/admin.py`.
- **Critério de conclusão:** Campo `error` nunca contém `str(exc)` original/credenciais; detalhe vai só para o log (RF-011).
- **Dependências:** T-001.
- **Estimativa:** Pequena.

### [ ] T-011 — Atualizar `.env.example` e documentação de ambiente
- **Descrição:** Substituir `ADMIN_API_KEY=your-admin-panel-key` (`.env.example:9`) por marcador de obrigatoriedade (sem placeholder utilizável), e documentar `ENVIRONMENT` (dev/production) e a política de webhook fechado no `.env.example` e no `README.md` (`README.md:207`).
- **Arquivos envolvidos:** `.env.example`, `README.md`.
- **Critério de conclusão:** Placeholder público removido; `ENVIRONMENT` e nova política documentados.
- **Dependências:** T-005, T-002.
- **Estimativa:** Pequena.

---

## Fase 3 — Testes

### [ ] T-012 — Testes de regressão do webhook (WE-03/WE-09/CO-08)
- **Descrição:** Atualizar `test_message_webhook_accepts_request_without_valid_auth_header` (`tests/test_main_webhook.py:63-82`) para esperar `401`. Adicionar testes: (a) chave configurada + sem header → 401 e `process_message` não chamado; (b) chave configurada + header válido → 200; (c) chave só no corpo → 401; (d) sem nenhuma chave → 200 + log crítico via `caplog`; (e) asserção de ausência de PII/payload nos logs capturados.
- **Arquivos envolvidos:** `tests/test_main_webhook.py`.
- **Critério de conclusão:** CA-001..CA-005 verdes.
- **Dependências:** T-002, T-003, T-004.
- **Estimativa:** Média.

### [ ] T-013 — Testes de regressão do painel e calendário (AD-01..AD-06)
- **Descrição:** Em `tests/test_admin.py` (e mock de `CalendarService`): matriz de produção sem/placeholder/forte × header (CA-006/CA-007/CA-008); `DELETE /api/blocks/{id}` com consulta real (não apaga) vs. bloqueio (apaga) (CA-009/CA-010); erro injetado em `summary/patients/conversations/errors` → resposta controlada (CA-011); `POST /api/blocks` com data passada → 422 (CA-012); Calendar lançando exceção → `error` genérico sem `str(exc)` (CA-013). Incluir teste unitário de `delete_day_block` recusando não-bloqueio.
- **Arquivos envolvidos:** `tests/test_admin.py`, (eventual) `tests/test_calendar_service.py`.
- **Critério de conclusão:** CA-006..CA-013 verdes.
- **Dependências:** T-005, T-006, T-007, T-008, T-009, T-010.
- **Estimativa:** Grande.

---

## Fase 4 — Documentação

### [ ] T-014 — Atualizar relatório técnico e marcar progresso
- **Descrição:** Atualizar a seção de autenticação do `docs/RELATORIO_TECNICO_DESENVOLVIMENTO.md` (`:218`) descrevendo a nova política de webhook fechado, painel protegido por ambiente e redação de PII. Atualizar o cabeçalho de progresso desta `tasks.md` e a tabela de registro.
- **Arquivos envolvidos:** `docs/RELATORIO_TECNICO_DESENVOLVIMENTO.md`, `implementações/012 - Seguranca do Webhook e Painel Admin/tasks.md`.
- **Critério de conclusão:** Documentação reflete o comportamento implementado e progresso atualizado.
- **Dependências:** T-012, T-013.
- **Estimativa:** Pequena.

---

## Registro de Progresso

| Tarefa | Descrição | Findings | Fase | Estimativa | Status |
|---|---|---|---|---|---|
| T-001 | Confirmar pontos de mudança e baseline | Todos | Preparação | Pequena | [ ] Pendente |
| T-002 | Fechar webhook (401 com chave) | WE-03 | Implementação | Média | [ ] Pendente |
| T-003 | Chave só via header/query | WE-09 | Implementação | Pequena | [ ] Pendente |
| T-004 | Redigir PII/payload nos logs | CO-08, WE-09 | Implementação | Média | [ ] Pendente |
| T-005 | Chave forte + detecção de produção | AD-01 | Implementação | Média | [ ] Pendente |
| T-006 | Proteger exclusão no endpoint | AD-02 | Implementação | Média | [ ] Pendente |
| T-007 | Defesa em profundidade no método | AD-02 | Implementação | Média | [ ] Pendente |
| T-008 | try/except nos endpoints | AD-03 | Implementação | Média | [ ] Pendente |
| T-009 | Recusar data no passado | AD-04 | Implementação | Pequena | [ ] Pendente |
| T-010 | Sanear campo error | AD-06 | Implementação | Pequena | [ ] Pendente |
| T-011 | .env.example + docs de ambiente | AD-01 | Implementação | Pequena | [ ] Pendente |
| T-012 | Testes de regressão do webhook | WE-03, WE-09, CO-08 | Testes | Média | [ ] Pendente |
| T-013 | Testes de regressão do painel/calendário | AD-01..AD-06 | Testes | Grande | [ ] Pendente |
| T-014 | Atualizar relatório técnico e progresso | Todos | Documentação | Pequena | [ ] Pendente |
