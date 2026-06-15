# Tarefas: Guarda de Escopo Robusto

> **Implementação:** 008 - Guarda de Escopo Robusto
> **Spec:** [spec.md](./spec.md)
> **Progresso:** 0/14 tarefas concluídas (0%)
> **Última atualização:** 2026-06-15

Legenda: [ ] Pendente, [x] Concluída, [!] Bloqueada, [-] Cancelada.

---

## Fase 1 — Preparação

### [ ] T-001 — Mapear e fixar baseline de comportamento do guard
- **Descrição:** Confirmar via Read/execução o comportamento atual de `classify_patient_message` (`scope_guard_service.py:110-146`), `response_is_safe` (`149-168`) e dos pontos de uso em `app.py` (`206-233`, `274-280`, `1311-1351`, `1229-1284`). Montar planilha/lista de entradas de baseline (vazamentos conhecidos + mensagens legítimas) para servir de gate de regressão.
- **Arquivos envolvidos:** `src/domain/policies/scope_guard_service.py`, `src/interfaces/http/app.py`, `src/interfaces/tools/config_tool.py`
- **Critério de conclusão:** Documento de baseline com casos SC-01..SC-06, AG-05, WE-07, CO-03 reproduzidos (estado atual: quais passam/vazam hoje).
- **Dependências:** Impl. 001, Impl. 002
- **Estimativa:** Pequena

### [ ] T-002 — Definir listas/padrões alvo (preço, clínico, valor nu, ofuscação)
- **Descrição:** Especificar os novos regex/keywords para `_PRICE_PATTERNS` (plural/sinônimos), `_CLINICAL_PATTERNS` (sintomas comuns), padrão de "valor nu" para saída, e a estratégia de `_normalize` anti-ofuscação. Calibrar contra a baseline de T-001 evitando falsos positivos (ex.: horário "14h").
- **Arquivos envolvidos:** `src/domain/policies/scope_guard_service.py`
- **Critério de conclusão:** Lista revisada de padrões aprovada, com casos legítimos que NÃO devem casar mapeados (EB-03).
- **Dependências:** T-001
- **Estimativa:** Média

---

## Fase 2 — Implementação

### [ ] T-003 — (SC-01) Inverter ordem de checagem em `response_is_safe`
- **Descrição:** Reordenar `response_is_safe` (`scope_guard_service.py:155-168`) para checar `_UNSAFE_RESPONSE_PATTERNS`, `_PROCEDURE_TERMS` e `_CLINICAL_PATTERNS` **antes** do `return True` por `_SAFE_RESPONSE_MARKERS`. O marcador seguro só confirma `True` quando nenhum padrão proibido casa.
- **Arquivos envolvidos:** `src/domain/policies/scope_guard_service.py`
- **Critério de conclusão:** Atende RF-001; CA-001 e CA-002 verdes; nenhuma resposta com conteúdo proibido é aprovada por conter marcador seguro.
- **Dependências:** T-002
- **Estimativa:** Pequena

### [ ] T-004 — (SC-02) Ampliar `_PRICE_PATTERNS` para plural/sinônimos
- **Descrição:** Adicionar a `_PRICE_PATTERNS` (`scope_guard_service.py:19-25`) cobertura de "precos", "valores", "tabela", "quanto sai", "quanto fica/é", mantendo precisão para não derrubar agendamento.
- **Arquivos envolvidos:** `src/domain/policies/scope_guard_service.py`
- **Critério de conclusão:** Atende RF-002; CA-003 verde.
- **Dependências:** T-002
- **Estimativa:** Pequena

### [ ] T-005 — (SC-03) Detectar valor monetário "nu" e clínico fora da lista na saída
- **Descrição:** Estender `_UNSAFE_RESPONSE_PATTERNS` (`scope_guard_service.py:81-88`) para reprovar valores nus em contexto de preço ("fica em 350", "são 350,00"), sem confundir com horários (EB-03).
- **Arquivos envolvidos:** `src/domain/policies/scope_guard_service.py`
- **Critério de conclusão:** Atende RF-003; CA-004 verde; EB-03 não regride.
- **Dependências:** T-003, T-002
- **Estimativa:** Média

