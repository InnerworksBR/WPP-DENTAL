# Tarefas: Mensageria Confiável e Alertas

> **Implementação:** 009 - Mensageria Confiável e Alertas
> **Spec:** [spec.md](./spec.md)
> **Progresso:** 13/13 tarefas concluídas (100%)
> **Última atualização:** 2026-06-16

Legenda: [ ] Pendente, [x] Concluída, [!] Bloqueada, [-] Cancelada.

---

## Fase 1 — Preparação

### [x] T-001 — Mapear baseline e confirmar escopo
- **Descrição:** Confirmado via Read o comportamento atual de `_send_response`, `_format_phone`, `AlertService.send_alert`, `_handle_appointment_confirmation` e `lifespan`. 9 findings mapeados a pontos exatos de código.
- **Arquivos envolvidos:** `src/infrastructure/integrations/whatsapp_service.py`, `src/infrastructure/integrations/alert_service.py`, `src/interfaces/http/app.py`
- **Critério de conclusão:** Lista de pontos exatos confirmada.
- **Dependências:** Nenhuma
- **Estimativa:** Pequena

### [x] T-002 — Criar `pending_alerts` e `FailedAlertStore`
- **Descrição:** Adicionada tabela `pending_alerts` ao `_CREATE_TABLES` e migração `_ensure_column` para `kind` em `connection.py`. Criado `src/infrastructure/persistence/failed_alert_store.py` com `FailedAlertStore.record(...)`.
- **Arquivos envolvidos:** `src/infrastructure/persistence/connection.py`, `src/infrastructure/persistence/failed_alert_store.py`
- **Critério de conclusão:** Tabela criada; FailedAlertStore persiste.
- **Dependências:** T-001
- **Estimativa:** Pequena

---

## Fase 2 — Implementação

### [x] T-003 — (WH-05) Retry com backoff em `WhatsAppService`
- **Descrição:** Adicionado retry exponencial (2^attempt segundos, default 2 retries via `WHATSAPP_SEND_RETRIES`) a `send_message` (async) e `send_message_sync`. Cada tentativa falha logada como WARNING; falha final como ERROR.
- **Arquivos envolvidos:** `src/infrastructure/integrations/whatsapp_service.py`
- **Critério de conclusão:** CA-002 verde (35/35 testes passando).
- **Dependências:** T-001
- **Estimativa:** Média

### [x] T-004 — (WH-06) Validação de DDD e tamanho em `_format_phone`
- **Descrição:** `_format_phone` agora valida: comprimento 12 ou 13 dígitos após formatação; DDD (dígitos 2-3) entre 11 e 99. Inválido → `""` + `logger.warning`.
- **Arquivos envolvidos:** `src/infrastructure/integrations/whatsapp_service.py`
- **Critério de conclusão:** CA-003 verde.
- **Dependências:** T-001
- **Estimativa:** Pequena

### [x] T-005 — (WH-03, WH-09) Persistência de alertas falhos + kind
- **Descrição:** `AlertService` verifica retorno de `send_message_sync`. Se False: `FailedAlertStore.record(...)` + `logger.critical(...)`. Alertas enviados com `kind='doctor_alert'` via `_send_to_doctor()`. Pacientes continuam recebendo mensagens sem kind especial.
- **Arquivos envolvidos:** `src/infrastructure/integrations/alert_service.py`
- **Critério de conclusão:** CA-004 verde.
- **Dependências:** T-002, T-003
- **Estimativa:** Média

### [x] T-006 — (CO-01) Validação de `DOCTOR_PHONE` no startup
- **Descrição:** `lifespan` em `app.py` chama `config.get_doctor_phone()` após `ConfigService()`. Se vazio: `logger.critical` com instrução de configuração. Startup continua.
- **Arquivos envolvidos:** `src/interfaces/http/app.py`
- **Critério de conclusão:** CA-005 verde.
- **Dependências:** T-001
- **Estimativa:** Pequena

### [x] T-007 — (WE-02) Verificar `delivered` antes de `clear`/`cancel`
- **Descrição:** Todos os branches de `_handle_appointment_confirmation` agora verificam `delivered`: se False → `_mark_message_failed` + `HTTPException(502)`. `ConversationStateService.clear` movido para APÓS entrega bem-sucedida nos branches confirmar e cancel-success.
- **Arquivos envolvidos:** `src/interfaces/http/app.py`
- **Critério de conclusão:** CA-006 verde.
- **Dependências:** T-001
- **Estimativa:** Grande

### [x] T-008 — (WE-12) Chamar `mark_patient_response` nos branches faltantes
- **Descrição:** Branches "remarcar" e "confirmar" de `_handle_appointment_confirmation` agora chamam `AppointmentConfirmationService.mark_patient_response(...)` após entrega. Status: `"rescheduled"` e `"confirmed"` respectivamente.
- **Arquivos envolvidos:** `src/interfaces/http/app.py`
- **Critério de conclusão:** CA-007 e CA-008 verdes.
- **Dependências:** T-007
- **Estimativa:** Média

