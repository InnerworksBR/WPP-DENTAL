# Robustez do Estado Conversacional

> **ID:** 003
> **Status:** 🟡 Planejada
> **Prioridade:** 🔴 Critica
> **Criada em:** 2026-06-15
> **Última atualização:** 2026-06-15
> **Autor:** Agente AI

---

## 1. Resumo Executivo

A camada de estado conversacional do WPP-DENTAL é hoje frágil em cinco pontos que se manifestam nas quatro queixas do dono (API com erro, resposta errada, fuga de escopo e marcação errada). O ponto mais grave é a desserialização em `ConversationState(**payload)` (`src/application/services/conversation_state_service.py:64`), que lança `TypeError` em **qualquer** chave legada ou desconhecida no `state_json` persistido — bastando uma alteração futura na dataclass para derrubar o webhook inteiro daquele telefone (schema drift).

Além disso: campos de lista (`offered_times`, `rejected_slots`, `excluded_dates`) não são saneados na leitura e podem chegar `null`/tipo errado aos consumidores que iteram sobre eles; o stage `awaiting_name_for_slot_confirmation` é **gravado** (`src/interfaces/http/app.py:861-863`) mas **nunca tratado** por handler algum, prendendo o paciente; estados `awaiting_*` (slot/plan/name) **nunca expiram**, ao contrário do handoff e da confirmação que têm janela; os caminhos que setam `stage="idle"` deixam resíduos de `intent`/`pending_*`/`reschedule_*`/`offered_*` que vazam entre fluxos; e `HandoffService.activate` (`src/application/services/handoff_service.py:30-43`) **sobrescreve todo** o `ConversationState`, destruindo o contexto de um agendamento em andamento.

Esta implementação torna a leitura/escrita de estado à prova de schema drift, sanea listas e strings, implementa o handler ausente, adiciona TTL aos estados `awaiting_*`, padroniza o reset estruturado ao voltar para `idle`, e preserva o contexto de agenda ao ativar o handoff.

## 2. Contexto e Motivação

### 2.1 Problema Atual

A persistência de estado vive em `ConversationStateService` (`src/application/services/conversation_state_service.py`), que serializa a dataclass `ConversationState` (linhas 12-36) via `json.dumps(asdict(state))` (linha 75) e a reconstrói com `ConversationState(**payload)` (linha 64). Problemas reais identificados (todos verificados no código):

- **CO-01 — Schema drift fatal (api_error).** `ConversationState(**payload)` em `conversation_state_service.py:64` recebe diretamente o dicionário carregado do banco. Se o `state_json` contiver uma chave que não existe mais (ou ainda não existe) na dataclass — situação normal após qualquer evolução do schema — o construtor lança `TypeError: __init__() got an unexpected keyword argument`. Como `get()` é chamado logo no início do webhook (`app.py:204`), o telefone afetado passa a falhar em **toda** mensagem. Hoje só `metadata` é tratado (linhas 60-62); o resto não tem proteção.

- **CO-02 — Listas não saneadas (alto).** `get()` só corrige `metadata` quando não é `dict` (`conversation_state_service.py:60-62`). Os campos `offered_times`, `rejected_slots` e `excluded_dates` (declarados como `list[str]` nas linhas 30/33/34) podem ser persistidos como `null` ou outro tipo e chegam crus ao `ConversationState`. Consumidores que iteram sobre eles (ex.: filtros de slot em `_handle_offered_slot_selection`, `app.py:912+`, e `_slot_satisfies_state_filters`) quebram ou se comportam errado ao iterar sobre `None`.

- **CO-03 — Stage órfão prende o paciente (wrong_response).** Em `_handle_pending_slot_plan` (`app.py:828-892`), quando o nome do paciente não é confiável, grava-se `state.stage = "awaiting_name_for_slot_confirmation"` (linhas 861-863) e pede-se o nome completo. Porém o dispatcher do webhook (`app.py:215-250`) só trata `awaiting_plan_for_slot_confirmation` (215), `CONFIRMATION_STAGE` (225) e `awaiting_cancel_confirmation` (240). **Nenhum** handler trata `awaiting_name_for_slot_confirmation`: a próxima mensagem (o nome) cai direto no LLM, o stage nunca é resolvido e o paciente fica preso sem fechar o agendamento.