### [ ] T-006 — (SC-05) Ampliar `_CLINICAL_PATTERNS` com sintomas comuns
- **Descrição:** Adicionar a `_CLINICAL_PATTERNS` (`scope_guard_service.py:71-80`) sintomas como "ardencia", "pus", "abscesso", "trincou/quebrou", "lateja/latejando", "pulsando", "machucou".
- **Arquivos envolvidos:** `src/domain/policies/scope_guard_service.py`
- **Critério de conclusão:** Atende RF-005; CA-006 verde.
- **Dependências:** T-002
- **Estimativa:** Pequena

### [ ] T-007 — (SC-06) Normalização agressiva anti-ofuscação em `_normalize`
- **Descrição:** Reforçar `_normalize` (`scope_guard_service.py:104-108`) para neutralizar separadores intercalados entre letras e repetição de caracteres, mantendo a remoção de acentos/caixa atual.
- **Arquivos envolvidos:** `src/domain/policies/scope_guard_service.py`
- **Critério de conclusão:** Atende RF-006; CA-007 verde; demais padrões continuam casando (sem regressão).
- **Dependências:** T-002
- **Estimativa:** Média

### [ ] T-008 — (SC-04) Reduzir falsos positivos de agendamento na classificação
- **Descrição:** Ajustar `classify_patient_message` (`scope_guard_service.py:129-144`) ampliando `_SUPPORTED_OPERATIONAL_PROCEDURE_TERMS`/`_SUPPORTED_OPERATIONAL_CONTEXT_PATTERNS` e exigindo intenção informativa explícita para escalar quando há contexto de marcar/agendar/consulta.
- **Arquivos envolvidos:** `src/domain/policies/scope_guard_service.py`
- **Critério de conclusão:** Atende RF-004; CA-005 verde; preço continua prevalecendo (EB-02).
- **Dependências:** T-004, T-006
- **Estimativa:** Média

### [ ] T-009 — (AG-05) Não vazar cobertura/restrição ao paciente em `CheckPlanTool`
- **Descrição:** Ajustar `CheckPlanTool._run` (`config_tool.py:43-61`) para que a mensagem destinada ao paciente não inclua "Restrições: ..." nem "Estes procedimentos NÃO são cobertos"; detalhe de cobertura restrito ao caminho de referral/alerta interno.
- **Arquivos envolvidos:** `src/interfaces/tools/config_tool.py`
- **Critério de conclusão:** Atende RF-007; CA-008 verde; caminho referral (EB-04) preservado.
- **Dependências:** T-001
- **Estimativa:** Média

### [ ] T-010 — (WE-07) Preservar estado de agenda ao escalar
- **Descrição:** Condicionar `ConversationStateService.clear(phone)` em `_handle_scope_escalation` (`app.py:1331`) à ausência de estado de agenda ativo (pending/reschedule/confirmação/slot); ou reordenar `receive_message` (`app.py:206-233`) para tratar esses handlers antes da escalação.
- **Arquivos envolvidos:** `src/interfaces/http/app.py`
- **Critério de conclusão:** Atende RF-008; CA-009 verde; nenhum outro fluxo determinístico regride.
- **Dependências:** T-001
- **Estimativa:** Grande

### [ ] T-011 — (CO-03) Fallback determinístico na confirmação ambígua
- **Descrição:** Substituir o `return None` de `_handle_appointment_confirmation` (`app.py:1284`) por um fallback determinístico (reapresentar o pedido de confirmação ou escalar com segurança), impedindo a queda no LLM durante `CONFIRMATION_STAGE`.
- **Arquivos envolvidos:** `src/interfaces/http/app.py`
- **Critério de conclusão:** Atende RF-009; CA-010 verde; `dental_crew.process_message` não é invocado nesse caminho.
- **Dependências:** T-001
- **Estimativa:** Média

