# Tarefas: Guarda de Escopo Robusto

> **Implementação:** 008 - Guarda de Escopo Robusto
> **Spec:** [spec.md](./spec.md)
> **Progresso:** 15/15 tarefas concluídas (100%)
> **Última atualização:** 2026-06-16

Legenda: [ ] Pendente, [x] Concluída, [!] Bloqueada, [-] Cancelada.

---

## Fase 1 — Preparação

### [x] T-001 — Mapear e fixar baseline de comportamento do guard
- **Descrição:** Confirmar via Read/execução o comportamento atual de `classify_patient_message` (`scope_guard_service.py:110-146`), `response_is_safe` (`149-168`) e dos pontos de uso em `app.py` (`206-233`, `274-280`, `1311-1351`, `1229-1284`). Montar planilha/lista de entradas de baseline (vazamentos conhecidos + mensagens legítimas) para servir de gate de regressão.
- **Arquivos envolvidos:** `src/domain/policies/scope_guard_service.py`, `src/interfaces/http/app.py`, `src/interfaces/tools/config_tool.py`
- **Critério de conclusão:** Documento de baseline com casos SC-01..SC-06, AG-05, WE-07, CO-03 reproduzidos (estado atual: quais passam/vazam hoje).
- **Dependências:** Impl. 001, Impl. 002
- **Estimativa:** Pequena

### [x] T-002 — Definir listas/padrões alvo (preço, clínico, valor nu, ofuscação)
- **Descrição:** Especificar os novos regex/keywords para `_PRICE_PATTERNS` (plural/sinônimos), `_CLINICAL_PATTERNS` (sintomas comuns), padrão de "valor nu" para saída, e a estratégia de `_normalize` anti-ofuscação. Calibrar contra a baseline de T-001 evitando falsos positivos (ex.: horário "14h").
- **Arquivos envolvidos:** `src/domain/policies/scope_guard_service.py`
- **Critério de conclusão:** Lista revisada de padrões aprovada, com casos legítimos que NÃO devem casar mapeados (EB-03).
- **Dependências:** T-001
- **Estimativa:** Média

---

## Fase 2 — Implementação

### [x] T-003 — (SC-01) Inverter ordem de checagem em `response_is_safe`
- **Descrição:** Reordenar `response_is_safe` (`scope_guard_service.py:190-204`) para checar `_UNSAFE_RESPONSE_PATTERNS`, `_PROCEDURE_TERMS` e `_CLINICAL_PATTERNS` **antes** do `return True` por `_SAFE_RESPONSE_MARKERS`. O marcador seguro só confirma `True` quando nenhum padrão proibido casa.
- **Arquivos envolvidos:** `src/domain/policies/scope_guard_service.py`
- **Critério de conclusão:** Atende RF-001; CA-001 e CA-002 verdes.
- **Dependências:** T-002
- **Estimativa:** Pequena

### [x] T-004 — (SC-02) Ampliar `_PRICE_PATTERNS` para plural/sinônimos
- **Descrição:** Adicionado a `_PRICE_PATTERNS` (`scope_guard_service.py:19-28`): `\bprec[ao]s?\b`, `\bvalores?\b`, `\bquanto (e|sai|vai)\b`, `\btabela\s*(de\s*)?prec`, `\bta quanto\b`, `\bquanto sai\b`.
- **Arquivos envolvidos:** `src/domain/policies/scope_guard_service.py`
- **Critério de conclusão:** Atende RF-002; CA-003 verde.
- **Dependências:** T-002
- **Estimativa:** Pequena

### [x] T-005 — (SC-03) Detectar valor monetário "nu" e clínico fora da lista na saída
- **Descrição:** Estendido `_UNSAFE_RESPONSE_PATTERNS` com 3 padrões de valor nu: `(?:fica|custa|vai|sai|vale|cobra)\s+(?:em\s+)?[1-9]\d{2,}`, `\bsao\s+[1-9]\d{2,}`, `\buns?\s+[1-9]\d{2,}`. Limiar de 3+ dígitos evita confusão com horários.
- **Arquivos envolvidos:** `src/domain/policies/scope_guard_service.py`
- **Critério de conclusão:** Atende RF-003; CA-004 verde; EB-03 não regride.
- **Dependências:** T-003, T-002
- **Estimativa:** Média