- **CO-07 — Estados `awaiting_*` sem TTL (medio).** O handoff tem janela (`HandoffService.WINDOW_MINUTES = 30`, `handoff_service.py:14`) e a confirmação de consulta considera estado antigo como expirado após 2h via `get_updated_at` (`appointment_confirmation_service.py:283-303`). Já os estados `awaiting_plan_for_slot_confirmation`, `awaiting_name_for_slot_confirmation` e `awaiting_appointment_confirmation` **não expiram nunca** no fluxo do webhook: se o paciente abandona e volta dias depois, o dispatcher (`app.py:215-233`) ainda o reconduz a um fluxo morto com dados obsoletos.

- **CO-08 — Reset `idle` incompleto (wrong_booking).** Vários caminhos setam `stage="idle"` sem limpar os campos satélites: `app.py:616` (`_preserve_partial_reschedule_state`, que é proposital), `app.py:866`, `app.py:987`, `app.py:1192` e `app.py:1243`. Os campos `intent`, `pending_event_id`, `pending_slot_date/time`, `reschedule_event_id/label`, `offered_date/times` ficam residuais e vazam para o próximo fluxo (ex.: um `reschedule_event_id` antigo influenciando uma nova marcação), provocando marcação errada.

- **HO-01 — Handoff destrói contexto (wrong_booking).** `HandoffService.activate` (`handoff_service.py:30-43`) faz `ConversationStateService.save(phone, ConversationState(stage=..., metadata={...}))`, ou seja, **substitui** o estado inteiro por um objeto novo. Se o paciente estava em `pending_slot_date/time` ou em remarcação quando a doutora interveio, todo o contexto de agenda é perdido; ao expirar a janela de 30 min, o paciente recomeça do zero ou — pior — um fluxo subsequente marca em horário diferente do que estava pendente.

### 2.2 Impacto do Problema

- **Disponibilidade:** CO-01 transforma qualquer schema drift em outage por telefone (queixa “API toda hora dá erro”).
- **Correção das respostas:** CO-03 prende o paciente em um estado sem saída (queixa “responde errado”).
- **Integridade do agendamento:** CO-08 e HO-01 fazem dados de um fluxo vazarem para outro, causando marcação em horário errado / contexto perdido (queixa “marca errado e traz transtorno”).
- **Higiene operacional:** CO-02 e CO-07 produzem falhas intermitentes difíceis de reproduzir e conversas “zumbis” presas em estados antigos.

### 2.3 Soluções Consideradas

| Solução | Prós | Contras | Decisão |
|---|---|---|---|
| Filtrar `payload` pelos campos da dataclass + sanear listas/strings no `get()` | Corrige CO-01/CO-02 na origem; um único ponto; tolerante a drift futuro | Exige introspecção dos campos da dataclass | **Adotada** |
| Migração de banco reescrevendo todo `state_json` legado | Limpa dados antigos de uma vez | Não previne drift futuro; risco em produção; não resolve `null` em runtime | Rejeitada |
| `try/except TypeError` em volta de `ConversationState(**payload)` | Trivial | Mascara o erro retornando estado vazio e perde contexto válido silenciosamente | Rejeitada (parcial — usar como rede secundária, não como fix primário) |
| Implementar handler dedicado para `awaiting_name_for_slot_confirmation` espelhando `_handle_pending_slot_plan` | Resolve CO-03 reusando padrão existente; determinístico | Mais um handler no dispatcher | **Adotada** |
| TTL central em `ConversationStateService.get()` para todos os stages | Um só ponto | Acopla regra de negócio à camada de persistência; quebra handoff/confirmação que já têm TTL próprio | Rejeitada |
| TTL no dispatcher do webhook usando `get_updated_at`, só para `awaiting_*` | Reusa padrão de `appointment_confirmation_service.py:285`; isolado no fluxo | Lógica de TTL fica no `app.py` | **Adotada** |
| Helper `reset_to_idle(state)` que zera satélites preservando identidade | Padroniza CO-08; reduz duplicação | Precisa revisar cada call site | **Adotada** |
| HandoffService preservar campos de agenda do estado atual | Resolve HO-01 sem perder contexto | Handoff passa a depender do estado anterior | **Adotada** |

