# Configuração Resiliente e Limpeza de Engine

> **ID:** 011
> **Status:** 🟡 Planejada
> **Prioridade:** 🟠 Alta
> **Criada em:** 2026-06-15
> **Última atualização:** 2026-06-15
> **Autor:** Agente AI

---

## 1. Resumo Executivo

O `ConfigService` (`src/infrastructure/config/config_service.py`) é o ponto único de leitura de toda a configuração do WPP-DENTAL: planos/convênios (`config/plans.yaml`), mensagens ao paciente (`config/messages.yaml`), regras gerais e da clínica (`config/settings.yaml`) e regras de procedimento (`config/procedure_rules.yaml`). Ele é um singleton (`__new__`, linha 32-36) cujo dicionário `_configs` é compartilhado em nível de classe (`_configs: dict[str, Any] = {}`, linha 16) e, portanto, entre todas as threads da aplicação FastAPI.

Esta implementação ataca dois grupos de problemas reais e confirmados no código:

1. **Configuração frágil (findings CO-02 a CO-10):** o `reload()` (linha 96-99) limpa o dicionário compartilhado antes de recarregar (não-atômico → janela de config vazia e estado permanentemente vazio se o YAML estiver quebrado); o `_load_configs()` (linha 87-94) não tem `try/except` (YAML malformado derruba a app no startup); `get_calendar_id()` (linha 299-304) faz `cal_id.startswith("${")` sem garantir que `cal_id` seja string (`AttributeError` se `calendar_id` for `null`); `get_message()` (linha 263-282) devolve string vazia quando a chave não existe (paciente recebe mensagem em branco); `_resolve_env_vars()` (linha 76-85) só resolve a string exatamente igual a `${VAR}` e, se a env não existir, vaza o literal `${VAR}` para mensagens e Calendar; o singleton não tem lock e `get_doctor_name()` (linha 284-286) tem default `"Dra."` truncado.

2. **"Engine fantasma" (findings EN-02 a EN-04):** README (`README.md`, seção "Camada conversacional com LangGraph", linhas 103-129) e `.env.example` (linhas 31-34) prometem um motor `langgraph` com fallback (`CONVERSATION_ENGINE`, `LANGGRAPH_FALLBACK_TO_LEGACY`) que **não existe no código**. O grep por `CONVERSATION_ENGINE`/`langgraph` em arquivos `.py` retorna **zero ocorrências**; o motor real é hardcoded — `CleanAgentService` (instanciado como `dental_crew` em `src/interfaces/http/app.py:114`) cuja própria docstring declara "Um único engine" (`clean_agent_service.py:3`). A dependência `langgraph>=1.1.0` (`requirements.txt:10`) está instalada sem uso, e `logging_config.py` mantém tags de engine mortas (`_ENGINE_COLORS` com `langgraph`/`legacy`, linhas 22-26).

O resultado esperado: configuração à prova de falhas (sem janela vazia, sem crash no startup, sem mensagem em branco ao paciente, sem literal `${VAR}` vazado) e documentação que descreve o sistema que realmente existe.

## 2. Contexto e Motivação

### 2.1 Problema Atual

**Configuração (`config_service.py`):**

- **CO-02 (crítico):** `reload()` (linha 96-99) executa `self._configs.clear()` seguido de `self._load_configs()`. Como `_configs` é compartilhado entre todas as threads (singleton de classe), durante a execução de `_load_configs()` outra thread atendendo um webhook pode ler `_configs` vazio → `get_message()` devolve `""`, `get_plans()` devolve `[]`, `get_calendar_id()` devolve o default. Pior: se o YAML estiver malformado, `_load_configs()` levanta exceção **depois** do `clear()`, deixando o estado **permanentemente vazio** até a app reiniciar.
- **CO-03 (alto):** `_load_configs()` (linha 87-94) chama `yaml.safe_load(f)` dentro de um loop `for yaml_file in config_dir.glob("*.yaml")` sem nenhum `try/except`. Um `config/settings.yaml` com indentação quebrada faz `yaml.safe_load` levantar `yaml.YAMLError` no `__new__` (linha 35) → a aplicação não sobe.
- **CO-06 (alto):** `get_calendar_id()` (linha 301-302) faz `cal_id.startswith("${")`. Se `calendar_id:` for `null` no YAML (ou um número), `cal_id` é `None`/`int` e `.startswith` levanta `AttributeError`. Como `get_calendar_id` é chamado em todo agendamento, isto causa "API toda hora dá erro".
- **CO-07 (alto, wrong_response):** `get_message()` (linha 263-282) percorre as chaves pontilhadas; se qualquer chave não existir, faz `value = value.get(key, "")` e ao final retorna `""`. O paciente recebe **mensagem em branco** — contribui diretamente para "responde errado aos clientes".
- **CO-09 (médio):** `_resolve_env_vars()` (linha 78-80) só resolve quando `value.startswith("${") and value.endswith("}")`, e usa `os.getenv(env_var, value)` — ou seja, se a env não estiver definida, **retorna o literal `${VAR}`**. Isso pode vazar `${GOOGLE_CALENDAR_ID}` para o Calendar ou `${DOCTOR_PHONE}` para alertas. Strings que contêm `${VAR}` no meio do texto não são resolvidas.
- **CO-10 (baixo):** o singleton (`__new__`, linha 32-36) não tem lock — duas threads criando a primeira instância simultaneamente podem disparar `_load_configs()` duas vezes. O default de `get_doctor_name()` é `"Dra."` (linha 286), truncado e sem o nome.

