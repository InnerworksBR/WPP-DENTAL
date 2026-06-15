# Estabilidade da API e Resiliência de IO

> **ID:** 001
> **Status:** 🟢 Concluída
> **Prioridade:** 🔴 Crítica
> **Criada em:** 2026-06-15
> **Última atualização:** 2026-06-15
> **Autor:** Agente AI

---

## 1. Resumo Executivo

Esta implementação ataca diretamente a queixa nº 1 do dono ("a API toda hora dá erro"). O webhook `POST /webhook/message` (`src/interfaces/http/app.py:129`) é o caminho crítico de todas as conversas e hoje possui pontos de falha não tratados que viram HTTP 500 e provocam reentrega pela Evolution API, gerando loops de erro e mensagens duplicadas.

Foram identificados seis vetores concretos de instabilidade no código real:

1. **AG-01** — `ChatOpenAI` instanciado sem `request_timeout` e sem `max_tokens` (`clean_agent_service.py:286-290`), e `self._llm.invoke(messages)` (`clean_agent_service.py:295`) sem try/except local. Com timeout nulo, o `httpx` subjacente pode pendurar o handler indefinidamente.
2. **EVENT-LOOP** — `CleanAgentService.process_message` é **síncrono** e faz IO de rede (LLM), mas é chamado diretamente do endpoint `async` em `app.py:255-264`, bloqueando o event loop e serializando todas as conversas.
3. **WE-10** — Acessos ao SQLite no caminho do webhook sem try/except: `_try_claim_message_processing` (`app.py:1420-1451`), `_mark_message_processed` (`app.py:1454-1463`), `_mark_message_failed` (`app.py:1466-1475`) e `ConversationStateService.get` (`conversation_state_service.py:42-64`, chamado em `app.py:204`). Um erro de lock vira 500 + reentrega.
4. **CONNECTION** — `src/infrastructure/persistence/connection.py` já configura `journal_mode=WAL` e `foreign_keys=ON` (linhas 100-101), mas **não** define `busy_timeout` e mantém `check_same_thread` no padrão, enquanto webhook (event loop) e scheduler async escrevem concorrentemente.
5. **AG-06 / CA-03** — Exceções das tools são serializadas como string crua e devolvidas ao LLM (`clean_agent_service.py:355-356`: `f"Erro em '{call['name']}': {exc}"`); as tools do `calendar_tool.py` chamam a API Google sem padronização de erro consistente (ex.: `GetAvailableSlotsTool._run` chama `service.get_available_slots(dt, period)` na linha 182 sem try/except).
6. **WH-07** — `OutboundMessageStore.record` roda dentro do bloco try que só cobre `httpx.HTTPError` (`whatsapp_service.py:84-101` e `119-136`); uma falha de SQLite após o POST bem-sucedido vira exceção não tratada.

O objetivo é eliminar exceções não tratadas e travas, aplicando timeouts, execução fora do event loop, degradação segura e padronização de mensagens de erro — sem introduzir escalonamento silencioso nem sucesso silencioso.

---

## 2. Contexto e Motivação

### 2.1 Problema Atual

O motor de produção é `CleanAgentService` (`src/application/services/clean_agent_service.py`), instanciado como `dental_crew` em `src/interfaces/http/app.py:114`. O fluxo determinístico que o precede vive em `src/interfaces/http/app.py` (1554 linhas).

Comportamentos defeituosos observados no código:

