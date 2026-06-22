# Tarefas: Orquestrador Determinístico

> **Implementação:** 016 - Orquestrador Determinístico
> **Spec:** [spec.md](./spec.md)
> **Progresso:** 3/13 tarefas concluídas (23%)
> **Última atualização:** 2026-06-22

---

## Legenda

- `[ ]` — Pendente · `[x]` — Concluída · `[!]` — Bloqueada · `[-]` — Cancelada

---

## Tarefas

### Fase 1: Preparação e Setup

- [x] **T-001:** Definir estados e transições
  - **Descrição:** Criar `flow/states.py` com o enum `FlowState` e a tabela de transições mapeando
    os `stage` atuais (`awaiting_name_for_slot_confirmation`, etc.) para estados explícitos.
  - **Arquivos envolvidos:** `src/application/flow/states.py`, `src/application/flow/__init__.py`
  - **Critério de conclusão:** Enum cobre todos os `stage` hoje usados; mapeamento documentado.
  - **Dependências:** 014, 015 concluídas
  - **Estimativa:** Média

- [x] **T-002:** Definir `OrchestratorResult` e `Effect`
  - **Descrição:** Estruturas de saída (texto, próximo estado, efeitos, status) com mapeamento dos
    `status` HTTP atuais.
  - **Arquivos envolvidos:** `src/application/flow/orchestrator.py`
  - **Critério de conclusão:** Tipos definidos; status mapeados 1:1 com os atuais.
  - **Dependências:** T-001
  - **Estimativa:** Pequena

### Fase 2: Implementação Core (migrar um handler por vez, suíte verde a cada passo)

- [x] **T-003:** Esqueleto do orquestrador + IDLE/saudação/coleta de intenção
  - **Descrição:** `handle()` monta `NluContext`, classifica (015) e trata IDLE → intenção.
  - **Arquivos envolvidos:** `src/application/flow/orchestrator.py`
  - **Critério de conclusão:** Mensagem inicial e roteamento de intenção funcionam por teste.
  - **Dependências:** T-002
  - **Estimativa:** Média

- [~] **T-004:** Coleta de nome e plano (migrar `_handle_pending_slot_name/_plan`)
  - **Descrição:** Estados PRECISA_NOME/PRECISA_PLANO com as validações atuais (nome válido, plano
    direto vs encaminhamento).
  - **Arquivos envolvidos:** `src/application/flow/orchestrator.py`, `src/interfaces/http/app.py`
  - **Critério de conclusão:** Testes de identidade/plano verdes via orquestrador.
  - **Dependências:** T-003
  - **Estimativa:** Média

- [ ] **T-005:** Oferta de horários estruturada (substituir `_parse_offered_slots`)
  - **Descrição:** OFERTANDO consome slots estruturados do `CalendarService` e grava no estado.
  - **Arquivos envolvidos:** `src/application/flow/orchestrator.py`
  - **Critério de conclusão:** Oferta sem regex em prosa; CA-004.
  - **Dependências:** T-003
  - **Estimativa:** Média

- [ ] **T-006:** Escolha e confirmação (migrar `_handle_offered_slot_selection`)
  - **Descrição:** AGUARDANDO_ESCOLHA/CONFIRMACAO + criação via `CalendarService` (idempotente).
  - **Arquivos envolvidos:** `src/application/flow/orchestrator.py`
  - **Critério de conclusão:** Fluxo feliz completo verde (CA-001).
  - **Dependências:** T-004, T-005
  - **Estimativa:** Grande

- [ ] **T-007:** Re-oferta reativa (migrar `_handle_reactive_reoffer` / impl 013)
  - **Descrição:** Recusa ampla + horário/dia específico re-ofertam corretamente.
  - **Arquivos envolvidos:** `src/application/flow/orchestrator.py`
  - **Critério de conclusão:** Testes do 013 verdes (ou migrados). CA-003.
  - **Dependências:** T-005, T-006
  - **Estimativa:** Média

