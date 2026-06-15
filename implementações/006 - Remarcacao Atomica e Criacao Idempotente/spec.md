# Remarcacao Atomica e Criacao Idempotente

> **ID:** 006
> **Status:** 🟡 Planejada
> **Prioridade:** 🔴 Critica
> **Criada em:** 2026-06-15
> **Última atualização:** 2026-06-15
> **Autor:** Agente AI

---

## 1. Resumo Executivo

Esta implementacao elimina a principal fonte de "marcacao errada que traz transtorno" (queixa 4 do dono): a remarcacao que **deixa duas consultas ativas** e a criacao de consulta que **duplica eventos** em reprocessamento de webhook.

Hoje existem dois motores de agendamento no codigo:

- **Caminho deterministico** em `src/interfaces/http/app.py` → `_handle_offered_slot_selection` (1019-1093). Esse caminho **ja** trata a remarcacao como troca (cria o novo evento, cancela o antigo via `state.reschedule_event_id`, e em falha parcial preserva estado + alerta a doutora em vez de "sucesso silencioso"). Veja `app.py:1035-1065`.
- **Caminho do agente (LLM)** em `src/application/services/clean_agent_service.py` → `_run_loop` (292-389), via a tool `CreateAppointmentTool` (`src/interfaces/tools/calendar_tool.py:332-363`). Esse caminho **NAO** trata remarcacao: `_run_loop` nunca le `state.reschedule_event_id` nem chama `cancelar_agendamento`. O LLM e apenas *instruido* a identificar a consulta antiga (prompt linhas 234-235), sem nenhuma garantia deterministica. Resultado: **2 consultas ativas** (AG-02 / CA-02).

Alem disso, em ambos os caminhos a criacao do evento ocorre **antes** do envio ao paciente. Se a entrega falhar, o handler levanta `HTTPException(502)` (`app.py:1103-1106`), a Evolution API reentrega o webhook e — como o `message_id` foi marcado `failed` e pode ser **reclamado** (`_try_claim_message_processing`, `app.py:1440`) — o fluxo roda de novo e **cria outro evento** no Calendar (WH-01 / double-book).

A solucao tem tres pilares:

1. **Roteamento da remarcacao sempre pelo fluxo deterministico** + um guarda no motor LLM que impede `criar_agendamento` quando `state.intent == "reschedule"` (a remarcacao nunca deve concluir "sozinha" pelo LLM sem a troca atomica).
2. **Idempotencia da criacao de consulta**: antes de inserir no Calendar, checar se ja existe evento para o mesmo `(telefone, slot)` e reutiliza-lo, evitando double-book em reprocessamento.
3. **Ordem segura e troca consistente** alinhadas a estrategia de remarcacao parcial ja decidida na implementacao 000 (criar novo → cancelar antigo → se cancelar falhar, NAO confirmar; preservar estado + alertar).

## 2. Contexto e Motivação

### 2.1 Problema Atual

**AG-02 / CA-02 (CRITICO — wrong_booking): remarcacao pelo agente cria evento novo sem cancelar o antigo.**
Em `clean_agent_service.py`, `_run_loop` (292-389) executa as tool calls do LLM. O bloco de validacao de `criar_agendamento` (319-347) so verifica: (a) se o slot foi ofertado (`_is_offered_slot`) e (b) se nome/plano sao validos. **Nao ha qualquer leitura de `state.reschedule_event_id` nem chamada a `cancelar_agendamento`**. A limpeza pos-agendamento (380-384) apenas zera a oferta. Portanto, quando o paciente esta remarcando e o fluxo cai no motor LLM, o `CreateAppointmentTool` (`calendar_tool.py:344-363`) cria o novo evento e o antigo permanece ativo → **duas consultas**. `grep` confirma: as strings `reschedule`, `reschedule_event_id`, `remarc`, `reagend` **nao aparecem no corpo executavel** de `clean_agent_service.py` (so no texto do prompt, linhas 197/232/234/235).

**CA-05 (alto): novo evento e criado ANTES de cancelar o antigo.**
Mesmo no caminho deterministico, a ordem e: cria (`app.py:1020`) → cancela (`app.py:1037`). Se o cancelamento falhar (`cancel_appointment` retorna `False`, `calendar_service.py:531-544`), ficam dois eventos. O caminho deterministico ja mitiga isso com `_preserve_partial_reschedule_state` + alerta (1039-1065), mas o caminho do agente nao tem nenhuma mitigacao.