- **LLM sem timeout:** em `clean_agent_service.py:286-290` o cliente é criado apenas com `model` e `temperature=0`. Sem `request_timeout`, o `httpx` interno da OpenAI pode aguardar indefinidamente. Como `_llm.invoke` (`:295`) está dentro de `_run_loop`, que está dentro de `process_message`, que é chamado de forma bloqueante no endpoint async, uma única chamada pendurada congela o processo inteiro.
- **Bloqueio do event loop:** `process_message` (`clean_agent_service.py:391-429`) é uma função `def` comum (síncrona) que executa IO de rede do LLM e das tools. É chamada diretamente em `app.py:255-264` dentro do handler `async def receive_message`. Enquanto uma conversa processa o LLM, nenhuma outra requisição async avança.
- **SQLite sem proteção no caminho quente:** `_try_claim_message_processing` (`app.py:1420`) faz `INSERT OR IGNORE` + `UPDATE` + `commit`; `_mark_message_processed`/`_mark_message_failed` fazem `UPDATE` + `commit`; `ConversationStateService.get` (`app.py:204`) faz `SELECT`. Nenhum tem try/except. Um `sqlite3.OperationalError: database is locked` propaga até o FastAPI e vira 500.
- **Conexão sem `busy_timeout`:** `connection.py:94-102` cria uma conexão por thread com WAL ativo, porém sem `PRAGMA busy_timeout`. Sob escrita concorrente entre o event loop (webhook) e o scheduler async (ver fluxo de `lifespan` em `app.py:95-103`), uma escritora bloqueada falha imediatamente em vez de esperar.
- **Erros de tool crus para o LLM:** em `clean_agent_service.py:355-356`, qualquer exceção vira `f"Erro em '{call['name']}': {exc}"`. Detalhes técnicos do Google (`HttpError`, stack traces serializados) entram no histórico do modelo e podem aparecer indiretamente ao paciente, contribuindo para a queixa nº 2 ("responde errado").
- **`record` dentro do try de HTTP:** em `whatsapp_service.py:92-96` (async) e `127-131` (sync), a chamada `OutboundMessageStore.record(...)` ocorre **depois** do `response.raise_for_status()`, mas dentro do mesmo `try` cujo `except` só captura `httpx.HTTPError` (`:99` e `:134`). Se `record` lançar `sqlite3.Error`, a mensagem já foi entregue ao paciente, porém o método propaga exceção — sucesso de entrega tratado como falha.

### 2.2 Impacto do Problema

- **Disponibilidade:** uma única chamada de LLM pendurada ou um lock de SQLite derruba o atendimento de todos os pacientes (event loop bloqueado / 500).
- **Duplicidade:** 500 no webhook faz a Evolution reentregar a mensagem; combinado com falha em `_try_claim_message_processing`, o paciente pode receber respostas repetidas ou o sistema reprocessar indefinidamente.
- **Experiência do paciente:** erros crus de tool podem contaminar respostas; quedas frequentes minam a confiança na automação.
- **Transtorno operacional:** falha após o POST de WhatsApp (WH-07) pode marcar como falha algo que foi entregue, levando a reenvios e confusão (relacionado à queixa nº 4).

### 2.3 Soluções Consideradas

| Solução | Prós | Contras | Decisão |
|---|---|---|---|
| Adicionar `request_timeout`/`max_tokens` ao `ChatOpenAI` e try/except em `_run_loop` com retry curto | Mata o pendurado na origem; alinhado ao SDK LangChain/OpenAI | Exige escolher valores e tratar `APITimeoutError`/`RateLimitError` | **Adotada** (AG-01) |
| Rodar `process_message` via `asyncio.to_thread` no endpoint | Desbloqueia o event loop com mudança mínima; mantém o serviço síncrono | Cada conversa consome uma thread do executor; precisa garantir thread-safety do SQLite (resolvido por `threading.local` + `busy_timeout`) | **Adotada** (EVENT-LOOP) |
| Reescrever `CleanAgentService` para async nativo | Sem custo de threads | Refatoração grande e arriscada; tools são síncronas | Rejeitada (fora de escopo, alto risco) |
| try/except com degradação segura nas funções de SQLite do webhook | Evita 500/reentrega; resposta previsível | Precisa decidir o fallback de cada função sem mascarar bugs | **Adotada** (WE-10) |
| `PRAGMA busy_timeout` + revisão de `check_same_thread` em `connection.py` | Reduz `database is locked` sob concorrência; já há WAL | Não elimina 100% dos conflitos de escrita | **Adotada** (CONNECTION) |
| Padronizar mensagens de erro de tool em `clean_agent_service.py` e `calendar_tool.py` | Mensagens seguras ao LLM; logs detalhados separados | Necessário mapear tipos de erro do Google | **Adotada** (AG-06/CA-03) |
| Isolar `OutboundMessageStore.record` em try/except próprio | Entrega de WhatsApp não falha por erro de persistência | Precisa logar o desvio para não perder rastreio | **Adotada** (WH-07) |

---

## 3. Especificação Técnica

### 3.1 Visão Geral da Arquitetura

A arquitetura limpa (domain/application/infrastructure/interfaces) é mantida. As mudanças ficam nas camadas `interfaces` (HTTP e tools), `application` (CleanAgentService) e `infrastructure` (persistência e integrações). Não há mudança de contrato externo do webhook.

