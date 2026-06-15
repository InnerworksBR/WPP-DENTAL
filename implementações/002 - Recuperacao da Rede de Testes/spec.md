# Recuperação da Rede de Testes

> **ID:** 002
> **Status:** 🟡 Planejada
> **Prioridade:** 🟠 Alta
> **Criada em:** 2026-06-15
> **Última atualização:** 2026-06-15
> **Autor:** Agente AI

---

## 1. Resumo Executivo

A suíte de testes do WPP-DENTAL está parcialmente quebrada: ao rodar `C:/Apps/WPP-DENTAL/.venv/Scripts/python.exe -m pytest -q` o resultado atual é **67 failed, 114 passed, 1 warning**. As 67 falhas têm uma causa única dominante: testes importam módulos de serviço que **não existem mais** no código (`conversation_workflow_service`, `agent_conversation_service`, `langgraph_conversation_service`) e o pacote `src/application/orchestration` está com import quebrado para `dental_crew` (módulo inexistente). Como esses imports são *lazy* (feitos dentro das funções de teste), o erro aparece como `ModuleNotFoundError` em tempo de execução, e não na coleta — o que esconde a real natureza da quebra.

Além disso, o **motor de produção** — `CleanAgentService` em `src/application/services/clean_agent_service.py`, instanciado como `dental_crew` em `src/interfaces/http/app.py:114` — **não possui nenhum teste comportamental**: o único teste de webhook (`tests/test_main_webhook.py`) faz mock de `process_message`, ou seja, nunca exercita a lógica real de escolha de tool, oferta de horários, recusa de procedimento ou validação de slot.

Esta implementação **restaura a rede de segurança ANTES de qualquer mudança de comportamento** (motivo de ser pré-requisito das implementações seguintes): re-aponta ou remove os testes obsoletos, conserta o pacote `orchestration`, cria um teste comportamental para `CleanAgentService` mockando LLM e `CalendarService`, adiciona testes de regressão para os bugs corrigidos nos commits #0002..#0005 e endurece a configuração do pytest. **Critério de saída: suíte verde (0 failed)** cobrindo webhook, agente e calendar.

## 2. Contexto e Motivação

### 2.1 Problema Atual

Estado real verificado (execução de `pytest -q` no working dir):

```
67 failed, 114 passed, 1 warning in ~26s
```

**Módulos referenciados que não existem** (confirmado via `ls src/application/services/` — só existem `appointment_confirmation_service.py`, `clean_agent_service.py`, `conversation_service.py`, `conversation_state_service.py`, `handoff_service.py`, `patient_service.py`):

| Módulo importado pelos testes | Existe? | Ocorrências (arquivo:linha) |
|---|---|---|
| `src.application.services.conversation_workflow_service` | ❌ Não | `tests/test_conversation_workflow_service.py` (24 imports lazy: linhas 30, 49, 69, 112, 155, 215, 275, 297, 327, 364, 386, 408, 436, 457, 482, 500, 521, 541, 560, 580, 603, 623) + `tests/test_conversation_context_validation.py:165` |
| `src.application.services.agent_conversation_service` | ❌ Não | `tests/test_agent_scenarios.py:455` (usado por todos os cenários via `run_scenario`) |
| `src.application.services.langgraph_conversation_service` | ❌ Não | `tests/test_langgraph_conversation_service.py:54, 92, 125` |
| `src.application.orchestration.dental_crew` | ❌ Não | `tests/test_dental_crew_langgraph.py:27, 50` |

**Pacote `orchestration` quebrado na importação** — `src/application/orchestration/__init__.py:3`:

```python
"""Orquestracao da aplicacao."""

from .dental_crew import DentalCrew   # <- dental_crew.py NÃO existe nesse diretório

__all__ = ["DentalCrew"]
```

`ls src/application/orchestration/` mostra apenas `__init__.py` e `__pycache__` — não há `dental_crew.py`. Qualquer `import src.application.orchestration` falha em cadeia.