**Engine fantasma:**

- **EN-02 (médio):** `CONVERSATION_ENGINE` **nunca é lido em runtime** (zero matches em `.py`). O motor está hardcoded: `app.py:114` faz `dental_crew = CleanAgentService()` e `app.py:257` chama `dental_crew.process_message(...)`. A docstring do próprio serviço (`clean_agent_service.py:3`) diz "Um único engine. Sem state machine de strings. Sem heurísticas de keyword."
- **EN-03 (médio):** `README.md` (linhas 103-129, seção "Camada conversacional com LangGraph") afirma "O projeto agora pode usar LangGraph dentro da própria API" e descreve `CONVERSATION_ENGINE=langgraph` e fallback automático — recursos inexistentes. `.env.example` (linhas 31-34) repete a mentira com `CONVERSATION_ENGINE=legacy`, `LANGGRAPH_OPENAI_MODEL`, `LANGGRAPH_FALLBACK_TO_LEGACY=1`. README linhas 93-95 repetem as mesmas vars na lista de ambiente.
- **EN-04 (baixo):** `requirements.txt:10` declara `langgraph>=1.1.0` (peso morto — nenhum import). `logging_config.py` mantém `_ENGINE_COLORS` (linhas 22-26) com `langgraph`/`legacy` e a função `_colorize_message` (linha 44-52) que destaca tags `[ENGINE=...]` que nunca são emitidas.

### 2.2 Impacto do Problema

| Queixa do dono | Como esta implementação ajuda |
|---|---|
| (1) "API toda hora dá erro" | Remove crash no startup (CO-03), `AttributeError` em `get_calendar_id` (CO-06) e estado permanentemente vazio após reload (CO-02). |
| (2) "Responde errado aos clientes" | Elimina mensagem em branco (CO-07) e literal `${VAR}` vazado em texto/Calendar (CO-09). |
| (3) "Foge do escopo" | Indireto: respostas em branco hoje quebram o fluxo determinístico de agenda; mensagens corretas mantêm o paciente no trilho da agenda. |
| (4) "Marca errado / transtorno" | `get_calendar_id` resiliente (CO-06) evita escrever evento no calendário errado/default `primary` por env ausente. |
| Manutenção / confiança | Docs honestas (EN-02/03) evitam que um operador setar `CONVERSATION_ENGINE=langgraph` esperando comportamento que não acontece; remove peso morto (EN-04). |

### 2.3 Soluções Consideradas

| Solução | Prós | Contras | Decisão |
|---|---|---|---|
| Carregar em dict novo e trocar atomicamente (`self._configs = new`); manter config antiga em erro | Resolve CO-02 e CO-03 de uma vez; sem janela vazia; sem necessidade de lock em leitura (rebind de atributo é atômico em CPython) | Exige refatorar `_load_configs`/`reload` para retornar/aplicar dict | **Escolhida** |
| Envolver toda leitura em `RLock` | Conceitualmente simples | Overhead em cada `get_*`; não resolve o estado vazio se o YAML quebrar; mais propenso a deadlock | Rejeitada |
| Schema validation completa (pydantic) de cada YAML | Robusto a longo prazo | Escopo grande demais para esta correção; muda contrato de muitos campos | Adiada (fora de escopo) |
| Implementar de fato o engine LangGraph prometido | Cumpriria a doc | Esforço enorme, risco alto na agenda, contraria a decisão de "um único engine" do `CleanAgentService` | Rejeitada — corrigir a **documentação** em vez do código |
| Apenas remover as vars do `.env.example` sem tocar README | Rápido | README continua mentindo (seção inteira fantasma) | Rejeitada |