## 3. Especificação Técnica

### 3.1 Visão Geral da Arquitetura

As mudanças ficam concentradas na camada de aplicação:

- `ConversationStateService.get()` passa a (a) filtrar `payload` pelos nomes de campo válidos da dataclass `ConversationState` antes de instanciar e (b) coagir/validar tipos de listas e strings.
- `app.py` ganha o handler `_handle_pending_slot_name` (CO-03), uma verificação de TTL para stages `awaiting_*` no dispatcher (CO-07) e um helper `reset_to_idle` aplicado nos call sites de `stage="idle"` (CO-08).
- `HandoffService.activate` passa a carregar o estado atual e preservar os campos de agenda (HO-01).

Nenhuma alteração de schema SQL é necessária; a tabela `conversation_state` (phone, state_json, updated_at) permanece igual e `get_updated_at` (`conversation_state_service.py:79-92`) já fornece o timestamp para o TTL.

### 3.2 Componentes Afetados

| Componente | Tipo | Ação | Descrição |
|---|---|---|---|
| `conversation_state_service.py` :: `ConversationStateService.get` | Método | Modificar | Filtrar `payload` por campos válidos (CO-01) e sanear listas/strings (CO-02). |
| `conversation_state_service.py` :: `ConversationState` | Dataclass | Manter | Fonte de verdade dos campos válidos; usada via `dataclasses.fields`. |
| `app.py` :: dispatcher do webhook (linhas 215-250) | Bloco de fluxo | Modificar | Roteamento do novo stage + checagem de TTL `awaiting_*` (CO-03, CO-07). |
| `app.py` :: `_handle_pending_slot_name` | Função | Criar | Coleta o nome e retoma a confirmação do slot pendente (CO-03). |
| `app.py` :: `_handle_pending_slot_plan` (828-892) | Função | Ajustar | Garante coerência ao gravar `awaiting_name_for_slot_confirmation`. |
| `app.py` :: `reset_to_idle` | Função/helper | Criar | Zera satélites preservando identidade ao voltar a `idle` (CO-08). |
| `app.py` :: call sites `stage="idle"` (866, 987, 1192, 1243) | Linhas | Modificar | Usar `reset_to_idle`. (`616` é exceção justificada — preserva resíduos de propósito.) |
| `handoff_service.py` :: `HandoffService.activate` (30-43) | Método | Modificar | Preservar campos de agenda ao ativar handoff (HO-01). |

### 3.3 Interfaces e Contratos

- `ConversationStateService.get(phone: str) -> ConversationState` — **assinatura inalterada**; passa a garantir: nunca lança por chave desconhecida; `offered_times`, `rejected_slots`, `excluded_dates` sempre `list[str]`; campos `str` nunca `None`.
- `async _handle_pending_slot_name(phone, text, contact_name, message_id) -> JSONResponse | None` — espelha o contrato de `_handle_pending_slot_plan` (`app.py:828`): retorna `None` quando não há slot pendente (deixa seguir o fluxo) e `JSONResponse` quando resolve.
- `reset_to_idle(state: ConversationState) -> ConversationState` — zera `intent`, `pending_event_id/label`, `reschedule_event_id/label`, `pending_slot_date/time`, `offered_date`, `offered_times`, `rejected_slots`, `excluded_dates`, `requested_*`, `earliest_time`; mantém `patient_name`, `plan_name`, `metadata`; seta `stage="idle"`. (Variante com `keep` opcional para casos como o reschedule parcial.)
- `HandoffService.activate(phone, duration_minutes=None) -> datetime` — assinatura inalterada; passa a mesclar `pending_slot_date/time`, `intent`, `reschedule_event_id/label`, `pending_event_id/label` do estado atual no novo estado de handoff.