Camadas de resiliência a introduzir, na ordem do fluxo do webhook:

1. **Borda HTTP (`app.py`)** — claim/mark de mensagens e leitura de estado protegidos; chamada ao motor delegada a thread separada.
2. **Motor LLM (`clean_agent_service.py`)** — timeout/limite de tokens no cliente, retry curto para timeout/rate-limit, padronização de erro das tools.
3. **Persistência (`connection.py`)** — `busy_timeout` e política explícita de `check_same_thread`.
4. **Integração de saída (`whatsapp_service.py`)** — persistência isolada do envio.

### 3.2 Componentes Afetados

| Componente | Tipo | Ação | Descrição |
|---|---|---|---|
| `src/application/services/clean_agent_service.py` | Serviço (application) | Modificar | Adicionar `request_timeout` e `max_tokens` ao `ChatOpenAI` (`:286-290`); try/except em torno de `self._llm.invoke` (`:295`) tratando `APITimeoutError`/`RateLimitError` com retry curto e fallback "tente novamente em instantes"; padronizar erro de tool (`:355-356`). |
| `src/interfaces/http/app.py` | Endpoint/handlers (interfaces) | Modificar | Chamar `dental_crew.process_message` via `asyncio.to_thread` (`:255-264`); envolver `_try_claim_message_processing` (`:1420`), `_mark_message_processed` (`:1454`), `_mark_message_failed` (`:1466`) e o uso de `ConversationStateService.get` (`:204`) com degradação segura. |
| `src/infrastructure/persistence/connection.py` | Infra (persistência) | Modificar | Adicionar `PRAGMA busy_timeout` em `get_db` (`:94-102`); definir explicitamente `check_same_thread` ao `sqlite3.connect` (`:98`) coerente com o uso por `threading.local`. |
| `src/infrastructure/integrations/calendar_service.py` | Infra (integração) | Modificar (defensivo) | Garantir que falhas de rede/Google nas leituras usadas pelas tools sejam capturáveis de forma padronizada (sem alterar a lógica de negócio dos PRAGMAs/slots). |
| `src/interfaces/tools/calendar_tool.py` | Tools (interfaces) | Modificar | Envolver chamadas à API Google em try/except dedicado retornando mensagem segura (ex.: `GetAvailableSlotsTool._run` `:182`, `CreateAppointmentTool._run` `:350-354`, `CancelAppointmentTool._run` `:394-432`, `FindAppointmentTool._run` `:460-477`). `FindNextAvailableDayTool._run` já tem try/except global (`:261-321`) — padronizar a mensagem. |
| `src/infrastructure/integrations/whatsapp_service.py` | Infra (integração) | Modificar | Mover `OutboundMessageStore.record(...)` para try/except próprio em `send_message` (`:92-96`) e `send_message_sync` (`:127-131`), preservando o retorno `True` quando a entrega ocorreu. |

### 3.3 Interfaces e Contratos

- **Assinatura preservada:** `CleanAgentService.process_message(patient_phone, patient_message, patient_name="", history_text=None, is_first_message=None) -> str` mantém-se. O endpoint passa a invocá-la via `await asyncio.to_thread(dental_crew.process_message, ...)`.
- **Contrato de erro das tools (novo padrão interno):** toda tool deve retornar string começando com `Erro:` e contendo apenas mensagem segura (sem stack trace, sem detalhe técnico do Google). O `_run_loop` deve gerar a mesma forma para exceções não capturadas.
- **Contrato do webhook (inalterado externamente):** o endpoint continua retornando JSON com `status`; em degradação de SQLite, deve preferir uma resposta determinística (ex.: tratar a mensagem como não-claimável de forma segura) a propagar 500.
- **Fallback do LLM (AG-01):** ao esgotar o retry de timeout/rate-limit, `_run_loop` retorna texto curto ao paciente do tipo "Estou com uma instabilidade momentânea, pode tentar novamente em instantes?" — **não** dispara escalonamento/handoff silencioso.

### 3.4 Modelos de Dados

N/A — justificativa: nenhuma alteração de esquema. As tabelas envolvidas (`processed_messages`, `conversation_state`, `outbound_messages` em `connection.py:46-74`) já existem e permanecem com a mesma estrutura. Mudam apenas PRAGMAs de conexão e o tratamento de erro ao redor das operações.

