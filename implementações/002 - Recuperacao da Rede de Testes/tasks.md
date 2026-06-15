# Tarefas: Recuperação da Rede de Testes

> **Implementação:** 002 - Recuperação da Rede de Testes
> **Spec:** [spec.md](./spec.md)
> **Progresso:** 0/12 tarefas concluídas (0%)
> **Última atualização:** 2026-06-15

Legenda: [ ] Pendente, [x] Concluída, [!] Bloqueada, [-] Cancelada.

Tarefas agrupadas em Fases: **Preparação → Implementação → Testes → Documentação**.

---

## Fase 1 — Preparação

### [ ] T-001 — Estabelecer linha de base da suíte e mapear falhas
- **Descrição:** Rodar `C:/Apps/WPP-DENTAL/.venv/Scripts/python.exe -m pytest -q` e registrar a linha de base (atual: 67 failed, 114 passed, 1 warning). Mapear cada falha ao módulo morto correspondente (`conversation_workflow_service`, `agent_conversation_service`, `langgraph_conversation_service`, `orchestration.dental_crew`) e classificar cada arquivo de teste como "re-apontar" ou "remover".
- **Arquivos envolvidos:** `tests/` (todos), `pytest.ini`.
- **Critério de conclusão:** Lista de testes com classificação (vivo→re-apontar / morto→remover) e mapa falha→módulo registrada na descrição do PR/commit.
- **Dependências:** Implementação 001 concluída.
- **Estimativa:** Pequena.

### [ ] T-002 — Confirmar pontos de mock (LLM e CalendarService) e contratos
- **Descrição:** Confirmar no código os pontos de mock: `CleanAgentService._llm.invoke` (`clean_agent_service.py:290,295`) e `calendar_tool.CalendarService` (padrão em `tests/test_calendar_tool.py:32,60`). Confirmar nomes de tools (`calendar_tool.py:152,243,335,380,452`) e o ponto `dental_crew = CleanAgentService()` (`app.py:114`).
- **Arquivos envolvidos:** `src/application/services/clean_agent_service.py`, `src/interfaces/tools/calendar_tool.py`, `src/interfaces/http/app.py`, `tests/test_calendar_tool.py`.
- **Critério de conclusão:** Documento curto (no PR) com os hooks de mock e o contrato de `AIMessage`/`ToolMessage` confirmados.
- **Dependências:** T-001.
- **Estimativa:** Pequena.

---

## Fase 2 — Implementação (correções)

### [ ] T-003 — [CORREÇÃO TE-03/EN-01] Limpar import quebrado em `orchestration/__init__.py`
- **Descrição:** Remover `from .dental_crew import DentalCrew` (`__init__.py:3`) e ajustar/remover `__all__` (linha 5), pois `dental_crew.py` não existe no diretório. Garantir que `import src.application.orchestration` deixe de lançar.
- **Arquivos envolvidos:** `src/application/orchestration/__init__.py`.
- **Critério de conclusão:** `python -c "import src.application.orchestration"` executa sem erro (atende CA-001).
- **Dependências:** T-001.
- **Estimativa:** Pequena.

### [ ] T-004 — [CORREÇÃO TE-01/EN-05] Re-apontar `test_agent_scenarios.py` para `CleanAgentService`
- **Descrição:** Substituir o import `from src.application.services.agent_conversation_service import AgentConversationService` (`:455`) em `run_scenario` pelo motor real `CleanAgentService`, ajustando o setup de estado/mocks dos cenários (saudação, plano, procedimento, clínico, idade, erro de digitação, confirmação, consulta) para exercitar o agente atual com LLM e CalendarService mockados.
- **Arquivos envolvidos:** `tests/test_agent_scenarios.py`.
- **Critério de conclusão:** Todos os cenários do arquivo coletam e passam contra `CleanAgentService`; nenhuma referência a `agent_conversation_service`.
- **Dependências:** T-002, T-003.
- **Estimativa:** Grande.