---

## Fase 3 — Testes

### [ ] T-012 — Testes unitários do guard (SC-01..SC-06)
- **Descrição:** Implementar UT-01..UT-08 para `response_is_safe`, `classify_patient_message`, `_normalize` e `CheckPlanTool._run`, cobrindo cada finding. Incluir EB-01/EB-02/EB-05.
- **Arquivos envolvidos:** `tests/` (suíte do `ScopeGuardService` e do `config_tool`)
- **Critério de conclusão:** UT-01..UT-08 verdes; cobertura de regressão para SC-01, SC-03 e AG-05 (RNF-002).
- **Dependências:** T-003, T-004, T-005, T-006, T-007, T-008, T-009
- **Estimativa:** Média

### [ ] T-013 — Testes de integração de fluxo (WE-07, CO-03, ordem)
- **Descrição:** Implementar IT-01 (estado de reschedule sobrevive à escalação), IT-02 (confirmação ambígua não chama LLM — mock de `dental_crew.process_message`) e IT-03 (ordem de handlers).
- **Arquivos envolvidos:** `tests/` (testes de `receive_message`/handlers em `app.py`)
- **Critério de conclusão:** IT-01..IT-03 verdes; CA-009 e CA-010 confirmados em fluxo end-to-end simulado.
- **Dependências:** T-010, T-011
- **Estimativa:** Grande

### [ ] T-014 — Aceitação e calibração de falsos positivos/negativos
- **Descrição:** Rodar AT-01 (CA-001..CA-011), AT-02 (≥10 mensagens legítimas → 0 escalações), AT-03 (≥10 tentativas de vazamento → 100% bloqueadas) e RNF-001 (nenhuma chamada de rede no guard). Ajustar padrões se houver regressão.
- **Arquivos envolvidos:** `tests/`, `src/domain/policies/scope_guard_service.py`
- **Critério de conclusão:** Todos os CA verdes; taxas de falso positivo/negativo dentro do alvo (RNF-002/RNF-003).
- **Dependências:** T-012, T-013
- **Estimativa:** Média

---

## Registro de Progresso

| Tarefa | Descrição | Fase | Estimativa | Status |
|---|---|---|---|---|
| T-001 | Mapear e fixar baseline do guard | Preparação | Pequena | [ ] Pendente |
| T-002 | Definir listas/padrões alvo | Preparação | Média | [ ] Pendente |
| T-003 | (SC-01) Inverter ordem em `response_is_safe` | Implementação | Pequena | [ ] Pendente |
| T-004 | (SC-02) Ampliar `_PRICE_PATTERNS` | Implementação | Pequena | [ ] Pendente |
| T-005 | (SC-03) Detectar valor nu/clínico na saída | Implementação | Média | [ ] Pendente |
| T-006 | (SC-05) Ampliar `_CLINICAL_PATTERNS` | Implementação | Pequena | [ ] Pendente |
| T-007 | (SC-06) Normalização anti-ofuscação | Implementação | Média | [ ] Pendente |
| T-008 | (SC-04) Reduzir falsos positivos | Implementação | Média | [ ] Pendente |
| T-009 | (AG-05) Não vazar cobertura em `CheckPlanTool` | Implementação | Média | [ ] Pendente |
| T-010 | (WE-07) Preservar estado ao escalar | Implementação | Grande | [ ] Pendente |
| T-011 | (CO-03) Fallback na confirmação ambígua | Implementação | Média | [ ] Pendente |
| T-012 | Testes unitários do guard | Testes | Média | [ ] Pendente |
| T-013 | Testes de integração de fluxo | Testes | Grande | [ ] Pendente |
| T-014 | Aceitação e calibração | Testes | Média | [ ] Pendente |