**WH-01 (CRITICO — wrong_booking): evento criado antes da entrega; 502 + reentrega → double-book.**
Em `_handle_offered_slot_selection`, a criacao ocorre em 1019-1024; o envio so em 1095 (`_send_response`). Se a entrega falhar, `app.py:1103` levanta `HTTPException(502)` e marca o `message_id` como `failed` (1097-1102). Como `_try_claim_message_processing` (`app.py:1420-1451`) **reclama** mensagens em estado `failed` (1440), a reentrega da Evolution executa o handler de novo e chama `create_appointment_if_available` novamente — **criando um segundo evento** para o mesmo slot/paciente. `create_appointment_if_available` (`calendar_service.py:492-529`) so bloqueia por **conflito de horario** (`_slot_conflicts`, 524-525); como o primeiro evento ocupa exatamente o mesmo slot, a segunda criacao falharia por conflito **apenas se o primeiro evento ja estiver visivel** na busca de eventos — o que e fragil (latencia de propagacao do Calendar, normalizacao de janela em `_slot_conflicts`). A criacao **nao** e idempotente por `(telefone, slot)`.

**IDEMPOTENCIA: reprocessamento de webhook recria evento.**
A chave de idempotencia por `message_id` ja existe (tabela `processed_messages`, `app.py:1420-1475`), porem ela protege o *processamento da mensagem*, nao a *operacao de criacao no Calendar*. Em qualquer caminho de reclaim (status `failed` ou `processing` obsoleto, `app.py:1440`) a criacao pode repetir.

### 2.2 Impacto do Problema

- **Negocio:** duas consultas no mesmo telefone (ou consulta antiga + nova simultaneas) geram conflito de agenda, paciente comparece no horario errado, e a doutora perde slot util — exatamente a queixa "marca errado e traz transtorno".
- **Confiabilidade:** double-book por reentrega gera erro silencioso e dificil de auditar; a doutora so percebe ao ver a agenda.
- **Consistencia:** viola a regra do PRD "remarcacao = troca consistente; ao final so 1 evento ativo, sem sucesso silencioso em falha parcial".

### 2.3 Soluções Consideradas

| Solução | Prós | Contras | Decisão |
|---|---|---|---|
| **A. Rotear toda remarcacao pelo fluxo deterministico e bloquear `criar_agendamento` no LLM quando `intent==reschedule`** | Reaproveita a troca atomica + alerta parcial ja existentes (`app.py:1035-1065`); um unico ponto de verdade; baixo risco | Exige que o `intent=="reschedule"` esteja sempre setado antes do LLM; precisa de um guarda novo em `_run_loop` | **ESCOLHIDA** (combinada com B e C) |
| **B. Implementar a troca atomica tambem dentro do LLM (ler `reschedule_event_id` e forcar `cancelar_agendamento`)** | Cobre o caso em que o deterministico nao captura | Duplica logica de troca/alerta em dois motores; maior superficie de bug; LLM pode ignorar ordem | Parcial — usada apenas como **rede de seguranca** (guarda que bloqueia, nao que executa a troca no LLM) |
| **C. Idempotencia de criacao por `(telefone, slot)` em `create_appointment_if_available`** | Elimina double-book independentemente do caminho; protege contra reentrega 502 | Custa uma busca extra (`find_appointments_by_phone`) por criacao | **ESCOLHIDA** |
| **D. Inverter ordem para cancelar antigo ANTES de criar novo** | Nunca deixa 2 eventos por falha de cancelamento | Se a criacao do novo falhar, paciente fica **sem** consulta (pior UX); contraria a estrategia 000 | **REJEITADA** — 000 ja decidiu criar→cancelar→preservar parcial |
| **E. Mover envio antes da criacao** | Evita 502 pos-criacao | Confirmaria ao paciente antes de garantir o evento; pode confirmar slot que falhou | **REJEITADA** |

## 3. Especificação Técnica

### 3.1 Visão Geral da Arquitetura

