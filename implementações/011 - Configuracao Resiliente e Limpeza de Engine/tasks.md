# Tarefas: Configuração Resiliente e Limpeza de Engine

> **Implementação:** 011 - Configuração Resiliente e Limpeza de Engine
> **Spec:** [spec.md](./spec.md)
> **Progresso:** 0/12 tarefas concluídas (0%)
> **Última atualização:** 2026-06-15

Legenda: `[ ]` Pendente, `[x]` Concluída, `[!]` Bloqueada, `[-]` Cancelada.

---

## Fase 1 — Preparação

### [ ] T-001 — Mapear consumidores e confirmar findings no código
- **Descrição:** Reler `config_service.py` linhas-alvo (`_load_configs` 87-94, `reload` 96-99, `get_calendar_id` 299-304, `get_message` 263-282, `_resolve_env_vars` 76-85, `__new__` 32-36, `get_doctor_name` 286); rodar grep por `langgraph`/`CONVERSATION_ENGINE`/`langchain` em `src/**/*.py` para confirmar peso morto e identificar imports reais de `langchain-core`/`langchain-openai`.
- **Arquivos envolvidos:** `src/infrastructure/config/config_service.py`, `requirements.txt`, todo `src/**/*.py` (grep).
- **Critério de conclusão:** Lista escrita de todos os call-sites de `get_message`/`get_calendar_id` e veredito sobre quais dependências langchain/langgraph podem ser removidas com segurança.
- **Dependências:** Implementações 001 e 002 concluídas.
- **Estimativa:** Pequena.

### [ ] T-002 — Preparar fixtures de teste (YAML válido e malformado)
- **Descrição:** Criar fixtures de configuração para testes: um diretório com YAML válido (espelhando `config/`) e variantes inválidas (indentação quebrada, `calendar_id: null`, YAML vazio, chave de mensagem ausente).
- **Arquivos envolvidos:** `tests/fixtures/config/` (novo), referência a `config/settings.yaml`, `config/messages.yaml`, `config/plans.yaml`, `config/procedure_rules.yaml`.
- **Critério de conclusão:** Fixtures disponíveis e carregáveis isoladamente apontando `config_dir` para o diretório de teste.
- **Dependências:** T-001.
- **Estimativa:** Média.

---

## Fase 2 — Implementação

### [ ] T-003 — Carregamento atômico e degradação em `_load_configs`/`reload` (CO-02, CO-03)
- **Descrição:** Refatorar `_load_configs` para montar um dict local `new_configs` com `try/except` por arquivo (`yaml.YAMLError`, `OSError`), logando e pulando inválidos, e só então rebindar `self._configs = new_configs`. Ajustar `reload()` para usar o mesmo carregamento e preservar a config anterior em caso de erro fatal (nunca deixar `_configs` vazio).
- **Arquivos envolvidos:** `src/infrastructure/config/config_service.py` (`_load_configs` 87-94, `reload` 96-99).
- **Critério de conclusão:** Atende RF-001 e RF-002; YAML quebrado no startup não derruba a app; `reload()` mantém estado válido anterior.
- **Dependências:** T-001.
- **Estimativa:** Média.

### [ ] T-004 — Tornar `get_calendar_id` resiliente a valor não-string (CO-06)
- **Descrição:** Coagir `cal_id` para `str` antes de `.startswith("${")`; garantir retorno `str` e default `"primary"`/`GOOGLE_CALENDAR_ID` quando `calendar_id` for `null`/número.
- **Arquivos envolvidos:** `src/infrastructure/config/config_service.py` (`get_calendar_id` 299-304).
- **Critério de conclusão:** Atende RF-003; nenhuma `AttributeError` possível.
- **Dependências:** T-001.
- **Estimativa:** Pequena.

### [ ] T-005 — Fallback não-vazio e log em `get_message` (CO-07)
- **Descrição:** Quando a chave pontilhada não existir (ou intermediária não for dict), logar `warning` com o `path` e retornar fallback não-vazio (recomendado: `errors.general` formatada), em vez de `""`. Manter o `.format(**kwargs)` e a tolerância a `KeyError` de interpolação.
- **Arquivos envolvidos:** `src/infrastructure/config/config_service.py` (`get_message` 263-282), `config/messages.yaml` (`errors.general`).
- **Critério de conclusão:** Atende RF-004; paciente nunca recebe mensagem em branco; log gerado.
- **Dependências:** T-001.
- **Estimativa:** Média.

### [ ] T-006 — Resolver `${VAR}` no meio da string e não vazar literal (CO-09)
- **Descrição:** Reescrever `_resolve_env_vars` para substituir todas as ocorrências `${VAR}` dentro de qualquer string (regex), substituindo env ausente por vazio/default e logando `warning`; manter recursão em dict/list.
- **Arquivos envolvidos:** `src/infrastructure/config/config_service.py` (`_resolve_env_vars` 76-85).
- **Critério de conclusão:** Atende RF-005; literal `${...}` nunca chega ao consumidor; interpolação no meio de texto funciona.
- **Dependências:** T-001.
- **Estimativa:** Média.

### [ ] T-007 — Lock do singleton e default não-truncado (CO-10)
- **Descrição:** Proteger a criação do singleton em `__new__` com um lock de módulo/classe (carga única); ajustar default de `get_doctor_name()` de `"Dra."` para nome completo (ex.: `"Dra. Priscila"`, consistente com `settings.yaml:3`).
- **Arquivos envolvidos:** `src/infrastructure/config/config_service.py` (`__new__` 32-36, `get_doctor_name` 284-286).
- **Critério de conclusão:** Atende RF-006; `_load_configs` chamado uma vez sob corrida; default sem truncamento.
- **Dependências:** T-001.
- **Estimativa:** Pequena.