## 3. Especificação Técnica

### 3.1 Visão Geral da Arquitetura

O `ConfigService` permanece um singleton lido por toda a camada `application`/`interfaces`. As mudanças são internas ao serviço (carregamento atômico e getters defensivos) e na documentação/dependências. Nenhuma assinatura pública de método muda — os mesmos `get_*` continuam existindo, apenas ficam à prova de falhas. A camada `src/application/orchestration/` citada como alvo **não existe** no repositório (Glob `src/application/orchestration/**` retornou "No files found"); confirma-se que não há orquestração de engine para limpar lá — toda a "limpeza de engine" se concentra em README, `.env.example`, `requirements.txt` e `logging_config.py`.

### 3.2 Componentes Afetados

| Componente | Tipo | Ação | Descrição |
|---|---|---|---|
| `src/infrastructure/config/config_service.py` — `_load_configs` | Método | Modificar | Construir dict novo + `try/except` por arquivo; degradar em vez de derrubar. |
| `config_service.py` — `reload` | Método | Modificar | Carregar para dict novo e só então rebindar `self._configs`; manter antigo em erro. |
| `config_service.py` — `get_calendar_id` | Método | Modificar | Coagir `cal_id` para `str` antes de `.startswith`. |
| `config_service.py` — `get_message` | Método | Modificar | Fallback logado quando a chave não existe (não retornar `""` silencioso). |
| `config_service.py` — `_resolve_env_vars` | Método | Modificar | Resolver `${VAR}` também no meio da string; logar/avisar quando a env estiver ausente em vez de vazar o literal. |
| `config_service.py` — `__new__` / `_configs` | Método/atributo | Modificar | Adicionar lock de criação do singleton; default de `get_doctor_name` não truncado. |
| `requirements.txt` | Manifesto | Modificar | Remover `langgraph>=1.1.0` (linha 10). Avaliar `langchain-core`/`langchain-openai` (verificar uso real antes de remover). |
| `src/infrastructure/logging_config.py` | Módulo | Modificar | Remover `_ENGINE_COLORS` / `_colorize_message` mortos e a entrada `langgraph` em `_NOISY_LOGGERS` (linha 35). |
| `README.md` | Doc | Modificar | Remover seção "Camada conversacional com LangGraph" (103-129) e linhas 93-95; descrever o engine único real (`CleanAgentService`). |
| `.env.example` | Doc | Modificar | Remover `CONVERSATION_ENGINE`, `LANGGRAPH_OPENAI_MODEL`, `LANGGRAPH_FALLBACK_TO_LEGACY` (linhas 31-34). |
| `config/settings.yaml`, `config/plans.yaml`, `config/messages.yaml`, `config/procedure_rules.yaml` | Config | Inspecionar | Usados nos testes como fixtures válidas/inválidas; nenhuma mudança de conteúdo prevista. |
| `src/application/orchestration/` | Diretório | N/A | N/A — diretório não existe no repositório; nada a limpar lá. |

### 3.3 Interfaces e Contratos

Nenhuma assinatura pública muda. Contratos reforçados:

- `get_message(path: str, **kwargs) -> str`: **nunca** retorna string vazia silenciosa para chave ausente; loga `warning` com o `path` e devolve um fallback não-vazio (mensagem genérica de `errors.general`, ou o próprio `path` como sinalizador, conforme decidido na implementação — ver §9). Continua aplicando `.format(**kwargs)` e tolerando `KeyError` de interpolação como hoje (linha 280-281).
- `get_calendar_id() -> str`: sempre retorna `str`; nunca levanta `AttributeError`; default `"primary"` mantido.
- `reload() -> None`: garante que, em caso de YAML inválido, `self._configs` permanece com a configuração **anterior** válida (não fica vazio).
- `_resolve_env_vars(value)`: env ausente não vaza `${VAR}` para o consumidor — substitui por string vazia (ou default conhecido para `calendar_id`) e loga.

### 3.4 Modelos de Dados

N/A — justificativa: não há mudança de schema de banco nem de estrutura dos YAML. As chaves consumidas continuam sendo as já existentes (`settings.doctor.{name,phone,calendar_id,address}`, `settings.scheduling.*`, `settings.clinic.*`, `plans.plans[]`, `messages.*`, `procedure_rules.rules[]`).