### 3.4 Modelos de Dados

A dataclass `ConversationState` (`conversation_state_service.py:12-36`) é mantida sem campos novos. Conjuntos relevantes para o saneamento:

- **Campos lista (`list[str]`):** `offered_times` (30), `rejected_slots` (33), `excluded_dates` (34).
- **Campos `dict`:** `metadata` (28) — já tratado.
- **Demais campos `str`:** todos os outros (16-27, 29, 31-32, 35-36).
- **Campos de agenda preservados no handoff (HO-01):** `intent`, `pending_event_id`, `pending_event_label`, `reschedule_event_id`, `reschedule_event_label`, `pending_slot_date`, `pending_slot_time`.

A lista canônica de nomes válidos será obtida em runtime via `{f.name for f in dataclasses.fields(ConversationState)}`, evitando hardcode e tornando o filtro automaticamente correto a cada evolução do schema.

### 3.5 Fluxo de Execução

1. **Leitura robusta (CO-01/CO-02):** `get()` carrega `state_json` → valida `dict` → calcula `valid = {f.name for f in fields(ConversationState)}` → `payload = {k: v for k, v in payload.items() if k in valid}` → sanea `metadata` (já existente), `offered_times/rejected_slots/excluded_dates` (coagir para `list[str]`, descartando itens não-string), e campos `str` `None`→`""` → `ConversationState(**payload)`.
2. **Dispatch com TTL (CO-07):** após `current_state = ConversationStateService.get(phone)` (`app.py:204`), se `stage` ∈ {`awaiting_plan_for_slot_confirmation`, `awaiting_name_for_slot_confirmation`, `CONFIRMATION_STAGE`}, comparar `get_updated_at(phone)` com `utcnow()`; se exceder o TTL (proposto: 60 min para slot/plan/name), `clear(phone)` e recarregar `current_state` antes de rotear.
3. **Roteamento do novo stage (CO-03):** adicionar, junto ao bloco `awaiting_plan_for_slot_confirmation` (215-223), o ramo para `awaiting_name_for_slot_confirmation` chamando `_handle_pending_slot_name`.
4. **Handler do nome (CO-03):** valida `pending_slot_date/time` (se ausentes, `clear` e retorna `None`); extrai o nome do `text`; faz `PatientService.upsert(phone, nome, state.plan_name)`; aplica `reset_to_idle`; envia `_build_slot_confirmation_request_message(...)` (`app.py:647`).
5. **Reset estruturado (CO-08):** nos call sites 866/987/1192/1243, substituir `state.stage = "idle"` por `state = reset_to_idle(state)` antes do `save`.
6. **Handoff preservando contexto (HO-01):** `activate` lê o estado atual, monta o estado de handoff e copia os campos de agenda antes de salvar.

### 3.6 Tratamento de Erros

- **TypeError por chave desconhecida:** eliminado na origem pelo filtro de campos; como rede secundária, `ConversationState(**payload)` fica envolto em `try/except TypeError` que loga `logger.warning` e retorna `ConversationState()` em vez de propagar.
- **`json.JSONDecodeError` / payload não-dict:** comportamento atual mantido (`conversation_state_service.py:54-58`) — retorna estado vazio.
- **Lista com itens não-string:** filtra item a item (`[x for x in valor if isinstance(x, str)]`); `null`→`[]`.
- **TTL expirado:** `clear(phone)` e fluxo segue para o LLM como conversa nova; nunca trava nem marca com dados velhos.
- **Handler do nome sem slot pendente:** `clear(phone)` e retorna `None` (mesmo padrão de `_handle_pending_slot_plan`, `app.py:836-838`).
- **Falha de entrega no novo handler:** segue o padrão existente — `_mark_message_failed` + `HTTPException(502)` (espelhando `app.py:876-879`).