Mantem-se o fluxo deterministico de `app.py` como **unico** caminho que conclui agendamento/remarcacao. O motor LLM (`clean_agent_service.py`) passa a ter um **guarda de remarcacao**: enquanto `state.intent == "reschedule"`, qualquer `criar_agendamento` disparado pelo LLM e bloqueado com uma `ToolMessage` instrutiva (mesmo padrao dos guardas ja existentes em 319-347), forcando que a conclusao ocorra pelo `_handle_offered_slot_selection`. A criacao no Calendar (`create_appointment_if_available`) ganha idempotencia por `(telefone, slot)`.

### 3.2 Componentes Afetados

| Componente | Tipo | Ação | Descrição |
|---|---|---|---|
| `src/application/services/clean_agent_service.py` → `_run_loop` (292-389) | Servico (motor LLM) | Modificar | Adicionar guarda: se `state.intent == "reschedule"`, bloquear `criar_agendamento` com `ToolMessage` e nao executar a tool (mesma mecanica de 330/346). |
| `src/infrastructure/integrations/calendar_service.py` → `create_appointment_if_available` (492-529) | Integracao Calendar | Modificar | Antes do `insert`, dentro do `_APPOINTMENT_CREATION_LOCK`, checar evento ja existente para `(telefone, slot)` via `find_appointments_by_phone`; se existir, **retornar o evento existente** (idempotente) em vez de criar outro. |
| `src/interfaces/http/app.py` → `_handle_offered_slot_selection` (1019-1093) | Handler webhook | Modificar | Garantir ordem segura (criar→cancelar→preservar parcial) e que a criacao reutilize evento idempotente; confirmar que o reclaim por `failed` nao recrie. |
| `src/interfaces/tools/calendar_tool.py` → `CreateAppointmentTool._run` (344-363) | Tool | Modificar | Encaminhar a idempotencia: a mensagem de retorno deve refletir reuso de evento existente (sem texto enganoso de "nova" consulta) e nunca usar para remarcacao. |
| `src/application/services/conversation_state_service.py` (state.intent / reschedule_event_id) | Estado | Reutilizar | Campos `intent`, `reschedule_event_id`, `reschedule_event_label`, `metadata` ja existem e sao usados em `app.py:615-622, 1035-1065, 1242-1245`. Sem mudanca de schema. |

### 3.3 Interfaces e Contratos

**Guarda no `_run_loop`** (novo, dentro do laco `for call in response.tool_calls`, antes da execucao da tool, no mesmo lugar dos guardas atuais 319-347):

```
if call["name"] == "criar_agendamento" and state.intent == "reschedule":
    result = (
        "Erro interno: remarcacao deve ser concluida pelo fluxo de selecao de horario "
        "(troca atomica), nao por criar_agendamento. Ofereca o novo horario e aguarde a "
        "escolha do paciente; a troca sera feita automaticamente."
    )
    messages.append(ToolMessage(content=result, tool_call_id=call["id"]))
    continue
```

**`create_appointment_if_available` (assinatura inalterada)** — `calendar_service.py:492`:
`create_appointment_if_available(patient_name: str, patient_phone: str, start_time: datetime) -> dict`
Novo contrato interno: dentro do `with _APPOINTMENT_CREATION_LOCK` (524), antes de `_slot_conflicts`, procurar evento existente cujo `summary` bata com o telefone (`find_appointments_by_phone`, 595-627) **e** cujo `start.dateTime` seja igual a `start_sp`. Se encontrado → retornar esse evento (idempotente). Caso contrario, manter o fluxo atual (`_slot_conflicts` + `create_appointment`).

### 3.4 Modelos de Dados

- **Tabela `processed_messages`** (`app.py:1420-1475`): reutilizada como esta. `status ∈ {processing, processed, failed}`; idempotencia por `message_id`. Sem alteracao de schema.
- **Estado de conversa** (`ConversationStateService`): campos reutilizados — `intent` (`"reschedule"`), `reschedule_event_id`, `reschedule_event_label`, `pending_slot_date/time`, `metadata["partial_reschedule_new_event_id"]`. Sem alteracao de schema.
- **Evento Google Calendar**: chave logica de idempotencia = `(summary contendo telefone normalizado, start.dateTime == slot)`. Formato do `summary` mantido: `"Nome - Telefone"` (`calendar_service.py:470`). N/A — nao ha tabela propria de eventos; a fonte da verdade e o Calendar.

### 3.5 Fluxo de Execução