### [x] T-006 — (SC-05) Ampliar `_CLINICAL_PATTERNS` com sintomas comuns
- **Descrição:** Adicionados 8 padrões a `_CLINICAL_PATTERNS`: ardenc, pus, abscesso, trinc, quebr, latej, pulsan, machuc.
- **Arquivos envolvidos:** `src/domain/policies/scope_guard_service.py`
- **Critério de conclusão:** Atende RF-005; CA-006 verde.
- **Dependências:** T-002
- **Estimativa:** Pequena

### [x] T-007 — (SC-06) Normalização agressiva anti-ofuscação em `_normalize`
- **Descrição:** Reforçado `_normalize` com dois passos SC-06: (1) colapsar 3+ chars repetidos → 1 (`"preçooo"` → `"preco"`); (2) colapsar 5+ letras isoladas com separadores (`"p r e c o"` → `"preco"`).
- **Arquivos envolvidos:** `src/domain/policies/scope_guard_service.py`
- **Critério de conclusão:** Atende RF-006; CA-007 verde.
- **Dependências:** T-002
- **Estimativa:** Média

### [x] T-008 — (SC-04) Reduzir falsos positivos de agendamento na classificação
- **Descrição:** Expandido `_SUPPORTED_OPERATIONAL_PROCEDURE_TERMS` para incluir todos os procedimentos de `_PROCEDURE_TERMS`, de modo que qualquer procedimento em contexto operacional (marc/agend/consulta/convenio/faz) retorne None.
- **Arquivos envolvidos:** `src/domain/policies/scope_guard_service.py`
- **Critério de conclusão:** Atende RF-004; CA-005 verde; EB-02 não regride.
- **Dependências:** T-004, T-006
- **Estimativa:** Média

### [x] T-009 — (AG-05) Não vazar cobertura/restrição ao paciente em `CheckPlanTool`
- **Descrição:** Substituído o bloco `"Restrições: ..."` / `"Estes procedimentos NÃO são cobertos"` por mensagem ao agente instruindo a solicitar carteirinha e avisar que a Dra. verificará a cobertura.
- **Arquivos envolvidos:** `src/interfaces/tools/config_tool.py`
- **Critério de conclusão:** Atende RF-007; CA-008 verde; caminho referral (EB-04) preservado.
- **Dependências:** T-001
- **Estimativa:** Média

### [x] T-010 — (WE-07) Preservar estado de agenda ao escalar
- **Descrição:** Condicionado `ConversationStateService.clear(phone)` em `_handle_scope_escalation` à ausência de estado de agenda ativo (pending_slot_date, pending_slot_time, pending_event_id, reschedule_event_id, intent=="reschedule"). O `clear` só é chamado se nenhum desses campos estiver ativo.
- **Arquivos envolvidos:** `src/interfaces/http/app.py`
- **Critério de conclusão:** Atende RF-008; CA-009 verde.
- **Dependências:** T-001
- **Estimativa:** Grande

### [x] T-011 — (CO-03) Fallback determinístico na confirmação ambígua
- **Descrição:** Substituído o `return None` ao final de `_handle_appointment_confirmation` por fallback que, quando `state.stage == CONFIRMATION_STAGE`, re-apresenta o pedido de confirmação com opções SIM/NAO/REMARCAR, retornando `JSONResponse({"status": "confirmation_reask"})`. Nunca cai no LLM.
- **Arquivos envolvidos:** `src/interfaces/http/app.py`
- **Critério de conclusão:** Atende RF-009; CA-010 verde.
- **Dependências:** T-001
- **Estimativa:** Média

---

## Fase 3 — Testes

### [x] T-012 — Testes unitários do guard (SC-01..SC-06, AG-05)
- **Descrição:** Implementados 64 testes em `tests/test_scope_guard_impl008.py` cobrindo CA-001..CA-011, EB-01..EB-05, AT-02, AT-03 e RNF-001. Todos os critérios de aceitação cobertos via parametrize e casos de borda explícitos.
- **Arquivos envolvidos:** `tests/test_scope_guard_impl008.py`
- **Critério de conclusão:** UT-01..UT-08 verdes; 64/64 passando.
- **Dependências:** T-003..T-009
- **Estimativa:** Média

