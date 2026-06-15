# Tarefas: Cancelamento Seguro

> **Implementação:** 005 - Cancelamento Seguro
> **Spec:** [spec.md](./spec.md)
> **Progresso:** 0/12 tarefas concluídas (0%)
> **Última atualização:** 2026-06-15

Legenda: [ ] Pendente, [x] Concluída, [!] Bloqueada, [-] Cancelada.

---

## Fase 1 — Preparação

### T-001 — Mapear chamadores de `cancel_appointment` e contratos atuais
- [ ] Pendente
- **Descrição:** Confirmar por Grep todos os pontos que chamam `CalendarService.cancel_appointment` e como tratam o retorno. Documentar os dois chamadores conhecidos: `CancelAppointmentTool._run` (calendar_tool.py:432) e `_handle_appointment_confirmation` (app.py:1271). Levantar como o Google `HttpError` expoe o status (404/410) no ambiente do projeto.
- **Arquivos envolvidos:** `src/infrastructure/integrations/calendar_service.py` (531-544), `src/interfaces/tools/calendar_tool.py` (432), `src/interfaces/http/app.py` (1271).
- **Critério de conclusão:** Lista completa de chamadores e nota tecnica de como obter status do `HttpError` (ex.: `exc.resp.status`).
- **Dependências:** Nenhuma.
- **Estimativa:** Pequena.

### T-002 — Definir contrato `CancelResult` e classificacao de cancelamento
- [ ] Pendente
- **Descrição:** Especificar o tipo de retorno `CancelResult(cancelled, already_absent, error)` e a regra de classificacao de intencao de cancelamento (explícito vs ambíguo) reutilizando `AppointmentOfferService.is_affirmative_confirmation`/`_normalize` (domain/policies/appointment_offer_service.py:261/131).
- **Arquivos envolvidos:** `src/infrastructure/integrations/calendar_service.py`, `src/domain/policies/appointment_offer_service.py`.
- **Critério de conclusão:** Assinatura do `CancelResult` aprovada e lista de tokens/regra de ambiguidade definida (cobrindo "nao sei", "talvez", "ainda nao").
- **Dependências:** T-001.
- **Estimativa:** Pequena.

---

## Fase 2 — Implementação

### T-003 — (CA-06) `cancel_appointment` retorna resultado tipado e diferencia 404/410 de erro real
- [ ] Pendente
- **Descrição:** Alterar `CalendarService.cancel_appointment` (calendar_service.py:531-544) para retornar `CancelResult`. `delete` 2xx -> `cancelled=True`; `HttpError` 404/410 -> `cancelled=True, already_absent=True`; demais excecoes -> `cancelled=False, error=str(exc)` mantendo `logger.error` com `event_id`. `event_id` vazio -> `cancelled=False, error="event_id ausente"` sem chamar a API.
- **Arquivos envolvidos:** `src/infrastructure/integrations/calendar_service.py` (531-544).
- **Critério de conclusão:** Função retorna `CancelResult` cobrindo os 4 ramos; log preservado. Corrige RF-006.
- **Dependências:** T-002.
- **Estimativa:** Média.

### T-004 — (RNF-004) Atualizar chamadores para o novo retorno tipado
- [ ] Pendente
- **Descrição:** Ajustar `CancelAppointmentTool._run` (calendar_tool.py:432-440) e `_handle_appointment_confirmation` (app.py:1271) para consumir `CancelResult` em vez de booleano. Garantir que nenhum chamador trate o retorno como booleano cru.
- **Arquivos envolvidos:** `src/interfaces/tools/calendar_tool.py` (432-440), `src/interfaces/http/app.py` (1269-1282).
- **Critério de conclusão:** Ambos os chamadores compilam e usam `result.cancelled`/`result.error`. Atende RNF-004.
- **Dependências:** T-003.
- **Estimativa:** Pequena.

