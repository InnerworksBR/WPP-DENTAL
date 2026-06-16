# Tarefas: Mensageria Confiável e Alertas

> **Implementação:** 009 - Mensageria Confiável e Alertas
> **Spec:** [spec.md](./spec.md)
> **Progresso:** 0/13 tarefas concluídas (0%)
> **Última atualização:** 2026-06-15

Legenda: [ ] Pendente, [x] Concluída, [!] Bloqueada, [-] Cancelada.

---

## Fase 1 — Preparação

### [ ] T-001 — Mapear pontos de chamada e padrão de falha existente
- **Descrição:** Confirmar e documentar todos os chamadores que ignoram retorno de entrega: `_send_scope_alert` (`app.py:1296-1308`), `_notify_doctor_of_processing_error` (`app.py:1478-1496`), tool `alertar_doutora` (`whatsapp_tool.py:61-82`), e os 3 ramos de `_handle_appointment_confirmation` (`app.py:1249,1261,1276`). Catalogar o padrão de referência `if not delivered: _mark_message_failed + raise 502` (`app.py:875-879`, `1201-1212`, `1519-1523`).
- **Arquivos envolvidos:** `src/interfaces/http/app.py`, `src/interfaces/tools/whatsapp_tool.py`.
- **Critério de conclusão:** Lista revisada de arquivo:linha de cada ponto a corrigir, anexada ao PR/spec.
- **Dependências:** Implementações 001 e 002 concluídas.
- **Estimativa:** Pequena.

### [ ] T-002 — Definir schema de persistência de alertas e flag de eco
- **Descrição:** Especificar a tabela `pending_alerts` (campos `id`, `doctor_phone`, `payload`, `reason`, `created_at`, `attempts`, `last_error`, `status`) e a coluna/flag `kind` (`bot_reply`/`doctor_alert`) em `outbound_messages`, com migração retrocompatível usando `connection.get_db`.
- **Arquivos envolvidos:** `src/infrastructure/persistence/connection.py`, `src/infrastructure/persistence/outbound_message_store.py`, (novo) `src/infrastructure/persistence/failed_alert_store.py`.
- **Critério de conclusão:** DDL e migração definidos; criação idempotente da tabela validada no startup local.
- **Dependências:** T-001.
- **Estimativa:** Média.

---

## Fase 2 — Implementação

### [ ] T-003 — Retry com backoff e timeout configurável no envio WhatsApp (WH-05)
- **Descrição:** Adicionar retry com backoff em `send_message` e `send_message_sync` para falhas transitórias (timeout, 5xx); timeout configurável via env (`WHATSAPP_SEND_TIMEOUT`, `WHATSAPP_SEND_RETRIES`). Manter retorno booleano e o `OutboundMessageStore.record` apenas em sucesso.
- **Arquivos envolvidos:** `src/infrastructure/integrations/whatsapp_service.py` (`:61-101`, `:103-136`).
- **Critério de conclusão:** Atende CA-005; backoff total ≤ ~10s (RNF-001).
- **Dependências:** T-001.
- **Estimativa:** Média.

### [ ] T-004 — `_format_phone` robusto com validação BR (WH-06)
- **Descrição:** Validar tamanho, DDD (2 dígitos) e 9º dígito antes de prefixar `55`; recusar (retorno `""` + `logger.error`) números inconsistentes. Preservar comportamento atual para `@lid` (`whatsapp_service.py:34`) e para número BR válido.
- **Arquivos envolvidos:** `src/infrastructure/integrations/whatsapp_service.py` (`:28-45`).
- **Critério de conclusão:** Atende CA-008 sem regredir `5513991198852`.
- **Dependências:** T-003.
- **Estimativa:** Pequena.

### [ ] T-005 — Verificação de entrega + persistência de alerta falho (WH-03) e fallback de template (WH-09)
- **Descrição:** Em `AlertService.send_alert`/`send_referral_alert`/`notify_patient_*`, verificar o retorno do envio; em `False`, gravar em `pending_alerts` e `logger.critical`. Antes de enviar, detectar placeholders crus de `get_message("alerts.to_doctor", ...)` (`alert_service.py:45`, `config_service.py:280`) e substituir por fallback legível.
- **Arquivos envolvidos:** `src/infrastructure/integrations/alert_service.py`, novo `failed_alert_store.py`, `src/infrastructure/config/config_service.py`.
- **Critério de conclusão:** Atende CA-001 e CA-009.
- **Dependências:** T-002, T-003.
- **Estimativa:** Média.