### [ ] T-005 — [CORREÇÃO TE-01/EN-05] Re-apontar ou remover testes de `conversation_workflow_service`
- **Descrição:** Em `test_conversation_workflow_service.py` (24 imports lazy) e `test_conversation_context_validation.py:165`, para cada teste decidir: se o comportamento ainda existe no motor atual, migrar a asserção para `CleanAgentService` (preferir consolidar em `test_clean_agent_service.py`); se for obsoleto, remover. Não deixar nenhuma referência a `conversation_workflow_service`.
- **Arquivos envolvidos:** `tests/test_conversation_workflow_service.py`, `tests/test_conversation_context_validation.py`.
- **Critério de conclusão:** Busca por `conversation_workflow_service` em `tests/` retorna zero; testes remanescentes verdes.
- **Dependências:** T-002, T-003.
- **Estimativa:** Grande.

### [ ] T-006 — [CORREÇÃO TE-01/EN-05] Remover testes de arquitetura morta (LangGraph e orchestration.dental_crew)
- **Descrição:** Remover `tests/test_langgraph_conversation_service.py` (importa `langgraph_conversation_service` inexistente, `:54,92,125`) e `tests/test_dental_crew_langgraph.py` (importa `orchestration.dental_crew` inexistente, `:27,50`). Confirmar que não há comportamento equivalente vivo a migrar.
- **Arquivos envolvidos:** `tests/test_langgraph_conversation_service.py`, `tests/test_dental_crew_langgraph.py`.
- **Critério de conclusão:** Arquivos removidos; busca por `langgraph_conversation_service` e `orchestration.dental_crew` em `tests/` retorna zero.
- **Dependências:** T-001.
- **Estimativa:** Pequena.

### [ ] T-007 — [CORREÇÃO TE-07] Endurecer `pytest.ini` e mover imports lazy para o topo
- **Descrição:** Adicionar `addopts = --strict-markers --strict-config -ra` à `pytest.ini`. Onde fizer sentido (módulos existentes e estáveis), mover imports de teste do interior das funções para o topo do arquivo, para que problemas de import apareçam na coleta. Rodar `pytest --collect-only` antes de habilitar strict.
- **Arquivos envolvidos:** `pytest.ini`, `tests/` (arquivos com imports lazy remanescentes).
- **Critério de conclusão:** `pytest.ini` contém os `addopts`; `pytest --collect-only` sem erro (atende CA-006, CA-003).
- **Dependências:** T-004, T-005, T-006.
- **Estimativa:** Média.

### [ ] T-008 — [CORREÇÃO TE-07] Eliminar PytestCollectionWarning de `TestResult`
- **Descrição:** Renomear o `@dataclass TestResult` (`test_agent_scenarios.py:427`) para `ScenarioResult` (ou adicionar `__test__ = False`), atualizando todos os usos (ex.: retorno de `run_scenario`). Eliminar o `PytestCollectionWarning: cannot collect test class 'TestResult'`.
- **Arquivos envolvidos:** `tests/test_agent_scenarios.py`.
- **Critério de conclusão:** `pytest -q` reporta 0 warnings de coleta (atende CA-007).
- **Dependências:** T-004.
- **Estimativa:** Pequena.

---

## Fase 3 — Testes (criação e regressão)

### [ ] T-009 — [TESTE TE-02] Criar `tests/test_clean_agent_service.py` (comportamental)
- **Descrição:** Criar o teste do motor de produção mockando `service._llm.invoke` (roteiro de `AIMessage` com/sem `tool_calls`) e `calendar_tool.CalendarService`. Cobrir: (a) escolha de tool; (b) oferta de exatamente 2 slots; (c) recusa de procedimento não realizado; (d) encaminhamento de convênio referral (`_has_valid_direct_plan`→False); (e) validação de slot ofertado — `criar_agendamento` fora de `offered_*` injeta "não estava entre os ofertados" (`clean_agent_service.py:326-331`) e não cria evento.
- **Arquivos envolvidos:** `tests/test_clean_agent_service.py` (novo), `src/application/services/clean_agent_service.py`, `src/interfaces/tools/calendar_tool.py`.
- **Critério de conclusão:** Os 5 cenários passam sem acessar OpenAI/Google reais (atende CA-004, CA-009).
- **Dependências:** T-002, T-003.
- **Estimativa:** Grande.