### [ ] T-008 — Remover dependência e tags de engine mortas (EN-04)
- **Descrição:** Remover `langgraph>=1.1.0` de `requirements.txt:10`; remover `_ENGINE_COLORS` (22-26), `_colorize_message` (44-52) e a entrada `langgraph` em `_NOISY_LOGGERS` (linha 35) de `logging_config.py`, ajustando `ColoredFormatter.format` (que chama `_colorize_message` na linha 81). Remover `langchain-*` somente se T-001 confirmar ausência de uso.
- **Arquivos envolvidos:** `requirements.txt`, `src/infrastructure/logging_config.py`.
- **Critério de conclusão:** Atende RF-008; app sobe sem `langgraph` instalado; sem código de engine fantasma em logs.
- **Dependências:** T-001.
- **Estimativa:** Média.

---

## Fase 3 — Testes

### [ ] T-009 — Testes unitários de configuração resiliente (CO-02/03/06/07/09/10)
- **Descrição:** Implementar os testes de §6.1: `test_reload_atomico_mantem_config_em_yaml_quebrado`, `test_load_configs_degrada_com_yaml_invalido`, `test_get_calendar_id_com_null`, `test_get_message_chave_ausente_nao_vazia`, `test_resolve_env_vars_ausente_e_no_meio`, `test_singleton_lock_carrega_uma_vez`, `test_get_doctor_name_default_nao_truncado`.
- **Arquivos envolvidos:** `tests/test_config_service.py` (novo), fixtures de T-002.
- **Critério de conclusão:** Cobre CA-001 a CA-007; todos verdes.
- **Dependências:** T-003, T-004, T-005, T-006, T-007.
- **Estimativa:** Grande.

### [ ] T-010 — Testes de casos de borda de configuração
- **Descrição:** Implementar os casos de §6.4: YAML vazio (`None` → `{}`), `calendar_id` numérico, chave intermediária string em `get_message`, `${VAR}` aninhado em lista/dict, `reload()` concorrente.
- **Arquivos envolvidos:** `tests/test_config_service.py`, fixtures de T-002.
- **Critério de conclusão:** Todos os casos de borda passam sem exceção e sem retorno vazio.
- **Dependências:** T-003, T-004, T-005, T-006, T-007.
- **Estimativa:** Média.

### [ ] T-011 — Testes de integração de startup e ausência de langgraph
- **Descrição:** Implementar §6.2: `test_app_sobe_com_yaml_quebrado` (ConfigService real com YAML inválido injetado, `dental_crew` instancia) e `test_app_sobe_sem_langgraph` (`setup_logging` e import de `app` funcionam após remoção).
- **Arquivos envolvidos:** `tests/test_startup.py` (novo), `src/interfaces/http/app.py`, `src/infrastructure/logging_config.py`.
- **Critério de conclusão:** Cobre CA-003, CA-009, CA-010; testes verdes.
- **Dependências:** T-003, T-008.
- **Estimativa:** Média.

---

## Fase 4 — Documentação

### [ ] T-012 — Corrigir documentação do engine fantasma (EN-02, EN-03)
- **Descrição:** Remover de `README.md` a seção "Camada conversacional com LangGraph" (linhas 103-129) e as linhas 93-95 (`CONVERSATION_ENGINE`/`LANGGRAPH_*`); substituir por descrição do engine único real (`CleanAgentService` / `dental_crew` em `app.py:114`). Remover de `.env.example` as linhas 31-34 (`# Conversation engine`, `CONVERSATION_ENGINE`, `LANGGRAPH_OPENAI_MODEL`, `LANGGRAPH_FALLBACK_TO_LEGACY`). Registrar na nota de release que as vars eram no-op.
- **Arquivos envolvidos:** `README.md`, `.env.example`.
- **Critério de conclusão:** Atende RF-007 e CA-008; grep por `langgraph`/`CONVERSATION_ENGINE` nesses arquivos retorna zero.
- **Dependências:** T-001.
- **Estimativa:** Pequena.

---

## Registro de Progresso

| Tarefa | Fase | Status | Estimativa | Findings cobertos |
|---|---|---|---|---|
| T-001 | Preparação | [ ] Pendente | Pequena | (todos — investigação) |
| T-002 | Preparação | [ ] Pendente | Média | CO-02/03/06/07/09 |
| T-003 | Implementação | [ ] Pendente | Média | CO-02, CO-03 |
| T-004 | Implementação | [ ] Pendente | Pequena | CO-06 |
| T-005 | Implementação | [ ] Pendente | Média | CO-07 |
| T-006 | Implementação | [ ] Pendente | Média | CO-09 |
| T-007 | Implementação | [ ] Pendente | Pequena | CO-10 |
| T-008 | Implementação | [ ] Pendente | Média | EN-04 |
| T-009 | Testes | [ ] Pendente | Grande | CO-02/03/06/07/09/10 |
| T-010 | Testes | [ ] Pendente | Média | CO-02/06/07/09 (borda) |
| T-011 | Testes | [ ] Pendente | Média | CO-03, EN-04 |
| T-012 | Documentação | [ ] Pendente | Pequena | EN-02, EN-03 |

**Total:** 12 tarefas | **Concluídas:** 0 | **Progresso:** 0%