- [ ] **T-008:** Cancelamento seguro (migrar `_handle_cancellation_intent` / impl 005)
  - **Descrição:** CANCELAR_CONFIRMACAO com confirmação real antes de cancelar.
  - **Arquivos envolvidos:** `src/application/flow/orchestrator.py`
  - **Critério de conclusão:** Testes de cancelamento seguro verdes.
  - **Dependências:** T-003
  - **Estimativa:** Média

- [ ] **T-009:** Remarcação atômica + parcial (migrar lógica das impls 000/006)
  - **Descrição:** REMARCAR_IDENTIFICAR_ANTIGA + troca atômica + alerta de remarcação parcial.
  - **Arquivos envolvidos:** `src/application/flow/orchestrator.py`
  - **Critério de conclusão:** Testes de remarcação atômica/parcial verdes (CA-002).
  - **Dependências:** T-006
  - **Estimativa:** Grande

- [ ] **T-010:** Religar o webhook ao orquestrador
  - **Descrição:** `receive_message` delega ao `orchestrator.handle`; remover os `_handle_*` migrados;
    aplicar `effects` (interação, alerta, handoff).
  - **Arquivos envolvidos:** `src/interfaces/http/app.py`
  - **Critério de conclusão:** Webhook usa o orquestrador; handlers migrados removidos.
  - **Dependências:** T-006, T-007, T-008, T-009
  - **Estimativa:** Grande

### Fase 3: Testes e Validação

- [ ] **T-011:** Testes de transição da FSM
  - **Descrição:** `test_orchestrator.py` cobrindo todas as transições e casos de borda da spec §6.4.
  - **Arquivos envolvidos:** `tests/test_orchestrator.py`
  - **Critério de conclusão:** Cobertura de transições completa, verde.
  - **Dependências:** T-010
  - **Estimativa:** Grande

- [ ] **T-012:** Adaptar testes de webhook e rodar suíte total
  - **Descrição:** Ajustar `test_main_webhook`/`test_webhook_state_flows` aos novos `status` quando
    necessário (migração consciente); suíte total verde.
  - **Arquivos envolvidos:** `tests/test_main_webhook.py`, `tests/test_webhook_state_flows.py`
  - **Critério de conclusão:** `pytest -q` verde (CA-005).
  - **Dependências:** T-011
  - **Estimativa:** Média

### Fase 4: Documentação e Finalização

- [ ] **T-013:** Atualizar status e índice
  - **Descrição:** Marcar CA, status 🟢 no `spec.md`, atualizar README. Avaliar necessidade de `016b`.
  - **Arquivos envolvidos:** `implementações/016 - Orquestrador Deterministico/spec.md`,
    `implementações/README.md`
  - **Critério de conclusão:** Índice e spec refletem a conclusão; commit na branch.
  - **Dependências:** T-012
  - **Estimativa:** Pequena

---

## Registro de Progresso

| Tarefa | Status | Data de Conclusão | Observações |
|--------|--------|-------------------|-------------|
| T-001  | ✅ Concluída | 2026-06-22 | `flow/states.py` (FlowState == stages atuais) |
| T-002  | ✅ Concluída | 2026-06-22 | `OrchestratorResult` + `Effect` + flag `handled` (deferimento) |
| T-003  | ✅ Concluída | 2026-06-22 | Esqueleto + `build_context` + roteamento via NLU |
| T-004  | 🔄 Em andamento | — | Scaffold de nome/plano + escalação; falta paridade total + wiring |
| T-005  | ⬜ Pendente | — | — |
| T-006  | ⬜ Pendente | — | — |
| T-007  | ⬜ Pendente | — | — |
| T-008  | ⬜ Pendente | — | — |
| T-009  | ⬜ Pendente | — | — |
| T-010  | ⬜ Pendente | — | — |
| T-011  | ⬜ Pendente | — | — |
| T-012  | ⬜ Pendente | — | — |
| T-013  | ⬜ Pendente | — | — |

---

> **📌 NOTA:** Se a Fase 2 ultrapassar 15 tarefas reais, dividir em `016b` antes de prosseguir.
