# Tarefas: Aposentar o Cérebro Duplo

> **Implementação:** 017 - Aposentar o Cérebro Duplo
> **Spec:** [spec.md](./spec.md)
> **Progresso:** 0/8 tarefas concluídas (0%)
> **Última atualização:** 2026-06-22

---

## Legenda

- `[ ]` — Pendente · `[x]` — Concluída · `[!]` — Bloqueada · `[-]` — Cancelada

---

## Tarefas

### Fase 1: Preparação e Setup

- [ ] **T-001:** Auditar usos remanescentes do `CleanAgentService`
  - **Descrição:** Após 016, mapear todos os pontos que ainda chamam o loop decisor e confirmar que
    a FSM cobre o comportamento.
  - **Arquivos envolvidos:** `src/interfaces/http/app.py`, `src/application/services/clean_agent_service.py`
  - **Critério de conclusão:** Lista de usos + confirmação de paridade pela FSM.
  - **Dependências:** 016 concluída
  - **Estimativa:** Pequena

### Fase 2: Implementação Core

- [ ] **T-002:** Criar `ReplyComposer` (tom)
  - **Descrição:** Composição de texto: template `messages.yaml` no caminho feliz; LLM só para livre/
    fora de escopo; fallback neutro se LLM indisponível.
  - **Arquivos envolvidos:** `src/application/render/reply_composer.py`
  - **Critério de conclusão:** Compõe texto para os tipos de `OrchestratorResult`.
  - **Dependências:** T-001
  - **Estimativa:** Média

- [ ] **T-003:** Remover o loop decisor e os guard-rails
  - **Descrição:** Apagar `_parse_offered_slots`, `_is_offered_slot`, bloqueio de remarcação,
    detector de loop e o `_run_loop`; remover/encolher `CleanAgentService`.
  - **Arquivos envolvidos:** `src/application/services/clean_agent_service.py`
  - **Critério de conclusão:** Nenhuma decisão de agenda via tool-call; guard-rails removidos.
  - **Dependências:** T-002
  - **Estimativa:** Média

- [ ] **T-004:** Avaliar as tools do LLM
  - **Descrição:** Decidir se `interfaces/tools/*` viram serviços diretos chamados pela FSM ou são
    removidas; registrar a decisão na spec §9.
  - **Arquivos envolvidos:** `src/interfaces/tools/*`
  - **Critério de conclusão:** Tools sem uso decisor; FSM chama serviços diretamente.
  - **Dependências:** T-003
  - **Estimativa:** Média

- [ ] **T-005:** Enxugar o `app.py`
  - **Descrição:** Reduzir a controlador fino: auth → idempotência/handoff/TTL → `orchestrator.handle`
    → `ReplyComposer` → `gateway.send_text`. Remover `_handle_*` de agenda remanescentes.
  - **Arquivos envolvidos:** `src/interfaces/http/app.py`
  - **Critério de conclusão:** `app.py` ≤ ~200 linhas (CA-003).
  - **Dependências:** T-003, T-004
  - **Estimativa:** Grande

### Fase 3: Testes e Validação

- [ ] **T-006:** Testes do `ReplyComposer` + escopo
  - **Descrição:** `test_reply_composer.py` (template, LLM mockado fora de escopo, fallback) e
    garantir `ScopeGuardService` (008) ainda valida respostas livres.
  - **Arquivos envolvidos:** `tests/test_reply_composer.py`
  - **Critério de conclusão:** CA-005 verde.
  - **Dependências:** T-002
  - **Estimativa:** Média

- [ ] **T-007:** Migrar/remover testes do "cérebro" e rodar suíte total
  - **Descrição:** Aposentar `test_clean_agent_service.py` (comportamento já coberto pela FSM em 016);
    suíte total verde.
  - **Arquivos envolvidos:** `tests/test_clean_agent_service.py`, suíte
  - **Critério de conclusão:** `pytest -q` verde (CA-004); sem perda de cobertura de comportamento.
  - **Dependências:** T-005, T-006
  - **Estimativa:** Média

### Fase 4: Documentação e Finalização

- [ ] **T-008:** Fechar o programa de refactor
  - **Descrição:** Status 🟢 no `spec.md`; atualizar README (programa de refactor concluído);
    registrar métricas finais (LOC do `app.py`, contagem de testes).
  - **Arquivos envolvidos:** `implementações/017 - Aposentar o Cerebro Duplo/spec.md`,
    `implementações/README.md`
  - **Critério de conclusão:** Índice e spec refletem a conclusão; commit na branch.
  - **Dependências:** T-007
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

> **📌 NOTA:** Só iniciar após 016 estável e verde. Remoções incrementais com a suíte como catraca.