### [x] T-009 — (WH-02) Simplificar `_send_response` para mensagem única
- **Descrição:** `_send_response` substituído por envio único (sem split por `\n\n`). `_split_response_messages` removida. Elimina duplicação de chunk1 em retentativas de webhook.
- **Arquivos envolvidos:** `src/interfaces/http/app.py`
- **Critério de conclusão:** CA-001 verde.
- **Dependências:** T-001
- **Estimativa:** Pequena

### [x] T-010 — (WH-04, WH-08) Coluna `kind` em `outbound_messages`
- **Descrição:** Coluna `kind TEXT NOT NULL DEFAULT 'bot'` adicionada via `_ensure_column`. `OutboundMessageStore.record` aceita `kind` param. `WhatsAppService` propaga `kind`. `consume_recent_match` exclui `kind='doctor_alert'` do match por conteúdo.
- **Arquivos envolvidos:** `src/infrastructure/persistence/outbound_message_store.py`, `src/infrastructure/persistence/connection.py`, `src/infrastructure/integrations/whatsapp_service.py`, `src/infrastructure/integrations/alert_service.py`
- **Critério de conclusão:** CA-009 verde.
- **Dependências:** T-002
- **Estimativa:** Grande

---

## Fase 3 — Testes

### [x] T-011 — Testes unitários
- **Descrição:** 28 testes em `tests/test_messaging_impl009.py` cobrindo WH-02, WH-05, WH-06, WH-03, CO-01, WH-04/WH-08, WH-09. Todos passando.
- **Arquivos envolvidos:** `tests/test_messaging_impl009.py`
- **Critério de conclusão:** 28/28 passando.
- **Dependências:** T-003..T-010
- **Estimativa:** Grande

### [x] T-012 — Testes de integração
- **Descrição:** 7 testes em `tests/test_messaging_integration_impl009.py` cobrindo WE-02 (delivered check, clear guardado) e WE-12 (mark_patient_response chamado). Todos passando.
- **Arquivos envolvidos:** `tests/test_messaging_integration_impl009.py`
- **Critério de conclusão:** 7/7 passando; suíte completa 412/414 (2 pré-existentes).
- **Dependências:** T-007..T-010
- **Estimativa:** Grande

---

## Fase 4 — Documentação

### [x] T-013 — Atualizar documentação e status
- **Descrição:** Atualizado `spec.md` (status 🟢 Concluída), `tasks.md` (progresso 13/13), `implementações/README.md` (linha 009).
- **Arquivos envolvidos:** `implementações/009 - Mensageria Confiável e Alertas/spec.md`, `implementações/README.md`
- **Critério de conclusão:** Documentação reflete o comportamento implementado.
- **Dependências:** T-011, T-012
- **Estimativa:** Pequena

---

## Registro de Progresso

| Tarefa | Descrição | Findings | Fase | Estimativa | Status | Concluída |
|---|---|---|---|---|---|---|
| T-001 | Mapear baseline | Todos | Preparação | Pequena | [x] | 2026-06-16 |
| T-002 | pending_alerts + FailedAlertStore | WH-03 | Preparação | Pequena | [x] | 2026-06-16 |
| T-003 | (WH-05) Retry WhatsAppService | WH-05 | Implementação | Média | [x] | 2026-06-16 |
| T-004 | (WH-06) Phone validation | WH-06 | Implementação | Pequena | [x] | 2026-06-16 |
| T-005 | (WH-03/WH-09) Alert failure + kind | WH-03, WH-09 | Implementação | Média | [x] | 2026-06-16 |
| T-006 | (CO-01) DOCTOR_PHONE no startup | CO-01 | Implementação | Pequena | [x] | 2026-06-16 |
| T-007 | (WE-02) delivered check | WE-02, HO-03 | Implementação | Grande | [x] | 2026-06-16 |
| T-008 | (WE-12) mark_patient_response | WE-12, CO-08 | Implementação | Média | [x] | 2026-06-16 |
| T-009 | (WH-02) _send_response único | WH-02 | Implementação | Pequena | [x] | 2026-06-16 |
| T-010 | (WH-04/WH-08) kind column | WH-04, WH-08 | Implementação | Grande | [x] | 2026-06-16 |
| T-011 | Testes unitários (28 testes) | Todos | Testes | Grande | [x] | 2026-06-16 |
| T-012 | Testes de integração (7 testes) | WE-02, WE-12 | Testes | Grande | [x] | 2026-06-16 |
| T-013 | Documentação | — | Documentação | Pequena | [x] | 2026-06-16 |

> Total: 13 tarefas concluídas.
