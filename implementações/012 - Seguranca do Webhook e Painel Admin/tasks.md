# Tarefas: Segurança do Webhook e Painel Admin

> **Implementação:** 012 - Segurança do Webhook e Painel Admin
> **Spec:** [spec.md](./spec.md)
> **Progresso:** 13/13 tarefas concluídas (100%)
> **Última atualização:** 2026-06-15

Legenda: [ ] Pendente, [x] Concluída, [!] Bloqueada, [-] Cancelada.

---

## Fase 1 — Preparação

### [x] T-001 — Confirmar pontos de mudança e baseline de testes
- **Descrição:** Mapeados os pontos exatos no código; baseline de 259 testes verdes registrado; `test_message_webhook_accepts_request_without_valid_auth_header` identificado como teste a atualizar.
- **Arquivos envolvidos:** `src/interfaces/http/app.py`, `src/interfaces/http/admin.py`, `src/infrastructure/integrations/calendar_service.py`, `tests/test_main_webhook.py`, `tests/test_admin.py`.
- **Critério de conclusão:** Lista de arquivos:linha confirmada e baseline registrado.
- **Dependências:** Implementações 001 e 002.
- **Estimativa:** Pequena.

---

## Fase 2 — Implementação

### [x] T-002 — Fechar webhook: rejeitar 401 com chave configurada (WE-03)
- **Descrição:** Ajustado `_authenticate_request`: mismatch com chave configurada sempre levanta HTTPException(401). Ausência total de chave → log critical + proceed. Removido `allow_unauthorized` parameter. Atualizado `receive_message`.
- **Arquivos envolvidos:** `src/interfaces/http/app.py`.
- **Critério de conclusão:** Mismatch com chave configurada → 401; ausência total de chave → 200 + log crítico (RF-001, RF-002).
- **Dependências:** T-001.
- **Estimativa:** Média.

### [x] T-003 — Restringir origem da chave a header/query (WE-09)
- **Descrição:** Removido bloco de extração da chave do corpo do payload em `_extract_request_api_key`. Chave aceita apenas via header e query string.
- **Arquivos envolvidos:** `src/interfaces/http/app.py`.
- **Critério de conclusão:** Chave só no corpo é ignorada; header/query continuam funcionando (RF-003).
- **Dependências:** T-001.
- **Estimativa:** Pequena.

### [x] T-004 — Redigir PII e payload nos logs do webhook (CO-08/WE-09)
- **Descrição:** Adicionado helper `_redact_phone`. Log do webhook recebido agora mostra apenas `event` e `instance`. Log de mensagem usa telefone redigido (últimos 4 dígitos) e trunca texto.
- **Arquivos envolvidos:** `src/interfaces/http/app.py`.
- **Critério de conclusão:** Nenhum log contém payload completo, telefone completo nem chave (RF-004, RNF-004).
- **Dependências:** T-001.
- **Estimativa:** Média.

### [x] T-005 — Exigir chave forte no painel e detectar produção (AD-01)
- **Descrição:** Adicionados `_is_production()`, `_is_strong_key()`, `_PLACEHOLDER_KEY`. `_require_admin` agora rejeita com 503 em produção sem chave forte.
- **Arquivos envolvidos:** `src/interfaces/http/admin.py`.
- **Critério de conclusão:** Produção sem chave/placeholder → 503; produção com chave forte → 200/401; dev sem chave → 200 (RF-005, RF-006).
- **Dependências:** T-001.
- **Estimativa:** Média.

### [x] T-006 — Proteger exclusão de eventos no endpoint (AD-02)
- **Descrição:** `delete_block` agora busca o evento e verifica `event_is_day_block` antes de deletar. Evento que não é bloqueio retorna `{"ok": false, "error": "Evento nao e um bloqueio."}`.
- **Arquivos envolvidos:** `src/interfaces/http/admin.py`.
- **Critério de conclusão:** ID de consulta real não é apagado; ID de bloqueio é apagado (RF-007).
- **Dependências:** T-001.
- **Estimativa:** Média.

### [x] T-007 — Defesa em profundidade em `delete_day_block` (AD-02)
- **Descrição:** `CalendarService.delete_day_block` busca o evento e verifica `event_is_day_block` antes de chamar `events().delete`. Retorna `False` para não-bloqueios.
- **Arquivos envolvidos:** `src/infrastructure/integrations/calendar_service.py`.
- **Critério de conclusão:** Método nunca apaga não-bloqueio mesmo chamado fora do endpoint (RF-008).
- **Dependências:** T-001.
- **Estimativa:** Média.

### [x] T-008 — Tratar erros nos endpoints do painel (AD-03)
- **Descrição:** `get_summary`, `list_patients`, `list_conversations`, `list_errors` envolvidos em `try/except`; retornam 503 com mensagem genérica em caso de erro de banco.
- **Arquivos envolvidos:** `src/interfaces/http/admin.py`.
- **Critério de conclusão:** Erro de banco → resposta controlada, não 500 cru (RF-009).
- **Dependências:** T-001.
- **Estimativa:** Média.

