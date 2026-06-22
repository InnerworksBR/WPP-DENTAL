# Tarefas: NLU Estruturada

> **Implementação:** 015 - NLU Estruturada
> **Spec:** [spec.md](./spec.md)
> **Progresso:** 0/8 tarefas concluídas (0%)
> **Última atualização:** 2026-06-22

---

## Legenda

- `[ ]` — Pendente · `[x]` — Concluída · `[!]` — Bloqueada · `[-]` — Cancelada

---

## Tarefas

### Fase 1: Preparação e Setup

- [ ] **T-001:** Definir o schema de NLU
  - **Descrição:** Criar `nlu/schema.py` com `Intent` (enum), `Entities` e `NluResult` (Pydantic),
    e `NluContext` (oferta pendente, confirmação pendente, período/dia já pedidos).
  - **Arquivos envolvidos:** `src/application/nlu/schema.py`, `src/application/nlu/__init__.py`
  - **Critério de conclusão:** Modelos importam e validam exemplos.
  - **Dependências:** Nenhuma
  - **Estimativa:** Pequena

- [ ] **T-002:** Mapear o vocabulário do extrator atual
  - **Descrição:** Levantar de `extract_request_constraints` todas as entidades/sinais já suportados
    para garantir paridade no fallback e no schema.
  - **Arquivos envolvidos:** `src/domain/policies/appointment_offer_service.py` (leitura)
  - **Critério de conclusão:** Lista de paridade documentada na própria tarefa/spec.
  - **Dependências:** T-001
  - **Estimativa:** Pequena

### Fase 2: Implementação Core

- [ ] **T-003:** Classificação via LLM estruturado
  - **Descrição:** Implementar `IntentClassifier.classify` com `with_structured_output(NluResult)`,
    `temperature=0`, prompt enxuto que recebe a mensagem + `NluContext`.
  - **Arquivos envolvidos:** `src/application/nlu/intent_classifier.py`
  - **Critério de conclusão:** Retorna `NluResult` válido para frases de exemplo (LLM real ou mock).
  - **Dependências:** T-001
  - **Estimativa:** Média

- [ ] **T-004:** Fallback determinístico
  - **Descrição:** Mapear `extract_request_constraints` + heurísticas mínimas para `NluResult`,
    acionado quando o LLM falha/saída inválida.
  - **Arquivos envolvidos:** `src/application/nlu/intent_classifier.py`
  - **Critério de conclusão:** Com LLM mockado para falhar, produz `NluResult` útil.
  - **Dependências:** T-002, T-003
  - **Estimativa:** Média

- [ ] **T-005:** Desambiguação escolha-vs-restrição
  - **Descrição:** Usar `NluContext` para decidir "pode ser às 8" (escolher) vs "só às 8"
    (earliest_time), resolvendo o caso do bug 013.
  - **Arquivos envolvidos:** `src/application/nlu/intent_classifier.py`
  - **Critério de conclusão:** CA-004 verde.
  - **Dependências:** T-003, T-004
  - **Estimativa:** Média

### Fase 3: Testes e Validação

- [ ] **T-006:** Testes da NLU (intenções + entidades)
  - **Descrição:** `test_intent_classifier.py` parametrizado por frase real → resultado esperado.
  - **Arquivos envolvidos:** `tests/test_intent_classifier.py`
  - **Critério de conclusão:** CA-001..CA-004 verdes.
  - **Dependências:** T-003, T-004, T-005
  - **Estimativa:** Média

- [ ] **T-007:** Teste de paridade com o extrator atual
  - **Descrição:** Para um conjunto de inputs, comparar entidades do fallback com
    `extract_request_constraints`.
  - **Arquivos envolvidos:** `tests/test_intent_classifier.py`
  - **Critério de conclusão:** Paridade verde (CA-002).
  - **Dependências:** T-004
  - **Estimativa:** Pequena

### Fase 4: Documentação e Finalização

- [ ] **T-008:** Suíte total + status
  - **Descrição:** Rodar `pytest -q` (488 + novos verdes); atualizar `spec.md` (status 🟢) e o README.
  - **Arquivos envolvidos:** `implementações/015 - NLU Estruturada/spec.md`, `implementações/README.md`
  - **Critério de conclusão:** Suíte verde; índice atualizado; commit na branch.
  - **Dependências:** T-006, T-007
  - **Estimativa:** Pequena

---

## Registro de Progresso

| Tarefa | Status | Data de Conclusão | Observações |
|--------|--------|-------------------|-------------|
| T-001  | ⬜ Pendente | — | — |
| T-002  | ⬜ Pendente | — | — |
| T-003  | ⬜ Pendente | — | — |
| T-004  | ⬜ Pendente | — | — |
| T-005  | ⬜ Pendente | — | — |
| T-006  | ⬜ Pendente | — | — |
| T-007  | ⬜ Pendente | — | — |
| T-008  | ⬜ Pendente | — | — |

---

> **📌 NOTA:** Atualize este documento conforme as tarefas forem concluídas.