## 4. Requisitos

### 4.1 Requisitos Funcionais

- **RF-001 (CO-01):** `ConversationStateService.get()` deve filtrar o `payload` pelos campos válidos de `ConversationState` antes de instanciar, nunca lançando `TypeError` por chave legada/desconhecida no `state_json`.
- **RF-002 (CO-02):** `get()` deve garantir que `offered_times`, `rejected_slots` e `excluded_dates` sejam sempre `list[str]` (coagindo `null`/tipo errado para `[]` e descartando itens não-string) e que campos `str` nunca sejam `None`.
- **RF-003 (CO-03):** O dispatcher do webhook deve tratar o stage `awaiting_name_for_slot_confirmation` por meio de `_handle_pending_slot_name`, coletando o nome e retomando a confirmação do slot pendente sem prender o paciente.
- **RF-004 (CO-07):** Estados `awaiting_plan_for_slot_confirmation`, `awaiting_name_for_slot_confirmation` e `awaiting_appointment_confirmation` devem expirar via TTL baseado em `ConversationStateService.get_updated_at`, sendo limpos antes do roteamento quando antigos.
- **RF-005 (CO-08):** Todo caminho que retorna o fluxo a `stage="idle"` (exceto o reschedule parcial intencional, `app.py:607-622`) deve aplicar o reset estruturado, zerando `intent`/`pending_*`/`reschedule_*`/`offered_*`/`requested_*` e preservando `patient_name`/`plan_name`/`metadata`.
- **RF-006 (HO-01):** `HandoffService.activate` deve preservar os campos de agenda em andamento (`pending_slot_date/time`, `intent`, `reschedule_event_id/label`, `pending_event_id/label`) ao ativar o handoff, sem destruir o contexto de agendamento.

### 4.2 Requisitos Não-Funcionais

- **RNF-001 (Compatibilidade):** A correção deve ler sem erro qualquer `state_json` previamente gravado, incluindo registros legados com chaves obsoletas.
- **RNF-002 (Sem schema SQL):** Nenhuma migração da tabela `conversation_state` é necessária.
- **RNF-003 (Observabilidade):** Descartes de chave desconhecida, saneamento de lista e expiração de TTL devem gerar log (`logger.warning`/`logger.info`) com `phone` para diagnóstico, sem vazar PII além do já registrado.
- **RNF-004 (Idempotência):** `get()` deve ser puro de efeitos colaterais sobre o banco (não grava); a limpeza por TTL ocorre explicitamente no dispatcher.
- **RNF-005 (Manutenibilidade):** O conjunto de campos válidos e os campos preservados em reset/handoff devem derivar de `dataclasses.fields`, evitando listas hardcoded que se desatualizam.

### 4.3 Restrições

- Não alterar a assinatura pública de `get`, `save`, `clear`, `get_updated_at` nem de `HandoffService.activate`.
- Não introduzir dependências externas novas.
- Respeitar as regras do PRD: na expiração/dúvida, escalar/recomeçar com segurança; nunca marcar com dados residuais (CO-08/HO-01 servem diretamente à regra “ao final só 1 evento ativo, sem sucesso silencioso”).
- Manter o fluxo determinístico do `app.py` antes do LLM.

## 5. Critérios de Aceitação