### T-005 — (CA-01) Exigir `event_id` na tool quando ha >1 consulta; remover inferencia por nome
- [ ] Pendente
- **Descrição:** Em `CancelAppointmentTool._run` (calendar_tool.py:405-419), remover o ramo que infere a consulta por substring fraca do nome (`patient_name_lower in summary`). Quando `len(events) > 1` e sem `event_id`, retornar instrucao para informar o `event_id` (via consultar_agendamento). Manter cancelamento direto apenas quando `len(events) == 1`.
- **Arquivos envolvidos:** `src/interfaces/tools/calendar_tool.py` (388-419).
- **Critério de conclusão:** Nenhum cancelamento por substring de nome; >1 consulta sem event_id nao cancela. Corrige RF-005.
- **Dependências:** T-004.
- **Estimativa:** Média.

### T-006 — (CA-07) Resposta coerente para `event_id` valido que nao bate no telefone
- [ ] Pendente
- **Descrição:** Em `CancelAppointmentTool._run` (calendar_tool.py:401-404), tornar a resposta coerente quando o `event_id` informado nao pertence aos eventos do telefone: mensagem clara orientando a usar `consultar_agendamento`, sem mensagem contraditória e sem cancelar nada. Tratar tambem o caso de erro real vindo de `CancelResult.error`.
- **Arquivos envolvidos:** `src/interfaces/tools/calendar_tool.py` (401-440).
- **Critério de conclusão:** Caminhos event_id-inexistente e erro real produzem mensagens distintas e coerentes. Corrige RF-007.
- **Dependências:** T-004.
- **Estimativa:** Pequena.

### T-007 — (CO-04) Exigir confirmacao explícita antes de cancelar no fluxo determinístico
- [ ] Pendente
- **Descrição:** Em `_handle_appointment_confirmation` (app.py:1269), substituir o gatilho por substring `"nao"` por classificacao de intencao. Respostas ambíguas ("nao sei", "talvez") devem pedir confirmacao explícita e salvar estado de "aguardando confirmacao de cancelamento"; cancelar somente apos confirmacao explícita.
- **Arquivos envolvidos:** `src/interfaces/http/app.py` (1269), `src/application/services/conversation_state_service.py`, `src/domain/policies/appointment_offer_service.py`.
- **Critério de conclusão:** "nao sei" nao cancela; cancelamento ocorre apenas com confirmacao explícita. Corrige RF-004.
- **Dependências:** T-002, T-004.
- **Estimativa:** Média.

### T-008 — (WE-01) Checar resultado, nao afirmar sucesso indevido e alertar a doutora
- [ ] Pendente
- **Descrição:** Em `_handle_appointment_confirmation` (app.py:1269-1282), capturar `result = CalendarService().cancel_appointment(event_id)`. Afirmar "Consulta cancelada com sucesso" apenas se `result.cancelled`. Com `event_id` vazio ou `result.error`, enviar mensagem neutra, NAO limpar estado e alertar a doutora via `_send_scope_alert`/`AlertService.send_alert` (alert_service.py:18). Limpar estado apenas em sucesso.
- **Arquivos envolvidos:** `src/interfaces/http/app.py` (1269-1282), `src/infrastructure/integrations/alert_service.py` (18-25).
- **Critério de conclusão:** Sucesso real/idempotente -> mensagem de sucesso + estado limpo; falha real/event_id vazio -> mensagem neutra + alerta + estado preservado. Corrige RF-001 e RF-003.
- **Dependências:** T-003, T-007.
- **Estimativa:** Média.

### T-009 — (WE-01) Registrar resposta do paciente via `mark_patient_response`
- [ ] Pendente
- **Descrição:** No branch de cancelamento de `_handle_appointment_confirmation`, chamar `AppointmentConfirmationService.mark_patient_response` (appointment_confirmation_service.py:173-198) com `event_id`, `appointment_start` obtido de `state.metadata[METADATA_START_KEY]` (appointment_confirmation_service.py:31/251), `status="cancelled"` em sucesso ou `status="cancel_failed"` em falha, e `reminder_type` adequado.
- **Arquivos envolvidos:** `src/interfaces/http/app.py` (1269-1282), `src/application/services/appointment_confirmation_service.py` (173-198).
- **Critério de conclusão:** Tabela `appointment_confirmations` reflete a resposta em todos os desfechos; no-op seguro quando faltam dados. Corrige RF-002.
- **Dependências:** T-008.
- **Estimativa:** Pequena.