### 3.5 Fluxo de Execução

Fluxo-alvo do `POST /webhook/message`:

1. Parse do JSON (já protegido em `app.py:132-135`).
2. `_try_claim_message_processing` (`app.py:164`) — agora com try/except: se o SQLite falhar, degradar de forma segura (logar e seguir sem reprocessar duplicata, ou recusar de forma determinística) em vez de 500.
3. Leitura de estado `ConversationStateService.get` (`app.py:204`) — protegida: em falha, usar `ConversationState()` padrão.
4. Etapas determinísticas existentes (escopo, slot, confirmação).
5. Chamada ao motor: `await asyncio.to_thread(dental_crew.process_message, ...)` (`app.py:255-264`), liberando o event loop.
6. Dentro do motor, `_run_loop` chama `self._llm.invoke` (`clean_agent_service.py:295`) protegido por timeout + retry curto; tools executadas com erro padronizado.
7. Envio via `_send_response` → `WhatsAppService.send_message`: após `raise_for_status`, `record` isolado (não derruba a entrega).
8. `_mark_message_processed`/`_mark_message_failed` — protegidos.

### 3.6 Tratamento de Erros

| Origem do erro | Comportamento atual | Comportamento alvo |
|---|---|---|
| LLM pendurado / `APITimeoutError` | Handler congela (sem timeout) | `request_timeout` (20–30s) + retry curto; ao esgotar, mensagem "tente novamente em instantes" |
| `RateLimitError` da OpenAI | Exceção não tratada em `_run_loop` | Capturada; retry curto; fallback amigável sem handoff silencioso |
| Tool lança exceção (`clean_agent_service.py:355`) | `f"Erro em '{name}': {exc}"` cru ao LLM | Mensagem segura padronizada + log detalhado (`logger.warning/error`) com o `exc` real |
| API Google falha dentro da tool | String crua/erro inesperado | try/except dedicado → `Erro: não consegui consultar a agenda agora.` (mensagem segura) |
| `sqlite3.OperationalError: database is locked` | 500 + reentrega Evolution | `busy_timeout` reduz ocorrência; try/except nas funções do webhook degrada com segurança |
| `ConversationStateService.get` falha | 500 | Retorna `ConversationState()` padrão e loga |
| `OutboundMessageStore.record` falha após POST OK | Exceção propaga; entrega tratada como falha | try/except isolado; entrega continua `True`; falha de persistência apenas logada |

---

## 4. Requisitos

### 4.1 Requisitos Funcionais

- **RF-001 (AG-01):** O `ChatOpenAI` em `clean_agent_service.py:286-290` deve ser instanciado com `request_timeout` (20–30s) e `max_tokens` definido. A chamada `self._llm.invoke` (`:295`) deve estar em try/except que trata `APITimeoutError` e `RateLimitError` com retry curto e, no esgotamento, retorna mensagem amigável de "tente novamente em instantes" — sem escalonamento silencioso.
- **RF-002 (EVENT-LOOP):** A chamada a `dental_crew.process_message` em `app.py:255-264` deve ocorrer fora do event loop, via `asyncio.to_thread` (ou `run_in_executor`), de modo que conversas concorrentes não bloqueiem umas às outras.
- **RF-003 (WE-10):** `_try_claim_message_processing` (`app.py:1420`), `_mark_message_processed` (`app.py:1454`), `_mark_message_failed` (`app.py:1466`) e o uso de `ConversationStateService.get` em `app.py:204` devem tratar exceções de SQLite com degradação segura, jamais propagando 500 por erro de lock/IO.
- **RF-004 (CONNECTION):** `connection.py:get_db` (`:94-102`) deve configurar `PRAGMA busy_timeout` e definir explicitamente `check_same_thread` coerente com o modelo por-thread, mantendo `journal_mode=WAL` (`:100`) e `foreign_keys=ON` (`:101`).
- **RF-005 (AG-06/CA-03):** Exceções de tools no `_run_loop` (`clean_agent_service.py:355-356`) e chamadas à API Google em `calendar_tool.py` devem ser capturadas e convertidas em mensagens de erro seguras e padronizadas, sem vazar detalhes técnicos.
- **RF-006 (WH-07):** `OutboundMessageStore.record` deve ser isolado em try/except próprio em `send_message` (`whatsapp_service.py:92-96`) e `send_message_sync` (`:127-131`); falha de persistência após POST bem-sucedido não pode alterar o retorno de sucesso da entrega.