- [ ] **CA-001 (CO-01):** Carregar um `state_json` contendo a chave inexistente `"legacy_field": "x"` retorna um `ConversationState` válido sem lançar `TypeError`.
- [ ] **CA-002 (CO-01):** Após adicionar/remover hipoteticamente um campo da dataclass, `get()` continua lendo registros antigos sem erro (cobertura por teste com payload extra).
- [ ] **CA-003 (CO-02):** `state_json` com `"offered_times": null` retorna `offered_times == []`; com `"rejected_slots": [1, "10:00", null]` retorna `["10:00"]`.
- [ ] **CA-004 (CO-02):** Qualquer campo `str` gravado como `null` é lido como `""`.
- [ ] **CA-005 (CO-03):** Com `stage="awaiting_name_for_slot_confirmation"` e `pending_slot_date/time` setados, ao enviar um nome válido o paciente recebe a mensagem de confirmação do slot e o stage sai do `awaiting_*`.
- [ ] **CA-006 (CO-03):** Com o mesmo stage, mas sem `pending_slot_date/time`, o estado é limpo e o fluxo segue (não trava).
- [ ] **CA-007 (CO-07):** Um estado `awaiting_*` com `updated_at` além do TTL é limpo antes do roteamento e a mensagem segue como conversa nova.
- [ ] **CA-008 (CO-07):** Um estado `awaiting_*` recente (dentro do TTL) continua sendo tratado pelo handler correspondente.
- [ ] **CA-009 (CO-08):** Após qualquer transição a `idle` (exceto reschedule parcial), `intent`, `pending_*`, `reschedule_*` e `offered_*` ficam vazios; `patient_name`/`plan_name`/`metadata` permanecem.
- [ ] **CA-010 (HO-01):** Ativar handoff com `pending_slot_date/time` e `intent="reschedule"` no estado atual resulta em um estado de handoff que preserva esses campos; ao expirar, o contexto de agenda ainda existe.
- [ ] **CA-011 (Regressão):** Os fluxos já existentes `awaiting_plan_for_slot_confirmation`, `CONFIRMATION_STAGE` e `awaiting_cancel_confirmation` continuam funcionando sem alteração de comportamento observável.

## 6. Plano de Testes

### 6.1 Unitários

- **UT-01 (CO-01):** `get()` com payload contendo chave desconhecida → `ConversationState` válido, sem exceção (mockar a leitura do banco).
- **UT-02 (CO-01):** rede secundária — payload que ainda assim cause `TypeError` é capturado e retorna `ConversationState()` com log de warning.
- **UT-03 (CO-02):** matriz de tipos inválidos para `offered_times`/`rejected_slots`/`excluded_dates` (`null`, `"x"`, `[1, null, "10:00"]`) → sempre `list[str]` saneada.
- **UT-04 (CO-02):** campos `str` com `null` → `""`.
- **UT-05 (CO-08):** `reset_to_idle` zera satélites e preserva `patient_name`/`plan_name`/`metadata`/`stage="idle"`.
- **UT-06 (HO-01):** `HandoffService.activate` sobre um estado com campos de agenda → estado de handoff com `stage="handoff_active"` e campos de agenda preservados + `metadata[handoff_until_utc]` presente.

### 6.2 Integração

- **IT-01 (CO-03):** webhook com `stage="awaiting_name_for_slot_confirmation"` e slot pendente; enviar nome → resposta de confirmação do slot e estado fora do `awaiting_*` (com `WhatsAppService`/`CalendarService` mockados).
- **IT-02 (CO-07):** webhook com estado `awaiting_*` antigo (manipular `updated_at`) → estado limpo e mensagem roteada ao LLM como nova.
- **IT-03 (CO-01 e2e):** gravar via `save` um estado, injetar manualmente chave legada no `state_json` do banco de teste, disparar webhook → sem `HTTPException 500`/`TypeError`.
- **IT-04 (HO-01):** simular agendamento pendente → ativar handoff → expirar janela → confirmar que o contexto de agenda sobreviveu.

### 6.3 Aceitação

- **AT-01:** Percorrer CA-001…CA-011 manualmente em ambiente de teste e marcar cada checkbox.
- **AT-02:** Cenário de ponta a ponta: paciente sem nome confiável escolhe horário → informa convênio → informa nome → recebe confirmação e evento é criado como “Nome - Telefone” (regra PRD), validando CO-03 + CO-08 juntos.

### 6.4 Casos de Borda