**Remarcacao (estado correto, deterministico) — caminho feliz:**
1. Paciente pede remarcar → `_handle_appointment_confirmation` (1241-1255) seta `intent="reschedule"` + `reschedule_event_id`.
2. Paciente escolhe novo horario → `_handle_offered_slot_selection` (1019).
3. `create_appointment_if_available` (1020): **idempotente** — se ja houver evento para `(telefone, slot)` reutiliza; senao cria.
4. `intent == "reschedule"` (1035): cancela `reschedule_event_id` (1037).
5. Cancelamento OK → mensagem de confirmacao + `ConversationStateService.clear` (1074-1079). Ao final: **1 evento ativo**.

**Remarcacao — falha de cancelamento (parcial):**
- `cancelled == False` (1038) → `_preserve_partial_reschedule_state` (1039) + alerta a doutora (1046) + mensagem honesta de "pendente" (1061). **Nao** confirma sucesso. Mantem-se a estrategia 000.

**Remarcacao via LLM (rede de seguranca):**
- LLM tenta `criar_agendamento` com `intent=="reschedule"` → **bloqueado** pelo novo guarda; nenhum evento e criado pelo LLM; a conclusao volta ao deterministico.

**Reentrega 502 (idempotencia):**
1. Primeira passada cria o evento; entrega falha → `HTTPException(502)` + `_mark_message_failed` (1097-1106).
2. Evolution reentrega; `_try_claim_message_processing` reclama (status `failed`, 1440).
3. `create_appointment_if_available` roda de novo, mas agora **encontra o evento ja criado** para `(telefone, slot)` e o **reutiliza** → nao ha segundo evento. Se a entrega tiver sucesso nesta passada, marca `processed`.

### 3.6 Tratamento de Erros

- **Cancelamento do antigo falha:** `cancel_appointment` retorna `False` (`calendar_service.py:537-544`) → caminho parcial (preservar estado + alerta), sem confirmar. Ja existente.
- **Slot indisponivel na confirmacao:** `create_appointment_if_available` levanta `ValueError` (492-528) → `slot_confirmation_unavailable` (`app.py:1025-1032`).
- **Entrega ao paciente falha:** `HTTPException(502)` + `_mark_message_failed` → reentrega segura por idempotencia (acima).
- **LLM insiste em criar na remarcacao:** guarda bloqueia e devolve `ToolMessage` instrutiva; detector de loop existente (`seen_calls`, 312-316) evita repeticao infinita.
- **Busca de evento idempotente falha (excecao do Calendar):** tratar como "nao encontrado" e prosseguir com o caminho de criacao normal protegido por `_slot_conflicts` (degradacao segura — pior caso volta ao comportamento atual, nunca pior).

## 4. Requisitos

### 4.1 Requisitos Funcionais

- **RF-001 (AG-02/CA-02):** No motor LLM (`_run_loop`, 292-389), quando `state.intent == "reschedule"`, a tool `criar_agendamento` DEVE ser bloqueada (nao executada) e responder com `ToolMessage` instrutiva, garantindo que a remarcacao seja concluida apenas pelo fluxo deterministico de troca atomica.
- **RF-002 (AG-02/CA-02):** Ao final de qualquer remarcacao bem-sucedida DEVE existir exatamente **1** evento ativo para o paciente (o novo), com o antigo cancelado.
- **RF-003 (CA-05):** A troca deve seguir a ordem criar-novo → cancelar-antigo; se o cancelamento falhar, o sistema NAO DEVE confirmar sucesso ao paciente — deve preservar o estado parcial e alertar a doutora (reuso de `_preserve_partial_reschedule_state` + `_send_scope_alert`, 1039-1065).
- **RF-004 (WH-01/IDEMPOTENCIA):** `create_appointment_if_available` (492-529) DEVE ser idempotente por `(telefone, slot)`: se ja existir evento para o mesmo telefone normalizado e o mesmo `start.dateTime`, DEVE retornar o evento existente em vez de criar um novo.
- **RF-005 (IDEMPOTENCIA):** Uma reentrega de webhook (mesmo conteudo de slot/telefone) apos um 502 NAO DEVE criar um segundo evento no Calendar.
- **RF-006:** A mensagem de retorno do `CreateAppointmentTool` (356-363) e do caminho deterministico NAO DEVE afirmar "consulta agendada" de forma enganosa quando o evento foi reutilizado — deve refletir o estado real (confirmacao consistente, sem duplicar).