### 3.5 Fluxo de Execução

**Startup:**
1. Primeira chamada a `ConfigService()` → `__new__` (sob lock) → `_load_configs()`.
2. `_load_configs` itera `config/*.yaml`, carrega cada um em um dict **local** `new_configs`; arquivo inválido é logado e pulado (mantendo os demais).
3. `self._configs = new_configs` (rebind atômico) ao final.

**Reload (hot reload):**
1. `reload()` chama o mesmo carregamento para um dict local.
2. Se o carregamento produziu pelo menos a estrutura mínima, rebind `self._configs = new_configs`.
3. Se houve erro fatal, **mantém** `self._configs` antigo e loga `error`.

**Leitura (request de webhook):**
1. `get_message`/`get_calendar_id`/`get_plans` leem `self._configs` (sempre populado).
2. `get_message` com chave ausente → log + fallback não-vazio.

### 3.6 Tratamento de Erros

| Situação | Comportamento atual | Comportamento alvo |
|---|---|---|
| YAML malformado no startup | Exceção sobe no `__new__`, app não inicia (CO-03) | Arquivo logado e pulado; demais carregam; app sobe |
| YAML malformado no `reload()` | `_configs` fica vazio para sempre (CO-02) | Mantém config anterior; loga `error` |
| `calendar_id: null` no YAML | `AttributeError` em `get_calendar_id` (CO-06) | Coage para `str`, segue para default `primary` |
| Chave de mensagem inexistente | Retorna `""` → paciente recebe vazio (CO-07) | Loga `warning`; retorna fallback não-vazio |
| Env `${VAR}` ausente | Vaza literal `${VAR}` (CO-09) | Loga `warning`; substitui por vazio/default |
| Singleton criado em corrida | Possível dupla carga (CO-10) | Lock garante carga única |

## 4. Requisitos

### 4.1 Requisitos Funcionais

- **RF-001 (CO-02):** `reload()` deve carregar a configuração em uma estrutura nova e trocá-la atomicamente; em caso de erro, a configuração anterior válida deve ser preservada (sem janela vazia e sem estado permanentemente vazio).
- **RF-002 (CO-03):** `_load_configs()` deve tratar exceções por arquivo (`yaml.YAMLError`, `OSError`), registrar log e degradar — a aplicação deve iniciar mesmo com um YAML inválido, carregando os arquivos válidos restantes.
- **RF-003 (CO-06):** `get_calendar_id()` deve coagir o valor lido para `str` antes de qualquer operação de string, jamais levantando `AttributeError` quando `calendar_id` for `null` ou não-string.
- **RF-004 (CO-07):** `get_message()` deve registrar `warning` e retornar um fallback **não-vazio** quando a chave pontilhada não existir; o paciente nunca deve receber mensagem em branco.
- **RF-005 (CO-09):** `_resolve_env_vars()` deve resolver `${VAR}` mesmo dentro de strings maiores e, quando a env estiver ausente, não vazar o literal `${VAR}` para o consumidor (substituir por vazio/default e logar).
- **RF-006 (CO-10):** a criação do singleton deve ser protegida por lock; o default de `get_doctor_name()` deve ser um nome completo e não-truncado (ex.: `"Dra. Priscila"` consistente com `settings.yaml:3`).
- **RF-007 (EN-02/EN-03):** a documentação (`README.md`, `.env.example`) deve descrever o motor real e único (`CleanAgentService` / `dental_crew`) e remover qualquer menção a `CONVERSATION_ENGINE`, `langgraph` como engine selecionável e fallback automático.
- **RF-008 (EN-04):** a dependência `langgraph>=1.1.0` deve ser removida de `requirements.txt` e as tags de engine mortas (`_ENGINE_COLORS`, `_colorize_message`, `langgraph` em `_NOISY_LOGGERS`) removidas de `logging_config.py`.

### 4.2 Requisitos Não-Funcionais

- **RNF-001 (Disponibilidade):** nenhuma alteração pode introduzir crash no caminho de startup nem no caminho de atendimento de webhook.
- **RNF-002 (Concorrência):** leituras de configuração devem permanecer seguras sob múltiplas threads do servidor FastAPI (rebind atômico do dict; sem leitura de estado parcialmente populado).
- **RNF-003 (Observabilidade):** toda degradação (YAML pulado, chave de mensagem ausente, env ausente) deve gerar log no nível adequado (`warning`/`error`) para diagnóstico.
- **RNF-004 (Compatibilidade):** assinaturas públicas dos métodos `get_*` permanecem inalteradas; consumidores existentes não precisam mudar.