- **EC-01:** `state_json` vazio/`null`/JSON inválido → `ConversationState()` (comportamento atual mantido).
- **EC-02:** `metadata` como lista em vez de dict → forçado para `{}` (comportamento atual mantido).
- **EC-03:** TTL exatamente no limite (igualdade) — definir convenção (`>` expira, `==` não) e testar.
- **EC-04:** Nome enviado no handler vindo apenas como número de telefone/dígitos → tratar como inválido e repedir (espelhando `app.py:855-859` / `app.py:923`).
- **EC-05:** Handoff ativado quando não há contexto de agenda → estado de handoff “limpo”, sem campos espúrios.
- **EC-06:** Reschedule parcial (`_preserve_partial_reschedule_state`, `app.py:607-622`) NÃO deve ser afetado pelo `reset_to_idle` — confirmar que segue preservando os resíduos de propósito.

## 7. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| `reset_to_idle` zerar resíduo necessário (ex.: reschedule parcial) | Média | Alto | Não aplicar em `_preserve_partial_reschedule_state` (616); revisar cada call site; EC-06. |
| TTL curto demais derrubar paciente que está só pensando | Média | Médio | TTL de 60 min para slot/plan/name; medir e ajustar; log de expiração (RNF-003). |
| Handoff preservar campos que deveriam ser descartados | Baixa | Médio | Preservar apenas a lista canônica de agenda (3.4); UT-06/IT-04. |
| Filtro por `fields()` ocultar bug real de gravação de chave inválida | Baixa | Baixo | Logar a chave descartada (RNF-003) para detectar gravações indevidas. |
| Regressão em fluxos `awaiting_*` existentes | Baixa | Alto | CA-011 + suíte de regressão (IT) antes do merge. |

## 8. Dependências

### 8.1 Internas

- **Implementação 001** (pré-requisito): base de robustez/erros sobre a qual esta se apoia.
- **Implementação 002 — Recuperação da Rede de Testes** (pré-requisito): suíte verde para validar com segurança as mudanças no estado conversacional.
- Módulos diretamente envolvidos: `ConversationStateService` e `ConversationState` (`conversation_state_service.py`), dispatcher e helpers do webhook (`app.py`), `HandoffService` (`handoff_service.py`), `AppointmentConfirmationService` (referência do padrão de TTL, `appointment_confirmation_service.py:283-303`), `PatientService.upsert`, `_build_slot_confirmation_request_message` (`app.py:647`).

### 8.2 Externas

- SQLite via `get_db()` (`src/infrastructure/persistence/connection.py`) — tabela `conversation_state`.
- Biblioteca padrão `dataclasses` (`fields`), `json`, `datetime`.
- N/A — nenhuma dependência de terceiros nova é introduzida.

## 9. Observações e Decisões de Design

- **Filtro por introspecção, não hardcode.** Usar `dataclasses.fields(ConversationState)` para o conjunto válido garante que o fix continue correto após qualquer evolução futura do schema — atacando a causa raiz do schema drift (CO-01), não apenas o sintoma atual.
- **TTL no dispatcher, não no `get()`.** Mantém `get()` sem efeito colateral no banco (RNF-004) e reusa o padrão já validado em `appointment_confirmation_service.py:285`. A camada de persistência não deve conhecer regra de negócio de expiração.
- **Reset estruturado preserva identidade.** `patient_name`, `plan_name` e `metadata` sobrevivem ao `reset_to_idle` porque representam identidade/configuração do contato, não o fluxo transitório — alinhado com a necessidade de continuidade entre conversas.
- **Exceção consciente em `app.py:616`.** `_preserve_partial_reschedule_state` seta `idle` propositalmente mantendo `pending_slot_*` e `metadata` de remarcação parcial; essa função fica fora do `reset_to_idle` por design e é coberta por EC-06.
- **Handler do nome espelha o do plano.** `_handle_pending_slot_name` reaproveita a estrutura de `_handle_pending_slot_plan` (validação de slot, envio, marcação de mensagem, `JSONResponse`) para consistência e menor superfície de bug.
- **Rede dupla em CO-01.** Filtro de campos (primário) + `try/except TypeError` (secundário) garantem que nenhum dado malformado derrube o webhook, ainda que de forma degradada.