**Imports lazy mascaram a quebra:** os testes fazem `from ... import X` *dentro* da função de teste (ex.: `test_conversation_workflow_service.py:30`), então o `pytest` coleta os testes sem erro e só falha ao executar — dando a impressão de "erro de runtime do código" em vez de "teste apontando para módulo morto".

**Motor de produção sem teste comportamental:** `CleanAgentService.process_message` (`clean_agent_service.py:391`) e o laço `_run_loop` (`clean_agent_service.py:292`) contêm a lógica crítica — validação de slot ofertado (`_is_offered_slot`, linha 52; bloqueio em `criar_agendamento`, linhas 319-331), validação de nome/plano antes de marcar (linhas 333-347), oferta de exatamente 2 horários (regra do system prompt, linha 230), encaminhamento de convênio referral (`_has_valid_direct_plan`, linha 75). Nada disso é testado: `tests/test_main_webhook.py` substitui o método inteiro com `monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)` (ex.: linha 72).

**Warning de coleta:** `tests/test_agent_scenarios.py:427` define um `@dataclass TestResult` cujo nome começa com `Test`, gerando `PytestCollectionWarning: cannot collect test class 'TestResult' because it has a __init__ constructor`.

**`pytest.ini` mínimo** (conteúdo real, `pytest.ini`):

```ini
[pytest]
testpaths = tests
python_files = test_*.py
```

Sem `addopts`, sem `--strict-markers`, sem `--strict-config`, sem tratamento de warnings.

### 2.2 Impacto do Problema