### 4.3 Restrições

- Manter a arquitetura limpa: a correção fica em `infrastructure/config` e `infrastructure/logging_config`; nada de regra de negócio nova.
- Não implementar o engine LangGraph — a decisão é corrigir docs, não código.
- Não alterar o conteúdo funcional dos YAML de produção (`plans.yaml`, `messages.yaml`, etc.).
- PT-BR em mensagens e logs visíveis ao operador.

## 5. Critérios de Aceitação

- [ ] **CA-001 (RF-001):** Com `config/settings.yaml` corrompido em tempo de execução, após `reload()` o `ConfigService` continua retornando a última configuração válida (não vazia) e registra `error`.
- [ ] **CA-002 (RF-001):** Não existe janela em que outra thread leia `_configs` vazio durante o `reload()` (o dict só é rebindado quando completo).
- [ ] **CA-003 (RF-002):** Com um YAML malformado presente no startup, a aplicação inicia normalmente, carrega os YAML válidos e loga o arquivo problemático.
- [ ] **CA-004 (RF-003):** `get_calendar_id()` com `calendar_id: null` retorna `"primary"` (ou o valor de `GOOGLE_CALENDAR_ID`) sem levantar exceção.
- [ ] **CA-005 (RF-004):** `get_message("chave.inexistente")` retorna string não-vazia e gera um log `warning` com o path.
- [ ] **CA-006 (RF-005):** Com a env de uma `${VAR}` ausente, o valor resolvido não contém o literal `${...}`; com a env presente e a var no meio de um texto, o valor é interpolado corretamente.
- [ ] **CA-007 (RF-006):** A criação concorrente do singleton dispara `_load_configs` uma única vez; `get_doctor_name()` sem config retorna nome não-truncado.
- [ ] **CA-008 (RF-007):** `grep -i langgraph` e `grep CONVERSATION_ENGINE` em `README.md` e `.env.example` retornam zero ocorrências; README descreve `CleanAgentService` como engine único.
- [ ] **CA-009 (RF-008):** `requirements.txt` não contém `langgraph`; `logging_config.py` não contém `_ENGINE_COLORS` nem `langgraph` em `_NOISY_LOGGERS`; a app sobe sem `langgraph` instalado.
- [ ] **CA-010 (RNF-001):** Suíte de testes passa e a aplicação inicia com os quatro YAML reais de `config/`.

## 6. Plano de Testes

### 6.1 Unitários

- `test_reload_atomico_mantem_config_em_yaml_quebrado`: corromper temporariamente um YAML, chamar `reload()`, asseverar que `get_plans()`/`get_message()` ainda retornam a config anterior (CA-001/CA-002).
- `test_load_configs_degrada_com_yaml_invalido`: apontar `config_dir` para um diretório com um YAML quebrado + um válido; asseverar que o válido carrega e a instância é criada (CA-003).
- `test_get_calendar_id_com_null`: `settings.doctor.calendar_id = None` → `get_calendar_id()` retorna `"primary"` sem exceção (CA-004).
- `test_get_message_chave_ausente_nao_vazia`: `get_message("foo.bar.baz")` retorna não-vazio e loga (CA-005).
- `test_resolve_env_vars_ausente_e_no_meio`: `${NAO_EXISTE}` não vaza literal; `"prefixo ${EXISTE} sufixo"` é interpolado (CA-006).
- `test_singleton_lock_carrega_uma_vez`: instanciar em várias threads e asseverar uma única carga (CA-007).
- `test_get_doctor_name_default_nao_truncado`: sem `settings`, `get_doctor_name()` retorna nome completo (CA-007).

### 6.2 Integração

- `test_app_sobe_com_yaml_quebrado`: subir o `ConfigService` real com um YAML inválido injetado e verificar que `app.py` importa/instancia `dental_crew` sem falhar.
- `test_app_sobe_sem_langgraph`: garantir (ambiente sem `langgraph` instalado, ou import isolado) que `setup_logging` e o import de `app` funcionam após remoção (CA-009).

### 6.3 Aceitação

- Verificação manual/automatizada de que `README.md` e `.env.example` não mencionam mais `langgraph`/`CONVERSATION_ENGINE` (CA-008).
- Inicialização da app com os quatro YAML reais e envio de uma mensagem de teste pelo webhook, confirmando que nenhuma resposta sai em branco (CA-010).