### [x] T-013 — Testes de integração de fluxo (WE-07, CO-03)
- **Descrição:** Implementados 7 testes em `tests/test_scope_guard_integration_impl008.py`: 4 para WE-07 (lógica de preservação de estado) e 3 para CO-03 (`_handle_appointment_confirmation` com mocks de async).
- **Arquivos envolvidos:** `tests/test_scope_guard_integration_impl008.py`
- **Critério de conclusão:** IT-01..IT-03 verdes; CA-009 e CA-010 confirmados.
- **Dependências:** T-010, T-011
- **Estimativa:** Grande

### [x] T-014 — Aceitação e calibração de falsos positivos/negativos
- **Descrição:** Executados CA-001..CA-011; AT-02 (10 mensagens legítimas → 0 escalações); AT-03 (10 tentativas de vazamento → 100% bloqueadas); RNF-001 (500 chamadas < 1s). Suíte completa: 377/379 passando (2 são pré-existentes).
- **Arquivos envolvidos:** `tests/`
- **Critério de conclusão:** Todos CA verdes; taxas de falso positivo/negativo dentro do alvo.
- **Dependências:** T-012, T-013
- **Estimativa:** Média

---

## Fase 4 — Documentação

### [x] T-015 — Atualizar documentação e status da implementação
- **Descrição:** Atualizado `spec.md` (status 🟢 Concluída), `tasks.md` (progresso 15/15), `implementações/README.md` (linha 008).
- **Arquivos envolvidos:** `implementações/008 - Guarda de Escopo Robusto/spec.md`, `implementações/README.md`
- **Critério de conclusão:** Documentação reflete o comportamento implementado; tabela de progresso atualizada.
- **Dependências:** T-014
- **Estimativa:** Pequena

---

## Registro de Progresso

| Tarefa | Descrição | Findings | Fase | Estimativa | Status | Concluída |
|---|---|---|---|---|---|---|
| T-001 | Mapear baseline do guard | Todos | Preparação | Pequena | [x] | 2026-06-16 |
| T-002 | Definir listas/padrões alvo | SC-01..06, AG-05 | Preparação | Média | [x] | 2026-06-16 |
| T-003 | (SC-01) Inverter ordem `response_is_safe` | SC-01 | Implementação | Pequena | [x] | 2026-06-16 |
| T-004 | (SC-02) Ampliar `_PRICE_PATTERNS` | SC-02 | Implementação | Pequena | [x] | 2026-06-16 |
| T-005 | (SC-03) Detectar valor nu na saída | SC-03 | Implementação | Média | [x] | 2026-06-16 |
| T-006 | (SC-05) Ampliar `_CLINICAL_PATTERNS` | SC-05 | Implementação | Pequena | [x] | 2026-06-16 |
| T-007 | (SC-06) Normalização anti-ofuscação | SC-06 | Implementação | Média | [x] | 2026-06-16 |
| T-008 | (SC-04) Reduzir falsos positivos | SC-04 | Implementação | Média | [x] | 2026-06-16 |
| T-009 | (AG-05) Não vazar restrições em CheckPlanTool | AG-05 | Implementação | Média | [x] | 2026-06-16 |
| T-010 | (WE-07) Preservar estado ao escalar | WE-07 | Implementação | Grande | [x] | 2026-06-16 |
| T-011 | (CO-03) Fallback determinístico na confirmação | CO-03 | Implementação | Média | [x] | 2026-06-16 |
| T-012 | Testes unitários do guard (64 testes) | Todos | Testes | Média | [x] | 2026-06-16 |
| T-013 | Testes de integração WE-07/CO-03 (7 testes) | WE-07, CO-03 | Testes | Grande | [x] | 2026-06-16 |
| T-014 | Aceitação e calibração | Todos | Testes | Média | [x] | 2026-06-16 |
| T-015 | Documentação e status | — | Documentação | Pequena | [x] | 2026-06-16 |

> Total: 15 tarefas concluídas.