### [ ] T-006 — Validar `DOCTOR_PHONE` no startup (CO-01)
- **Descrição:** Em `lifespan` (`app.py:71-103`), após `ConfigService()`, validar `get_doctor_phone()` (formato BR). Se inválido/vazio (inclui caminho `${DOCTOR_PHONE}` não resolvido, `config_service.py:291`), emitir `logger.critical`. Comportamento estrito (abortar) opcional via env (`STRICT_DOCTOR_PHONE`). Adicionar helper de validação reutilizável.
- **Arquivos envolvidos:** `src/interfaces/http/app.py` (`lifespan`), `src/infrastructure/config/config_service.py` (`get_doctor_phone` `:288-293`).
- **Critério de conclusão:** Atende CA-002 (RNF-002 respeitado).
- **Dependências:** T-001.
- **Estimativa:** Pequena.

### [ ] T-007 — Corrigir `_handle_appointment_confirmation` (WE-02 / HO-03)
- **Descrição:** Nos 3 ramos (remarcar 1241-1255, confirmar 1257-1267, cancelar 1269-1282), verificar `delivered`; em falha aplicar `_mark_message_failed` + `raise HTTPException(502)` ANTES de `_mark_message_processed`/`ConversationStateService.clear`. No ramo cancelar, definir ordem canônica (ver §9 da spec) para não deixar evento removido sem aviso ao paciente.
- **Arquivos envolvidos:** `src/interfaces/http/app.py` (`:1229-1284`).
- **Critério de conclusão:** Atende CA-003.
- **Dependências:** T-001.
- **Estimativa:** Média.

### [ ] T-008 — Fechar ciclo de confirmação chamando `mark_patient_response` (WE-12 / CO-08)
- **Descrição:** Em cada ramo de `_handle_appointment_confirmation`, após entrega confirmada, chamar `AppointmentConfirmationService.mark_patient_response(event_id, appointment_start=..., status=..., response_text=text)` (`appointment_confirmation_service.py:174`) com `confirmed`/`cancelled`/`rescheduled`. Recuperar `appointment_start` do estado/metadata.
- **Arquivos envolvidos:** `src/interfaces/http/app.py` (`:1229-1284`), `src/application/services/appointment_confirmation_service.py`.
- **Critério de conclusão:** Atende CA-004 (status deixa de ser `'sent'`).
- **Dependências:** T-007.
- **Estimativa:** Pequena.

### [ ] T-009 — Reduzir fragmentação / idempotência de chunk em `_send_response` (WH-02)
- **Descrição:** Ajustar `_send_response`/`_split_response_messages` (`app.py:806-825`) para preferir envio em mensagem única quando viável, evitando reenvio do chunk1 numa reentrega do webhook; opcionalmente registrar progresso de chunk.
- **Arquivos envolvidos:** `src/interfaces/http/app.py` (`:806-825`).
- **Critério de conclusão:** Atende CA-006.
- **Dependências:** T-003.
- **Estimativa:** Média.

### [ ] T-010 — Eco não pode mascarar resposta manual da doutora (WH-04 / WH-08)
- **Descrição:** Em `_handle_outbound_message` (`app.py:405-423`) e `OutboundMessageStore.consume_recent_match` (`outbound_message_store.py:56-86`), usar a flag `kind` para ignorar registros de alerta (`doctor_alert`) ao decidir eco no canal da doutora; preferir match por `message_id` quando disponível e restringir o match por conteúdo para não capturar resposta manual.
- **Arquivos envolvidos:** `src/interfaces/http/app.py`, `src/infrastructure/persistence/outbound_message_store.py`, `src/infrastructure/integrations/whatsapp_service.py` (gravação com `kind`).
- **Critério de conclusão:** Atende CA-007 sem regredir detecção de eco legítimo.
- **Dependências:** T-002.
- **Estimativa:** Média.