### 4.2 Requisitos Não-Funcionais

- **RNF-001 (Disponibilidade):** Nenhum ponto do caminho do webhook pode pendurar indefinidamente; toda chamada de rede externa (LLM e Google) deve ter timeout efetivo.
- **RNF-002 (Concorrência):** O processamento de uma conversa não deve bloquear o processamento de outras (event loop livre).
- **RNF-003 (Observabilidade):** Toda degradação segura deve gerar log em nível apropriado (`warning`/`error`) com o erro original, sem expor detalhe técnico ao paciente.
- **RNF-004 (Segurança de conteúdo):** Mensagens de erro entregues ao LLM/paciente não podem conter stack traces nem dados internos.
- **RNF-005 (Idempotência):** A degradação de SQLite no claim não pode causar duplicação descontrolada de respostas ao paciente.

### 4.3 Restrições

- Sem alteração de esquema do banco (`connection.py:13-84`).
- Sem quebra de contrato do webhook nem da assinatura de `process_message`.
- Manter `temperature=0` no LLM (`clean_agent_service.py:288`).
- Não introduzir escalonamento/handoff automático como fallback de erro técnico do LLM (mantém-se o tratamento existente de handoff em `app.py:281-291` apenas para os gatilhos atuais).
- Respeitar a arquitetura limpa: tools não devem importar camada HTTP.

---

## 5. Critérios de Aceitação

- [x] **CA-001 (AG-01):** `ChatOpenAI` é criado com `request_timeout` entre 20–30s e `max_tokens` definido em `clean_agent_service.py`.
- [x] **CA-002 (AG-01):** Quando `self._llm.invoke` lança `APITimeoutError` ou `RateLimitError`, há retry curto e, no esgotamento, o método retorna mensagem amigável de "tente novamente em instantes", sem acionar handoff/escalonamento silencioso.
- [x] **CA-003 (EVENT-LOOP):** `process_message` é executado via `asyncio.to_thread`/executor em `app.py`, comprovado por teste que verifica que duas requisições não se serializam no event loop.
- [x] **CA-004 (WE-10):** Forçar erro de SQLite em `_try_claim_message_processing`, `_mark_message_processed`, `_mark_message_failed` e `ConversationStateService.get` não resulta em HTTP 500; o sistema degrada de forma segura e loga.
- [x] **CA-005 (CONNECTION):** A conexão retornada por `get_db` tem `busy_timeout` > 0 e `journal_mode=wal` confirmados via `PRAGMA`.
- [x] **CA-006 (AG-06/CA-03):** Uma exceção lançada por uma tool resulta em string `Erro: ...` segura no `ToolMessage`, sem stack trace, e o erro real aparece nos logs.
- [x] **CA-007 (CA-03):** Falha simulada da API Google dentro de `GetAvailableSlotsTool._run`/`CreateAppointmentTool._run` retorna mensagem segura padronizada.
- [x] **CA-008 (WH-07):** Com `OutboundMessageStore.record` lançando exceção e o POST HTTP bem-sucedido, `send_message`/`send_message_sync` retornam `True` e logam a falha de persistência.

---

## 6. Plano de Testes

### 6.1 Unitários

- Testar que `CleanAgentService.__init__` cria o cliente com `request_timeout` e `max_tokens` (inspecionar atributos do `ChatOpenAI` ou mockar o construtor).
- Testar `_run_loop` com `self._llm.invoke` mockado lançando `APITimeoutError` e depois `RateLimitError`: confirmar retry e mensagem de fallback amigável.
- Testar `_run_loop` com uma tool mockada lançando `Exception`: confirmar `ToolMessage` com `Erro:` seguro e ausência de stack trace.
- Testar `connection.get_db`: confirmar `PRAGMA busy_timeout` aplicado e `check_same_thread` explícito.
- Testar `whatsapp_service.send_message_sync` com `OutboundMessageStore.record` mockado lançando `sqlite3.Error`: retorno `True`.

### 6.2 Integração

- Simular `_try_claim_message_processing` com `get_db` lançando `OperationalError`: o webhook não retorna 500.
- Simular `ConversationStateService.get` (em `app.py:204`) lançando exceção: o fluxo segue com estado padrão.
- Disparar duas requisições ao `/webhook/message` com `process_message` lento (mock com `time.sleep`): confirmar que via `to_thread` elas progridem concorrentemente e não serializam o event loop.
- Simular tool de calendar com API Google indisponível (mock de `CalendarService.get_available_slots` lançando erro): resposta segura ao paciente.