### 6.4 Casos de Borda

- YAML existente porém **vazio** (`yaml.safe_load` retorna `None`): já tratado por `or {}` na linha 93 — adicionar teste de regressão.
- `calendar_id` definido como número (ex.: `123`) — `get_calendar_id` deve coagir para `str`.
- `messages.yaml` com chave intermediária que é string (não dict): `get_message` percorrendo `value.get` num não-dict (linha 274-276) deve continuar retornando fallback não-vazio.
- `${VAR}` com chaves aninhadas em listas (`_resolve_env_vars` recursivo em list/dict, linhas 81-84) — manter funcionando.
- Duas chamadas a `reload()` em paralelo — não deixar `_configs` num estado intermediário.

## 7. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Remover `langchain-core`/`langchain-openai` junto com `langgraph` quebrar import real | Média | Alto | Antes de remover, rodar grep por `langchain` em `.py`; remover apenas `langgraph` se os demais forem usados. |
| Fallback de `get_message` mascarar bug de chave errada em vez de expô-lo | Média | Médio | Logar `warning` sempre; em testes, asseverar presença do log para flagrar regressões. |
| Mudança em `_resolve_env_vars` (resolver no meio da string) alterar valores hoje "aceitos" como literais | Baixa | Médio | Cobrir com testes os campos sensíveis (`calendar_id`, `phone`); revisar YAML de produção. |
| Rebind atômico assumir garantia de CPython (GIL) | Baixa | Médio | Documentar a premissa em §9; o assignment de atributo é atômico em CPython, suficiente para o cenário. |
| Operador já ter `CONVERSATION_ENGINE`/`LANGGRAPH_*` no `.env` de produção | Média | Baixo | Vars eram no-op (nunca lidas); remoção é segura, registrar na nota de release. |

## 8. Dependências

### 8.1 Internas

- **Implementação 001** (pré-requisito): base de estabilização/resiliência da API e do startup, sobre a qual o carregamento defensivo de config se apoia.
- **Implementação 002 — Recuperação da Rede de Testes** (pré-requisito): suíte verde para validar o carregamento defensivo de config sem regredir os contratos de leitura do `ConfigService`.

### 8.2 Externas

- `pyyaml>=6.0` (`requirements.txt:6`) — parser YAML usado em `_load_configs`.
- `python-dotenv>=1.0.0` (`requirements.txt:8`) — carregamento de env consumido por `_resolve_env_vars`/`os.getenv`.
- `langgraph>=1.1.0` (`requirements.txt:10`) — **a ser removida** por esta implementação.

## 9. Observações e Decisões de Design

- **Fonte da verdade do engine:** o motor real é `CleanAgentService` (docstring `clean_agent_service.py:3`: "Um único engine. Sem state machine de strings. Sem heurísticas de keyword."), instanciado em `app.py:114` (`dental_crew = CleanAgentService()`) e usado em `app.py:257`. A documentação deve refletir exatamente isso; não há, nem haverá nesta implementação, seleção de engine por variável de ambiente.
- **`src/application/orchestration/` não existe:** o Glob `src/application/orchestration/**` retornou "No files found". Listado como alvo apenas por precaução; não há código de orquestração de engine a remover ali. Registrado como N/A em §3.2.
- **Rebind atômico vs. lock de leitura:** optou-se por construir um dict novo e fazer `self._configs = new_configs`. Em CPython, o rebind de um atributo é atômico sob o GIL, o que elimina a janela de leitura parcial sem precisar de lock em cada `get_*` (decisão de §2.3). Lock será usado apenas na criação do singleton (CO-10).
- **Fallback de `get_message`:** decisão de implementação entre (a) retornar `errors.general` formatada (não-vazia, segura ao paciente) ou (b) retornar o próprio `path` como sinalizador visível em dev. Recomenda-se (a) em produção com `warning` logado, garantindo que o paciente nunca veja string vazia (RF-004) sem mascarar o diagnóstico (RNF-003).
- **`get_doctor_phone`/`get_calendar_id` já tratam `${`:** ambos já têm guarda contra literal `${...}` (linhas 291-293 e 302-303); a correção de CO-09 centraliza esse cuidado em `_resolve_env_vars` para que todos os campos (inclusive mensagens) fiquem cobertos, não só estes dois.
- **Limpeza conservadora de dependências:** remover apenas `langgraph` com certeza; `langchain-core`/`langchain-openai` só devem sair após confirmar ausência de import — fora do escopo se estiverem em uso.