- **Sem rede de segurança:** mudar o comportamento do agente (queixas 2, 3 e 4 do dono — responde errado, foge do escopo, marca errado) sem testes verdes é cego. Qualquer correção pode regredir silenciosamente.
- **Sinal de saúde falso:** 67 falhas constantes dessensibilizam — ninguém distingue "quebrou de verdade agora" de "já estava vermelho". A suíte deixa de ser sinal.
- **Regressões reais possíveis:** bugs já corrigidos (remarcação #0002/#0003, hand-off #0004, LID #0005) não têm teste de regressão no nível do agente; podem voltar sem aviso.
- **Bloqueio de CI/entrega:** qualquer gate de qualidade que rode `pytest` falha sempre, inviabilizando automação de deploy.

### 2.3 Soluções Consideradas

| Solução | Prós | Contras | Decisão |
|---|---|---|---|
| **A. Re-apontar testes vivos para `clean_agent_service` e remover os obsoletos** | Preserva cobertura de comportamento ainda existente; alinha testes ao motor real; baixo risco | Exige ler cada teste e decidir caso a caso (vivo vs. morto) | ✅ **Escolhida** |
| B. Deletar todos os testes que falham | Suíte verde imediata | Joga fora cobertura legítima; mascara falta de testes; perde valor de regressão | ❌ Rejeitada |
| C. Recriar os módulos mortos (`conversation_workflow_service` etc.) como wrappers de `CleanAgentService` | Testes passam sem reescrita | Ressuscita arquitetura abandonada; cria código morto de produção; aumenta dívida | ❌ Rejeitada |
| D. `xfail`/`skip` em massa nos testes quebrados | Rápido; suíte "verde" | Esconde o problema; não restaura rede de segurança; viola critério de saída (0 failed real) | ❌ Rejeitada |
| E. Manter imports lazy | Sem mudança | Continua mascarando quebra na coleta; contraria boa prática | ❌ Rejeitada (parcial: mover para topo onde fizer sentido) |

## 3. Especificação Técnica

### 3.1 Visão Geral da Arquitetura

O motor único de produção é `CleanAgentService` (`clean_agent_service.py:279`): um LLM (`ChatOpenAI`, linha 286) com *function calling* e ferramentas determinísticas (`_build_tools`, linha 119). O histórico da conversa é o estado; as tools executam ações reais (Calendar, paciente, planos). Os serviços antigos baseados em *state machine* de strings (`conversation_workflow_service`, `agent_conversation_service`, `langgraph_conversation_service`) e o `orchestration.dental_crew` foram **removidos do código**, mas os testes ainda os referenciam.

Esta implementação alinha a camada de testes à arquitetura atual: testes passam a exercitar `CleanAgentService` e suas tools, e o pacote `orchestration` deixa de quebrar a importação. Nenhuma mudança de comportamento de produção é feita aqui (exceto a limpeza do `__init__.py` quebrado, que é puro saneamento de import).

### 3.2 Componentes Afetados

| Componente | Tipo | Ação | Descrição |
|---|---|---|---|
| `src/application/orchestration/__init__.py` | Código (prod) | Modificar | Remover `from .dental_crew import DentalCrew` e `__all__` correspondente (linhas 3 e 5) para o pacote voltar a importar |
| `tests/test_conversation_workflow_service.py` | Teste | Remover ou re-apontar | 24 imports de módulo inexistente; comportamento coberto migra para `test_clean_agent_service.py` quando ainda válido, senão remove |
| `tests/test_conversation_context_validation.py` | Teste | Re-apontar ou remover | `:165` importa `ConversationWorkflowService`; avaliar quais asserts ainda fazem sentido contra `CleanAgentService` |
| `tests/test_agent_scenarios.py` | Teste | Re-apontar + corrigir warning | `:455` importa `AgentConversationService`; re-apontar `run_scenario` para `CleanAgentService`; renomear/anotar `TestResult` (`:427`) para parar o `PytestCollectionWarning` |
| `tests/test_langgraph_conversation_service.py` | Teste | Remover | Importa serviço LangGraph inexistente; arquitetura LangGraph não existe mais → teste obsoleto |
| `tests/test_dental_crew_langgraph.py` | Teste | Remover | Importa `orchestration.dental_crew` inexistente; cobre fachada removida → obsoleto |
| `tests/test_clean_agent_service.py` | Teste | Criar | **Novo** teste comportamental do motor de produção (mock LLM + `CalendarService`) |
| `pytest.ini` | Config | Modificar | Adicionar `addopts = --strict-markers --strict-config -ra` e tratamento de warnings |
| `tests/test_main_webhook.py` | Teste | Inspecionar | Confirmar que continua verde; serve de referência de mock de `dental_crew.process_message` (ex.: linha 72) |

### 3.3 Interfaces e Contratos

- **Motor sob teste:** `CleanAgentService().process_message(patient_phone: str, patient_message: str, patient_name: str = "", history_text: str | None = None, is_first_message: bool | None = None) -> str` (`clean_agent_service.py:391-429`). Lança `RuntimeError("CleanAgent não produziu resposta.")` se a resposta vier vazia (linha 427).
- **Ponto de mock do LLM:** o atributo de instância `self._llm` (`clean_agent_service.py:290`), resultado de `llm.bind_tools(self._tools)`. O laço chama `self._llm.invoke(messages)` retornando um `AIMessage` (linha 295). Para mockar comportamento, substituir `_llm.invoke` por um *fake* que retorne `AIMessage` com/sem `tool_calls`.
- **Ponto de mock do Calendar:** as tools resolvem `CalendarService` via `src.interfaces.tools.calendar_tool.CalendarService`; o padrão validado no repo é `monkeypatch.setattr(calendar_tool, "CalendarService", FakeCalendarService)` (ver `tests/test_calendar_tool.py:32, 60`).
- **Nomes de tools** (contrato com o LLM, de `calendar_tool.py`): `buscar_horarios_disponiveis` (`:152`), `buscar_proximo_dia_disponivel` (`:243`), `criar_agendamento` (`:335`), `cancelar_agendamento` (`:380`), `consultar_agendamento` (`:452`); mais `verificar_convenio`/`listar_convenios` (config_tool) e `buscar_paciente`/`salvar_paciente`/`registrar_interacao` (patient_tool).
- **Estado da conversa:** `ConversationStateService.get(phone)` / `.save(phone, state)`; campos usados na validação de slot: `offered_date`, `offered_times`, `rejected_slots`, `excluded_dates`, `earliest_time`, `requested_weekday`, `plan_name` (ver `clean_agent_service.py:52-107`).

### 3.4 Modelos de Dados

N/A — justificativa: esta implementação não cria nem altera schema de banco, entidades de domínio ou contratos de API. Os únicos "modelos" tocados são objetos de teste (ex.: o `@dataclass TestResult` em `test_agent_scenarios.py:427`, que será renomeado para `ScenarioResult` ou marcado com `__test__ = False`), que não são dados de produção.

### 3.5 Fluxo de Execução

Fluxo do teste comportamental novo (`test_clean_agent_service.py`), exercitando `CleanAgentService._run_loop`:

1. Instancia `CleanAgentService()` (constrói tools reais; o LLM real não será usado).
2. `monkeypatch` em `calendar_tool.CalendarService` → `FakeCalendarService` determinística (slots fixos, eventos fixos).
3. `monkeypatch` em `service._llm.invoke` → *fake* que devolve uma sequência roteirizada de `AIMessage` (primeiro com `tool_calls`, depois com `content` final), simulando a decisão do modelo.
4. Chama `process_message(...)` com `history_text` controlado.
5. Asserta sobre: tool escolhida, número de horários ofertados (exatamente 2), recusa de procedimento não realizado, encaminhamento de convênio referral (`_has_valid_direct_plan` retornando `False`), e o bloqueio de slot não ofertado (`criar_agendamento` com `datetime_str` fora de `offered_*` deve injetar a `ToolMessage` de erro interno — `clean_agent_service.py:326-331` — e **não** criar o evento).

Fluxo de saneamento (orchestration): após editar `__init__.py`, `import src.application.orchestration` passa a não lançar; testes que dependiam de `orchestration.dental_crew` são removidos (não há substituto vivo).

### 3.6 Tratamento de Erros

- **Import morto:** ao re-apontar/remover testes, garantir que nenhum teste remanescente importe módulo inexistente. Critério: `pytest --collect-only` sem erros e execução sem `ModuleNotFoundError`.
- **Mock do LLM:** o *fake* de `_llm.invoke` deve respeitar o contrato de `AIMessage` (atributo `tool_calls` lista de dicts com `name`/`args`/`id`, e `content`), senão `_run_loop` (linhas 295-307) quebra. Cobrir o caminho de `tool_calls` vazio (retorno direto, linha 297-298) e o de proteção anti-loop (linhas 312-316).
- **`RuntimeError` de resposta vazia:** incluir caso de borda que verifica que resposta não-vazia não levanta erro (linha 426-427).
- **Strict config:** com `--strict-config`/`--strict-markers`, qualquer marker desconhecido ou erro de config passa a **falhar a coleta** — exige que a `pytest.ini` esteja consistente antes de habilitar.
- **Warnings como sinal:** o `PytestCollectionWarning` de `TestResult` deve sumir; a política de warnings (via `-ra` e, se desejado, `filterwarnings`) garante visibilidade sem transformar warning legado em erro inesperado nesta fase.

## 4. Requisitos

### 4.1 Requisitos Funcionais

- **RF-001 (TE-03 / EN-01):** O pacote `src/application/orchestration` DEVE importar sem erro. O import quebrado `from .dental_crew import DentalCrew` (`__init__.py:3`) DEVE ser removido/limpo.
- **RF-002 (TE-01 / EN-05):** Nenhum teste DEVE referenciar `conversation_workflow_service` (24+ ocorrências), `agent_conversation_service` (`test_agent_scenarios.py:455`), `langgraph_conversation_service` (3 ocorrências) ou `orchestration.dental_crew` (2 ocorrências). Cada teste DEVE ser re-apontado para `CleanAgentService` quando o comportamento ainda existe, ou removido quando obsoleto.
- **RF-003 (TE-01):** Testes cujo comportamento ainda existe no motor atual DEVEM exercitar `CleanAgentService` (não os módulos mortos), mantendo cobertura equivalente do comportamento ainda válido.
- **RF-004 (TE-02):** DEVE existir `tests/test_clean_agent_service.py` com testes comportamentais do motor de produção, mockando o LLM (`self._llm.invoke`) e `CalendarService`, cobrindo: (a) escolha de tool; (b) oferta de exatamente 2 slots; (c) recusa de procedimento não realizado; (d) encaminhamento de convênio referral; (e) validação de slot ofertado (bloqueio de `criar_agendamento` para horário fora de `offered_*`).
- **RF-005 (TE-05):** DEVEM existir testes de regressão no nível do agente para os bugs corrigidos nos commits #0002..#0005: remarcação consistente (sem evento duplicado / sem sucesso silencioso em falha parcial), hand-off, e marcação correta (slot ofertado/disponível).
- **RF-006 (TE-07):** A `pytest.ini` DEVE conter `addopts = --strict-markers --strict-config -ra`, e os warnings de coleta DEVEM ser tratados.
- **RF-007 (TE-07):** O `@dataclass TestResult` em `test_agent_scenarios.py:427` DEVE ser renomeado (ex.: `ScenarioResult`) ou marcado para não-coleta, eliminando o `PytestCollectionWarning`.
- **RF-008 (TE-07):** Onde fizer sentido, imports dos testes DEVEM ser movidos para o topo do arquivo (em vez de lazy dentro das funções), para que problemas de import apareçam na coleta.

### 4.2 Não-Funcionais

- **RNF-001 (Determinismo):** Os testes do agente NÃO DEVEM chamar a OpenAI real nem o Google Calendar real. LLM e `CalendarService` DEVEM ser mockados; a suíte DEVE rodar sem variáveis de ambiente de produção e sem rede.
- **RNF-002 (Velocidade):** A suíte completa DEVE rodar em tempo comparável ao atual (~25-30s) ou melhor; mocks substituem chamadas externas lentas.
- **RNF-003 (Reprodutibilidade):** O comando oficial é `C:/Apps/WPP-DENTAL/.venv/Scripts/python.exe -m pytest -q` e DEVE retornar **0 failed**.
- **RNF-004 (Isolamento):** Testes que tocam `ConversationStateService` DEVEM isolar estado por telefone (fixtures/teardown) para não vazar entre casos.
- **RNF-005 (Legibilidade):** Mocks e fakes DEVEM seguir o padrão já usado no repo (`FakeCalendarService` + `monkeypatch.setattr` como em `test_calendar_tool.py`).

### 4.3 Restrições

- Não introduzir comportamento novo de produção; única edição de produção permitida é limpar o import quebrado em `orchestration/__init__.py`.
- Não recriar os módulos mortos para fazer testes passarem (solução C rejeitada).
- Não usar `skip`/`xfail` em massa para mascarar falhas (solução D rejeitada).
- Plataforma Windows; usar sempre o interpretador `.venv/Scripts/python.exe` e caminhos absolutos.
- Esta implementação depende da 001 (correção da chave/erros de API que estabilizam o ambiente de execução).

## 5. Critérios de Aceitação

- [ ] **CA-001:** `import src.application.orchestration` não lança erro (RF-001).
- [ ] **CA-002:** Busca por `conversation_workflow_service`, `agent_conversation_service`, `langgraph_conversation_service`, `orchestration.dental_crew` em `tests/` não retorna nenhuma ocorrência (RF-002).
- [ ] **CA-003:** `pytest --collect-only` executa sem `ModuleNotFoundError` e sem erro de coleta (RF-002, RF-008).
- [ ] **CA-004:** `tests/test_clean_agent_service.py` existe e cobre os 5 cenários do RF-004 (escolha de tool, 2 slots, recusa de procedimento, referral, slot ofertado), todos passando (RF-004).
- [ ] **CA-005:** Existem testes de regressão para #0002..#0005 (remarcação, hand-off, marcação) no nível do agente, passando (RF-005).
- [ ] **CA-006:** `pytest.ini` contém `addopts = --strict-markers --strict-config -ra` (RF-006).
- [ ] **CA-007:** A execução de `pytest -q` reporta **0 warnings de coleta** (o `PytestCollectionWarning` de `TestResult` desapareceu) (RF-007).
- [ ] **CA-008:** `C:/Apps/WPP-DENTAL/.venv/Scripts/python.exe -m pytest -q` retorna **0 failed**, cobrindo webhook, agente e calendar (META / RNF-003).
- [ ] **CA-009:** A suíte roda sem acesso a OpenAI/Google reais (sem credenciais de produção) (RNF-001).

## 6. Plano de Testes

### 6.1 Unitários

- **Calendar/tools (já existentes, manter verdes):** `tests/test_calendar_tool.py`, `tests/test_calendar_rules.py`, `tests/test_appointment_offer_service.py`, `tests/test_appointment_confirmation_service.py`, `tests/test_phone_normalization.py`, `tests/test_scope_guard_service.py`, `tests/test_config.py`.
- **Funções puras de `clean_agent_service`:** testar diretamente `_parse_offered_slots` (linha 44), `_is_offered_slot` (linha 52) e `_apply_state_slot_filters` (linha 91) com entradas controladas — não precisam de LLM.

### 6.2 Integração

- **`tests/test_clean_agent_service.py` (novo):** `CleanAgentService` ponta-a-ponta com `_llm.invoke` e `CalendarService` mockados — exercita `_run_loop` (linha 292) de verdade: roteiro de `tool_calls` → `ToolMessage` → resposta final.
- **`tests/test_main_webhook.py` (existente):** mantém o nível HTTP verde; serve de referência do contrato `dental_crew.process_message`.

### 6.3 Aceitação

- **`tests/test_agent_scenarios.py` (re-apontado):** cenários de saudação, plano, procedimento, clínico, idade, erro de digitação, confirmação e consulta passam a rodar contra `CleanAgentService` (substituindo `AgentConversationService` em `run_scenario`, linha 455).
- **`tests/test_conversation_context_validation.py` (re-apontado/removido):** os 10 fluxos humanos (`[01-...]`..`[10-...]`) avaliados; os que ainda fazem sentido contra o motor atual são migrados, o resto removido.
- **Execução final:** `pytest -q` → 0 failed (CA-008).

### 6.4 Casos de Borda

- **Slot não ofertado:** `criar_agendamento` com `datetime_str` fora de `offered_date/offered_times` deve injetar a mensagem "Erro interno: o horário solicitado não estava entre os ofertados" (linhas 326-331) e **não** criar evento.
- **Nome/plano ausente:** `criar_agendamento` com `patient_name` vazio/igual ao telefone/sem plano direto válido deve injetar "Erro interno: antes de criar o agendamento, colete e valide o nome..." (linhas 341-347).
- **Anti-loop:** mesma tool com mesmos args duas vezes deve retornar a mensagem de "dificuldade interna" (linhas 313-315).
- **Tool inexistente:** `call["name"]` desconhecido retorna "Erro: ferramenta '...' não encontrada." (linha 351).
- **Resposta vazia:** garantir que `_run_loop` retornando vazio levanta `RuntimeError` (linha 427) — e que o caminho feliz não levanta.
- **Convênio referral:** `_has_valid_direct_plan` (linha 75) retornando `False` → fluxo de encaminhamento, sem agendar.
- **Estado isolado por telefone:** dois testes sequenciais com o mesmo telefone não devem vazar `offered_*`.

## 7. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Remover teste obsoleto que na verdade cobria comportamento ainda válido | Média | Alto | Antes de remover, ler o teste e checar se o comportamento existe em `CleanAgentService`/tools; se existir, re-apontar em vez de deletar |
| Mock do LLM não fiel ao contrato de `AIMessage` → testes "verdes" mas irreais | Média | Médio | Construir fakes a partir de `langchain_core.messages.AIMessage` reais com `tool_calls` no formato esperado por `_run_loop` (linha 307) |
| `--strict-config`/`--strict-markers` quebrar coleta por marker/config legado | Média | Médio | Habilitar strict só após sanear markers; rodar `pytest --collect-only` antes |
| Acoplamento dos testes a strings exatas de mensagens de erro internas | Média | Baixo | Assertar por substrings estáveis (ex.: "não estava entre os ofertados", "agendada com sucesso") já presentes no código |
| Vazamento de estado entre testes via `ConversationStateService` | Média | Médio | Fixtures de setup/teardown limpando estado por telefone; usar telefones distintos por caso |
| Testes do agente tentarem chamar OpenAI/Google reais por mock incompleto | Baixa | Alto | Mock obrigatório de `_llm.invoke` e `calendar_tool.CalendarService`; rodar sem credenciais para provar (CA-009) |
| Dependência 001 não concluída deixar ambiente instável | Média | Médio | Tratar 001 como pré-requisito formal (seção 8.1); não iniciar 002 antes de 001 verde |

## 8. Dependências

### 8.1 Internas

- **Implementação 001 — Estabilidade da API e Resiliência de IO (pré-requisito):** estabilização de erros/timeout/I-O. Necessária para que o ambiente de execução de testes seja confiável antes de reconstruir a rede de segurança.
- **`CleanAgentService`** (`src/application/services/clean_agent_service.py`) — alvo dos novos testes comportamentais.
- **Tools** (`src/interfaces/tools/calendar_tool.py`, `config_tool.py`, `patient_tool.py`) — contratos exercitados pelos mocks.
- **`ConversationStateService`** (`src/application/services/conversation_state_service.py`) — estado isolado nos testes.
- **`src/interfaces/http/app.py:114`** — `dental_crew = CleanAgentService()`, ponto de mock do webhook.

### 8.2 Externas

- **pytest** (já em uso; `pytest.ini` presente).
- **langchain-core / langchain-openai** — fornecem `AIMessage`/`ToolMessage`/`ChatOpenAI` usados no mock (importados em `clean_agent_service.py:15-17`).
- **Interpretador** `C:/Apps/WPP-DENTAL/.venv/Scripts/python.exe` (Windows).
- **OpenAI e Google Calendar:** **NÃO** devem ser acessados pelos testes (mockados) — dependência externa explicitamente neutralizada por design.

## 9. Observações e Decisões de Design

- **Por que 002 vem antes de mudar comportamento:** as queixas do dono (responde errado, foge do escopo, marca errado) só podem ser atacadas com segurança se houver testes verdes que travem o comportamento correto. Reconstruir a rede primeiro é decisão deliberada de sequenciamento.
- **Mover imports para o topo:** os imports lazy (`from ... import X` dentro da função de teste) mascararam a quebra como erro de runtime. Onde o módulo importado existir e for estável, mover ao topo faz problemas aparecerem na coleta — sinal mais cedo e mais honesto (RF-008).
- **`TestResult` → renomear:** o prefixo `Test` faz o pytest tentar coletar a `@dataclass` como classe de teste; renomear para `ScenarioResult` (ou `__test__ = False`) é a correção canônica do `PytestCollectionWarning` (linha 427).
- **Re-apontar vs. recriar:** optou-se por alinhar testes ao motor real (`CleanAgentService`) em vez de ressuscitar os serviços mortos. Isso evita reintroduzir arquitetura abandonada (state machine de strings / LangGraph) só para satisfazer testes.
- **Fidelidade do mock do LLM:** o roteiro de `AIMessage` deve refletir o que o modelo realmente faria (chamar tool de slots, depois responder), para que os testes validem o `_run_loop` de verdade — incluindo as guardas de slot ofertado e de nome/plano, que são o coração das queixas 3 e 4.
- **Padrão de mock do Calendar:** segue `tests/test_calendar_tool.py` (`monkeypatch.setattr(calendar_tool, "CalendarService", FakeCalendarService)`), mantendo consistência com o repo.
- **Escopo de remoção:** `test_langgraph_conversation_service.py` e `test_dental_crew_langgraph.py` cobrem fachadas que não existem mais (LangGraph e `orchestration.dental_crew`) e serão removidos; não há comportamento equivalente vivo a migrar.