### 4.2 Requisitos Não-Funcionais

- **RNF-001 (Atomicidade):** A checagem de idempotencia + criacao DEVE ocorrer dentro do `_APPOINTMENT_CREATION_LOCK` (`calendar_service.py:524`) para evitar corrida entre duas threads de webhook.
- **RNF-002 (Performance):** O custo adicional por criacao e no maximo 1 chamada extra a `find_appointments_by_phone` (uma listagem `events.list` com `maxResults=20`). Aceitavel para o volume da clinica.
- **RNF-003 (Observabilidade):** Reuso idempotente e bloqueio de remarcacao no LLM DEVEM ser logados (`logger.info`/`warning`) para auditoria, no padrao ja usado em `_run_loop` (314, 322) e `calendar_service` (538).
- **RNF-004 (Compatibilidade):** Sem alteracao de schema de banco nem de assinaturas publicas das tools.

### 4.3 Restrições

- Slots de 15 min; seg-sex; somente horario ofertado e disponivel; bloqueios do Calendar invioláveis (regras do PRD ja aplicadas em `create_appointment_if_available`, 499-527).
- A estrategia de remarcacao parcial e a ordem criar→cancelar sao **decididas em 000** e nao podem ser revertidas aqui.
- Nao introduzir um segundo caminho de conclusao de remarcacao: o LLM nunca conclui remarcacao.

## 5. Critérios de Aceitação

- [ ] **CA-001:** Com `state.intent == "reschedule"`, uma tentativa do LLM de chamar `criar_agendamento` em `_run_loop` e bloqueada e NAO cria evento no Calendar (verificavel por mock do `CreateAppointmentTool`).
- [ ] **CA-002:** Apos uma remarcacao bem-sucedida pelo fluxo deterministico, `find_appointments_by_phone(telefone)` retorna exatamente 1 evento (o novo).
- [ ] **CA-003:** Se `cancel_appointment` do evento antigo retornar `False`, o paciente recebe a mensagem de remarcacao parcial (`_build_partial_reschedule_message`) e a doutora recebe alerta — sem mensagem de "remarcacao confirmada".
- [ ] **CA-004:** Chamar `create_appointment_if_available` duas vezes com o mesmo `(patient_phone, start_time)` resulta em **um** unico evento; a segunda chamada retorna o `id` do evento ja existente.
- [ ] **CA-005:** Simulando 502 na entrega + reentrega do mesmo webhook, o numero de eventos no Calendar para o slot permanece 1.
- [ ] **CA-006:** A mensagem ao paciente em reuso idempotente nao afirma criacao de um novo evento adicional.
- [ ] **CA-007:** As palavras `reschedule_event_id`/`cancelar_agendamento` passam a influenciar o comportamento do motor LLM (via guarda), comprovado por teste unitario do `_run_loop`.

## 6. Plano de Testes

### 6.1 Unitários

- **TU-1:** `_run_loop` com `state.intent="reschedule"` e tool call `criar_agendamento` → resultado e `ToolMessage` de bloqueio; `CreateAppointmentTool.invoke` (mock) **nao** chamado. (RF-001, CA-001, CA-007)
- **TU-2:** `create_appointment_if_available` quando `find_appointments_by_phone` ja retorna evento para o mesmo `(telefone, slot)` → retorna o evento existente; `create_appointment` (mock) **nao** chamado. (RF-004, CA-004)
- **TU-3:** `create_appointment_if_available` sem evento existente → segue caminho normal (`_slot_conflicts` + `create_appointment`). (regressao do comportamento atual)
- **TU-4:** `_handle_offered_slot_selection` com `intent="reschedule"` e `cancel_appointment` retornando `False` → chama `_preserve_partial_reschedule_state` e nao chama `_build_confirmation_message`. (RF-003, CA-003)

### 6.2 Integração

- **TI-1:** Webhook de remarcacao deterministico ponta-a-ponta com Calendar mockado: cria novo, cancela antigo, 1 evento final. (RF-002, CA-002)
- **TI-2:** Webhook que cria evento, entrega falha (forcar `_send_response=False`) → 502 + `failed`; reentrega do mesmo `message_id` → `create_appointment_if_available` reutiliza evento; 1 evento final. (RF-005, CA-005)