---

## Fase 3 — Testes

### T-010 — Testes unitários de `cancel_appointment` (regressao CA-06)
- [ ] Pendente
- **Descrição:** Cobrir os ramos de `CancelResult`: 2xx, `HttpError` 404, `HttpError` 410, erro de rede/5xx/auth e `event_id` vazio, validando `cancelled`, `already_absent`, `error` e o log.
- **Arquivos envolvidos:** `tests/` (ex.: `tests/test_calendar_service_cancel.py`), `src/infrastructure/integrations/calendar_service.py`.
- **Critério de conclusão:** Testes cobrem CA-008 e CA-009 e passam.
- **Dependências:** T-003.
- **Estimativa:** Média.

### T-011 — Testes da tool de cancelamento (regressao CA-01 e CA-07)
- [ ] Pendente
- **Descrição:** Validar `CancelAppointmentTool._run`: 0 eventos, 1 evento sem event_id (cancela), 2 eventos sem event_id (instrucao, NAO cancela por nome), event_id valido (cancela esse), event_id inexistente para o telefone (mensagem coerente), erro real.
- **Arquivos envolvidos:** `tests/` (ex.: `tests/test_cancel_appointment_tool.py`), `src/interfaces/tools/calendar_tool.py`.
- **Critério de conclusão:** Testes cobrem CA-007 e CA-010 e passam.
- **Dependências:** T-005, T-006.
- **Estimativa:** Média.

### T-012 — Testes de integracao do fluxo de confirmacao (regressao WE-01 e CO-04)
- [ ] Pendente
- **Descrição:** Testar `_handle_appointment_confirmation` com estado real (`_build_confirmation_state`): cancelamento sucesso, falha real, event_id vazio, idempotente (404/410) e resposta ambígua. Verificar mensagem enviada, chamada a `cancel_appointment`, `mark_patient_response`, alerta apenas em falha real e limpeza de estado apenas em sucesso.
- **Arquivos envolvidos:** `tests/` (ex.: `tests/test_handle_appointment_confirmation_cancel.py`), `src/interfaces/http/app.py`.
- **Critério de conclusão:** Testes cobrem CA-001..CA-006 e passam.
- **Dependências:** T-008, T-009.
- **Estimativa:** Média.

---

## Fase 4 — Documentação

(Sem tarefa dedicada — a documentacao viva e esta spec/tasks; atualizar o "Registro de Progresso" ao concluir cada tarefa. N/A — sem documentos externos adicionais.)

---

## Registro de Progresso

| Tarefa | Fase | Finding | Estimativa | Status |
|---|---|---|---|---|
| T-001 | Preparação | — | Pequena | [ ] Pendente |
| T-002 | Preparação | CO-04 | Pequena | [ ] Pendente |
| T-003 | Implementação | CA-06 | Média | [ ] Pendente |
| T-004 | Implementação | — (RNF-004) | Pequena | [ ] Pendente |
| T-005 | Implementação | CA-01 | Média | [ ] Pendente |
| T-006 | Implementação | CA-07 | Pequena | [ ] Pendente |
| T-007 | Implementação | CO-04 | Média | [ ] Pendente |
| T-008 | Implementação | WE-01 | Média | [ ] Pendente |
| T-009 | Implementação | WE-01 | Pequena | [ ] Pendente |
| T-010 | Testes | CA-06 | Média | [ ] Pendente |
| T-011 | Testes | CA-01 / CA-07 | Média | [ ] Pendente |
| T-012 | Testes | WE-01 / CO-04 | Média | [ ] Pendente |

**Total:** 12 tarefas | **Concluídas:** 0 | **Progresso:** 0%