### 6.3 Aceitação

- Validar cada item CA-001 a CA-008 via testes automatizados.
- Smoke test do webhook ponta a ponta com LLM e WhatsApp mockados, confirmando 0 respostas 500 nos cenários de falha acima.

### 6.4 Casos de Borda

- LLM retorna timeout em **todas** as tentativas do retry → mensagem amigável única, sem duplicar resposta.
- SQLite `database is locked` no claim **e** no `mark` da mesma requisição → sem 500, sem reprocessamento em loop.
- `record` falha mas POST de WhatsApp falha em seguida (caso o método seja chamado novamente) → retorno coerente.
- Tool lança exceção cujo `str(exc)` contém aspas/quebras de linha → mensagem segura permanece bem formada para o `ToolMessage`.
- Concorrência: scheduler async (lifespan, `app.py:95-103`) escrevendo enquanto o webhook escreve → `busy_timeout` evita falha imediata.

---

## 7. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| `to_thread` aumenta uso de threads sob pico | Média | Médio | SQLite por-thread já isolado (`connection.py:_local`); `busy_timeout` cobre concorrência; monitorar pool padrão do executor |
| `check_same_thread=False` mal aplicado causar corrupção | Baixa | Alto | Manter modelo `threading.local` (uma conexão por thread); preferir manter `check_same_thread=True` e documentar; só relaxar se necessário com serialização |
| Retry de LLM mascarar erro persistente | Média | Médio | Retry curto e limitado; logar cada tentativa; fallback claro ao paciente |
| Degradação de SQLite esconder bug real | Média | Médio | Logar em `error` com exceção original; não engolir silenciosamente |
| `max_tokens` baixo truncar respostas | Baixa | Médio | Calibrar valor com base nas respostas típicas da secretária (mensagens curtas) |
| Mensagem de erro segura genérica demais confundir LLM | Baixa | Baixo | Padronizar prefixo `Erro:` e instrução de próxima ação, como já feito em `:341-345` |

---

## 8. Dependências

### 8.1 Internas

- Nenhuma. Esta é a implementação base de estabilidade (pré-requisito de outras), conforme o PRD. As demais implementações de correção (resposta correta, escopo, marcação) dependem desta para rodar sobre uma API estável.

### 8.2 Externas

- `langchain_openai.ChatOpenAI` e SDK OpenAI (exceções `APITimeoutError`, `RateLimitError`).
- `httpx` (timeouts já em uso em `whatsapp_service.py:85` e `:120`).
- `sqlite3` (PRAGMAs `busy_timeout`, `journal_mode`).
- Google Calendar API (`googleapiclient`) para os testes de falha das tools.
- Evolution API (comportamento de reentrega em 500 — motivação do tratamento de erro).

---

## 9. Observações e Decisões de Design

- **WAL já existe:** `connection.py:100` já define `PRAGMA journal_mode=WAL`. A lacuna real é `busy_timeout` e a política explícita de `check_same_thread`. O texto do finding CONNECTION é tratado como "revisar/garantir", não "introduzir do zero".
- **`FindNextAvailableDayTool` já é defensiva:** seu `_run` (`calendar_tool.py:261-321`) já tem try/except global retornando `f"Erro ao buscar horarios: {exc}"`. A melhoria aqui é padronizar a mensagem para não vazar `exc` cru, alinhando ao restante.
- **`CalendarService.cancel_appointment` já trata erro** (`calendar_service.py:531-544`) retornando `False` e logando — bom padrão a replicar nas leituras.
- **Sem escalonamento silencioso:** decisão explícita do PRD — erro técnico do LLM gera mensagem de "tente novamente", não handoff automático. O handoff automático em `app.py:281-291` permanece restrito aos seus gatilhos atuais de conteúdo.
- **Sem sucesso silencioso:** em WH-07, o sucesso reportado refere-se à **entrega** (POST OK). A falha de persistência do espelho de saída é registrada em log, preservando rastreabilidade sem reportar falsa falha de entrega.
- **Escopo contido:** não se reescreve o motor para async nativo nem se altera a lógica de slots/regras de agendamento; apenas resiliência de IO e tratamento de erro.