### [x] T-009 — Recusar data no passado em `create_block` (AD-04)
- **Descrição:** `_parse_date` recebe `reject_past=True` via `create_block`; datas anteriores a hoje retornam 422.
- **Arquivos envolvidos:** `src/interfaces/http/admin.py`.
- **Critério de conclusão:** Data passada → 422; hoje/futuro → cria (RF-010).
- **Dependências:** T-001.
- **Estimativa:** Pequena.

### [x] T-010 — Sanear campo `error` dos endpoints de agenda/bloqueio (AD-06)
- **Descrição:** `_calendar_error_payload` retorna mensagem genérica e loga `str(exc)` server-side via `_admin_logger`. Todos os callers passam o contexto.
- **Arquivos envolvidos:** `src/interfaces/http/admin.py`.
- **Critério de conclusão:** Campo `error` nunca contém `str(exc)` original; detalhe no log (RF-011).
- **Dependências:** T-001.
- **Estimativa:** Pequena.

### [x] T-011 — Atualizar `.env.example` e documentação de ambiente
- **Descrição:** `ADMIN_API_KEY` substituído por placeholder explícito sem valor utilizável. `ENVIRONMENT` documentado.
- **Arquivos envolvidos:** `.env.example`.
- **Critério de conclusão:** Placeholder público removido; `ENVIRONMENT` documentado.
- **Dependências:** T-005, T-002.
- **Estimativa:** Pequena.

---

## Fase 3 — Testes

### [x] T-012 — Testes de regressão do webhook (WE-03/WE-09/CO-08)
- **Descrição:** `TestWebhookSecurity` (5 testes) em `tests/test_main_webhook.py`: chave válida no header → 200; sem header → 401; header errado → 401; chave só no corpo → 401; logs sem telefone completo. Testes existentes atualizados (now expect 401 / added critical log check). Isolamento de env vars corrigido em `test_cancel_safe.py` e `test_webhook_resilience.py`.
- **Arquivos envolvidos:** `tests/test_main_webhook.py`, `tests/test_cancel_safe.py`, `tests/test_webhook_resilience.py`.
- **Critério de conclusão:** CA-001..CA-005 verdes.
- **Dependências:** T-002, T-003, T-004.
- **Estimativa:** Média.

### [x] T-013 — Testes de regressão do painel e calendário (AD-01..AD-06)
- **Descrição:** 8 novos testes em `tests/test_admin.py`: produção sem chave → 503; placeholder → 503; chave forte → 200/401; fora de produção → 200; deletar consulta → ok=False; data passada → 422; erro Calendar → campo error genérico. `FakeCalendarService` atualizado com `event_is_day_block` e `_get_service`.
- **Arquivos envolvidos:** `tests/test_admin.py`.
- **Critério de conclusão:** CA-006..CA-013 verdes.
- **Dependências:** T-005, T-006, T-007, T-008, T-009, T-010.
- **Estimativa:** Grande.

---

## Fase 4 — Documentação

### [x] T-014 — Atualizar relatório técnico e marcar progresso
- **Descrição:** tasks.md e spec.md atualizados. README.md na raiz não tem seção de autenticação a atualizar — nova política descrita na seção Deploy. 272 testes verdes.
- **Arquivos envolvidos:** `implementações/012 - Seguranca do Webhook e Painel Admin/tasks.md`.
- **Critério de conclusão:** Progresso atualizado.
- **Dependências:** T-012, T-013.
- **Estimativa:** Pequena.

---

## Registro de Progresso

| Tarefa | Descrição | Findings | Fase | Estimativa | Status |
|---|---|---|---|---|---|
| T-001 | Confirmar pontos de mudança e baseline | Todos | Preparação | Pequena | [x] 2026-06-15 |
| T-002 | Fechar webhook (401 com chave) | WE-03 | Implementação | Média | [x] 2026-06-15 |
| T-003 | Chave só via header/query | WE-09 | Implementação | Pequena | [x] 2026-06-15 |
| T-004 | Redigir PII/payload nos logs | CO-08, WE-09 | Implementação | Média | [x] 2026-06-15 |
| T-005 | Chave forte + detecção de produção | AD-01 | Implementação | Média | [x] 2026-06-15 |
| T-006 | Proteger exclusão no endpoint | AD-02 | Implementação | Média | [x] 2026-06-15 |
| T-007 | Defesa em profundidade no método | AD-02 | Implementação | Média | [x] 2026-06-15 |
| T-008 | try/except nos endpoints | AD-03 | Implementação | Média | [x] 2026-06-15 |
| T-009 | Recusar data no passado | AD-04 | Implementação | Pequena | [x] 2026-06-15 |
| T-010 | Sanear campo error | AD-06 | Implementação | Pequena | [x] 2026-06-15 |
| T-011 | .env.example + docs de ambiente | AD-01 | Implementação | Pequena | [x] 2026-06-15 |
| T-012 | Testes de regressão do webhook | WE-03, WE-09, CO-08 | Testes | Média | [x] 2026-06-15 |
| T-013 | Testes de regressão do painel/calendário | AD-01..AD-06 | Testes | Grande | [x] 2026-06-15 |

**Total:** 13 tarefas | **Concluídas:** 13 | **Progresso:** 100%