### 6.3 Aceitação

- **TA-1:** Roteiro manual: paciente confirma proativamente, pede "remarcar", escolhe novo horario → agenda com 1 evento, antigo removido. (CA-002)
- **TA-2:** Roteiro manual com cancelamento do antigo forcado a falhar → paciente recebe mensagem parcial e doutora recebe alerta. (CA-003)

### 6.4 Casos de Borda

- **CB-1:** Paciente com **duas** consultas futuras no mesmo telefone ao remarcar — garantir que so o `reschedule_event_id` correto e cancelado.
- **CB-2:** Slot fica indisponivel entre a oferta e a confirmacao → `ValueError` → `slot_confirmation_unavailable`, sem criar nem cancelar nada.
- **CB-3:** Duas threads de webhook concorrentes para o mesmo slot → lock garante 1 evento (RNF-001).
- **CB-4:** Excecao do Calendar na busca de idempotencia → degrada para criacao protegida por `_slot_conflicts`, nunca pior que hoje.
- **CB-5:** `reschedule_event_id` vazio quando `intent=="reschedule"` (estado inconsistente) → ja tratado por escalonamento em `app.py:986`; garantir que o guarda do LLM ainda bloqueia.

## 7. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Idempotencia falha por divergencia de fuso/formato entre `start_time` e `start.dateTime` do Calendar | Média | Alto (double-book) | Normalizar ambos via `_normalize_datetime` (ja usado em 500/452) e comparar timestamps normalizados, nao strings cruas |
| LLM contorna o guarda chamando outra tool | Baixa | Alto | Remarcacao so conclui no deterministico; LLM nunca cancela/cria efetivamente troca; detector de loop limita repeticao |
| Reuso idempotente devolve evento de OUTRO paciente com telefone parecido | Baixa | Alto | Reusar a logica de match de `find_appointments_by_phone` (617-624) que compara digitos do telefone; exigir igualdade de slot |
| Custo extra de `events.list` por criacao | Alta | Baixo | Volume da clinica e baixo; chamada ja existe em outros fluxos |
| Regressao no caminho deterministico ao mexer na ordem | Média | Alto | Cobrir 1019-1093 com TI-1/TI-2 antes do merge; nao alterar a estrategia 000 |

## 8. Dependências

### 8.1 Internas

- **000** — define a estrategia de remarcacao parcial e a ordem criar→cancelar (reutilizada por RF-003).
- **001, 002, 003, 004, 005** — pre-requisitos (estado de conversa, idempotencia por `message_id`, guardas de escopo e correcoes de marcacao/hand-off/LID ja aplicadas que este fluxo assume estaveis).

### 8.2 Externas

- **Google Calendar API** (`googleapiclient`) — `events().insert/list/delete` (`calendar_service.py`).
- **Evolution API** — origem dos webhooks e da reentrega que torna a idempotencia necessaria.
- **OpenAI (ChatOpenAI)** — motor LLM cujo `criar_agendamento` precisa do guarda.
- **SQLite** — tabela `processed_messages` para idempotencia por `message_id`.

## 9. Observações e Decisões de Design

- **Decisao 1 — LLM nunca conclui remarcacao.** Em vez de duplicar a troca atomica dentro do `_run_loop` (opcao B), bloqueamos `criar_agendamento` quando `intent=="reschedule"` e delegamos ao deterministico, que ja tem troca + alerta parcial (1035-1065). Menos superficie de bug e um unico ponto de verdade.
- **Decisao 2 — Idempotencia na camada de integracao, nao so no handler.** Colocar a checagem em `create_appointment_if_available` (dentro do lock) protege **todos** os caminhos (deterministico, tool LLM, futuros), nao apenas a reentrega do webhook.
- **Decisao 3 — Manter ordem criar→cancelar.** Inverter (cancelar→criar) deixaria o paciente sem consulta se a criacao falhasse; a estrategia 000 ja escolheu preservar parcial + alertar, que e o comportamento honesto exigido pelo PRD ("sem sucesso silencioso").
- **Observacao:** o prompt do agente (linhas 234-235) ja orienta a identificar a consulta antiga antes de remarcar, mas orientacao textual nao e garantia; o guarda deterministico do RF-001 e a salvaguarda real.
- **N/A — Migracao de dados:** nao se aplica; nenhuma mudanca de schema.