---

## Fase 3 — Testes

### [ ] T-011 — Testes unitários de envio, formatação e alertas
- **Descrição:** Cobrir: `_format_phone` (`@lid`, BR válido, DDD inválido) — regressão WH-06; `send_message_sync` com 503→200 (WH-05); `AlertService.send_alert` com envio `False` gravando `pending_alerts`+log crítico (WH-03); template faltando variável sem placeholder cru (WH-09); validação de `DOCTOR_PHONE` (CO-01).
- **Arquivos envolvidos:** `tests/` (unitários de `whatsapp_service`, `alert_service`, `config_service`).
- **Critério de conclusão:** Cobre CA-001, CA-002, CA-005, CA-008, CA-009.
- **Dependências:** T-003, T-004, T-005, T-006.
- **Estimativa:** Média.

### [ ] T-012 — Testes de integração de confirmação, eco e startup
- **Descrição:** Regressão WE-02 (envio `False` em `_handle_appointment_confirmation` → estado preservado, evento não cancelado em silêncio, 502, `_mark_message_failed`); WE-12 (`mark_patient_response` muda status `sent`→`confirmed`); WH-04/WH-08 (resposta manual da doutora idêntica a alerta ativa hand-off); WH-02 (chunk2 falho não duplica chunk1 na reentrega); startup com `DOCTOR_PHONE` ausente → log crítico (CO-01).
- **Arquivos envolvidos:** `tests/` (integração do webhook `/webhook/message`, `_handle_appointment_confirmation`, `_handle_outbound_message`, `lifespan`).
- **Critério de conclusão:** Cobre CA-003, CA-004, CA-006, CA-007 e parte de CA-002.
- **Dependências:** T-007, T-008, T-009, T-010.
- **Estimativa:** Grande.

---

## Fase 4 — Documentação

### [ ] T-013 — Documentar configuração, ordem cancelar/enviar e reprocessamento de alertas
- **Descrição:** Atualizar README/CLAUDE e `.env.example` com `WHATSAPP_SEND_TIMEOUT`/`WHATSAPP_SEND_RETRIES`/`STRICT_DOCTOR_PHONE`; documentar decisão de ordem cancelar-vs-enviar (§9 da spec), a tabela `pending_alerts` e como reprocessá-la; marcar progresso desta implementação.
- **Arquivos envolvidos:** `README.md`/`CLAUDE.md`, `.env.example`, esta `tasks.md`.
- **Critério de conclusão:** Documentação revisada; tabela de progresso abaixo atualizada para 13/13.
- **Dependências:** T-001 a T-012.
- **Estimativa:** Pequena.

---

## Registro de Progresso

| Tarefa | Fase | Finding(s) | Status | Estimativa |
|--------|------|-----------|--------|------------|
| T-001 | Preparação | WH-03, WE-02 | [ ] Pendente | Pequena |
| T-002 | Preparação | WH-03, WH-08 | [ ] Pendente | Média |
| T-003 | Implementação | WH-05 | [ ] Pendente | Média |
| T-004 | Implementação | WH-06 | [ ] Pendente | Pequena |
| T-005 | Implementação | WH-03, WH-09 | [ ] Pendente | Média |
| T-006 | Implementação | CO-01 | [ ] Pendente | Pequena |
| T-007 | Implementação | WE-02 / HO-03 | [ ] Pendente | Média |
| T-008 | Implementação | WE-12 / CO-08 | [ ] Pendente | Pequena |
| T-009 | Implementação | WH-02 | [ ] Pendente | Média |
| T-010 | Implementação | WH-04, WH-08 | [ ] Pendente | Média |
| T-011 | Testes | WH-03, WH-05, WH-06, WH-09, CO-01 | [ ] Pendente | Média |
| T-012 | Testes | WE-02, WE-12, WH-02, WH-04, WH-08, CO-01 | [ ] Pendente | Grande |
| T-013 | Documentação | Todos | [ ] Pendente | Pequena |

**Total:** 0/13 tarefas concluídas (0%).