### [ ] T-010 — [TESTE TE-02] Testes unitários das funções puras + casos de borda do `_run_loop`
- **Descrição:** Testar diretamente `_parse_offered_slots` (`:44`), `_is_offered_slot` (`:52`), `_apply_state_slot_filters` (`:91`); e os casos de borda do `_run_loop`: anti-loop (`:313-315`), tool inexistente (`:351`), bloqueio por nome/plano ausente (`:341-347`) e `RuntimeError` de resposta vazia (`:427`). Isolar estado por telefone em `ConversationStateService`.
- **Arquivos envolvidos:** `tests/test_clean_agent_service.py`, `src/application/services/clean_agent_service.py`, `src/application/services/conversation_state_service.py`.
- **Critério de conclusão:** Todos os casos de borda da spec (6.4) cobertos e verdes.
- **Dependências:** T-009.
- **Estimativa:** Média.

### [ ] T-011 — [TESTE TE-05] Testes de regressão dos bugs #0002..#0005 no nível do agente
- **Descrição:** Adicionar regressão por bug: (#0002/#0003) remarcação consistente — ao final apenas 1 evento ativo, sem "sucesso silencioso" em falha parcial, sem agendar antes de fixar o event_id antigo (regras do system prompt `clean_agent_service.py:233-235`); (#0004) hand-off encaminha corretamente; (#0005) marcação só em slot ofertado/disponível. Usar `CleanAgentService` com mocks ou os pontos já cobertos em `test_calendar_tool.py`/`test_main_webhook.py`, conforme o nível adequado.
- **Arquivos envolvidos:** `tests/test_clean_agent_service.py`, `tests/test_main_webhook.py`, `tests/test_calendar_tool.py`.
- **Critério de conclusão:** Um teste de regressão por bug (#0002, #0003, #0004, #0005) passando (atende CA-005).
- **Dependências:** T-009.
- **Estimativa:** Grande.

---

## Fase 4 — Documentação e fechamento

### [ ] T-012 — Validar suíte verde e atualizar documentação de progresso
- **Descrição:** Rodar `C:/Apps/WPP-DENTAL/.venv/Scripts/python.exe -m pytest -q` e confirmar **0 failed** e 0 warnings de coleta, cobrindo webhook/agente/calendar. Atualizar o cabeçalho de progresso deste arquivo e o status da spec (🟡 → ✅ quando aplicável). Registrar a comparação antes/depois (67 failed → 0 failed).
- **Arquivos envolvidos:** `implementações/002 - Recuperacao da Rede de Testes/spec.md`, `implementações/002 - Recuperacao da Rede de Testes/tasks.md`.
- **Critério de conclusão:** `pytest -q` retorna 0 failed (atende CA-008); docs atualizados.
- **Dependências:** T-001..T-011.
- **Estimativa:** Pequena.

---

## Registro de Progresso

| Tarefa | Descrição curta | Fase | Estimativa | Status |
|---|---|---|---|---|
| T-001 | Linha de base + mapa de falhas | Preparação | Pequena | [ ] Pendente |
| T-002 | Confirmar pontos de mock e contratos | Preparação | Pequena | [ ] Pendente |
| T-003 | Limpar import quebrado em orchestration | Implementação | Pequena | [ ] Pendente |
| T-004 | Re-apontar test_agent_scenarios → CleanAgentService | Implementação | Grande | [ ] Pendente |
| T-005 | Re-apontar/remover testes de workflow_service | Implementação | Grande | [ ] Pendente |
| T-006 | Remover testes de arquitetura morta (LangGraph/dental_crew) | Implementação | Pequena | [ ] Pendente |
| T-007 | Endurecer pytest.ini + imports no topo | Implementação | Média | [ ] Pendente |
| T-008 | Eliminar PytestCollectionWarning (TestResult) | Implementação | Pequena | [ ] Pendente |
| T-009 | Criar test_clean_agent_service.py (comportamental) | Testes | Grande | [ ] Pendente |
| T-010 | Unitários de funções puras + casos de borda | Testes | Média | [ ] Pendente |
| T-011 | Regressão dos bugs #0002..#0005 | Testes | Grande | [ ] Pendente |
| T-012 | Validar suíte verde + atualizar docs | Documentação | Pequena | [ ] Pendente |
