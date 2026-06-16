# Confirmacao Proativa, Cron e Handoff

> **ID:** 010
> **Status:** 🟡 Planejada
> **Prioridade:** 🟡 Media
> **Criada em:** 2026-06-15
> **Última atualização:** 2026-06-15
> **Autor:** Agente AI

---

## 1. Resumo Executivo

Esta implementacao corrige as heuristicas e a confiabilidade do ciclo proativo de confirmacao de consultas (cron diario das 20h), do reconhecimento de confirmacao/recusa do paciente e do handoff manual para a dentista.

Hoje o ciclo proativo vive em `src/application/services/appointment_confirmation_service.py` (classe `AppointmentConfirmationService`), disparado pelo loop interno `_run_appointment_confirmation_scheduler` em `src/interfaces/http/app.py:44`. As respostas do paciente ao lembrete sao tratadas em `_handle_appointment_confirmation` (`src/interfaces/http/app.py:1229`), que delega a deteccao afirmativa para `AppointmentOfferService.is_affirmative_confirmation` (`src/domain/policies/appointment_offer_service.py:261`). O handoff e controlado por `HandoffService` (`src/application/services/handoff_service.py`) e ativado automaticamente por substring na resposta da IA em `src/interfaces/http/app.py:282-291`.

Foram identificados nove findings que produzem os 4 sintomas reclamados pelo dono: API/cron falhando (CO-04, CO-05, AG-07), respostas erradas (WE-08/CA-11, WE-13, AG-10), e marcacao/transtorno indevido (CO-06, CO-07, HO-02). Esta spec converte cada finding em RF + criterio de aceite + tarefa de correcao + tarefa de teste de regressao, sempre ancorada no codigo real lido.

## 2. Contexto e Motivação

### 2.1 Problema Atual

O ciclo proativo e o handoff dependem de heuristicas frageis (substring em tokens curtos) e de um cron sem garantia de entrega:

- **WE-08 / CA-11** — `is_affirmative_confirmation` (`appointment_offer_service.py:261-268`) usa `token in normalized` (substring) sobre `_AFFIRMATIVE_CONFIRMATION_TOKENS` (`:65-77`), que inclui `"sim"` e `"ok"`. Como e substring, `"sim"` casa dentro de `"assim"`, `"ok"` casa em qualquer palavra que contenha `ok`. Pior: se o paciente diz *"pode confirmar so que preciso trocar o dia"*, o token `"pode confirmar"` casa e a frase e tratada como confirmacao, mesmo havendo pedido explicito de troca. O guard de negacao (`:266`) so impede se `_NEGATIVE_CONFIRMATION_TOKENS` (`:78-85`, inclui `"troca"`, `"muda"`) casar antes — mas a precedencia atual ainda confunde frases mistas (afirmativo + mudanca na mesma frase).
- **WE-13** — em `app.py:282-291`, o handoff e ativado quando a resposta normalizada da IA contem marcadores como `"vou encaminhar"`. Por ser substring sem tratamento de negacao, a frase *"nao vou encaminhar"* tambem ativa o handoff, silenciando o bot indevidamente.
- **HO-02** — `HandoffService.activate` (`handoff_service.py:30-43`) grava `handoff_until_utc` fixo (`WINDOW_MINUTES = 30`). Em `app.py:181-199`, quando o handoff esta ativo, a mensagem do paciente e apenas registrada e ignorada, **sem estender a janela**. Se o atendimento humano passa de 30 min, o bot volta no meio do atendimento.
- **CO-04 (cron)** — `_run_appointment_confirmation_scheduler` (`app.py:44-67`) so executa `send_next_day_confirmations` no proximo disparo das 20h (`get_next_run_datetime`, `appointment_confirmation_service.py:58-65`). Se o processo cair/reiniciar apos as 20h, o dia perdido **nunca** e reenviado — nao ha catch-up.
- **CO-05 (cron)** — em `send_next_day_confirmations` (`appointment_confirmation_service.py:255-351`), o `_try_claim_reminder_send` (`:90-129`) insere a linha com `status='processing'`. Se ocorrer excecao **apos** o claim (ex.: falha em `PatientService.find_by_phone`, `whatsapp.send_message` levantando excecao em vez de retornar `False`, ou erro ao salvar estado entre `:323` e `:348`), a linha fica presa em `'processing'`. O retry so reabre linhas com `status='failed'` (`:118`), entao `'processing'` nunca e reenviado.
- **CO-07 (cron)** — em `app.py` (logica em `appointment_confirmation_service.py:282-303`): se o estado nao e `idle` mas foi atualizado ha mais de 2h (7200s), o codigo considera "expirado" e chama `ConversationStateService.clear(phone)` (`:303`), **apagando uma conversa real em andamento** so porque ela esta parada ha 2h. Atendimentos lentos perdem contexto.
- **CO-06 (cron)** — `_select_unique_appointments` (`appointment_confirmation_service.py:220-229`) deduplica por telefone mantendo apenas o evento de menor `start_time`. Se o mesmo paciente tem **duas consultas** no dia seguinte, a segunda e descartada e nunca recebe lembrete.
- **AG-07** — o guard de loop em `clean_agent_service.py:292-316` usa `seen_calls` com assinatura `(name, args)`. Se a mesma tool e chamada legitimamente duas vezes com os mesmos args (ex.: consultar disponibilidade duas vezes em iteracoes distintas), o segundo call e tratado como loop e a conversa e abortada com erro generico (`:315`).
- **AG-10** — `_convert_history` (`clean_agent_service.py:260-274`) so reconhece linhas com prefixo `PACIENTE:` e `ASSISTENTE:`. A formatacao de historico (`conversation_service.py:119-135`) gera tambem `DENTISTA:` (`:131`), que e **silenciosamente descartado**. Tool calls historicos tambem nao sao reconstruidos. O agente perde o contexto das intervencoes da dentista.

### 2.2 Impacto do Problema

- **Queixa 1 (API/cron da erro):** CO-04 perde dias inteiros de lembrete em qualquer restart; CO-05 deixa lembretes presos; AG-07 aborta conversas legitimas com mensagem de erro. Diretamente percebido como instabilidade.
- **Queixa 2 (responde errado):** WE-08/CA-11 confirma quando o paciente pediu troca; WE-13 silencia o bot por engano; AG-10 faz o bot ignorar o que a dentista ja disse.
- **Queixa 4 (marca errado/transtorno):** CO-06 deixa a 2a consulta do paciente sem confirmacao; CO-07 apaga conversa em andamento; confirmacao errada (WE-08) leva o paciente a comparecer num horario que pretendia trocar.
- **Queixa 3 (foge do escopo):** N/A — justificativa: nenhum finding desta implementacao introduz resposta de preco/clinico; o escopo aqui e ciclo proativo/handoff. O guard de escopo (`ScopeGuardService`) permanece intocado.

### 2.3 Soluções Consideradas

| Solução | Prós | Contras | Decisão |
|---|---|---|---|
| Reescrever `is_affirmative_confirmation` com tokenizacao por palavra + deteccao de conflito afirmativo+mudanca | Elimina falso-positivo de substring; trata frase mista pedindo esclarecimento | Requer cuidado para nao quebrar casos curtos validos ("sim", "ok") | **Adotada** |
| Manter substring, apenas adicionar mais tokens negativos | Mudanca minima | Nao resolve `"sim" em "assim"` nem frase mista; lista de excecoes infinita | Rejeitada |
| Sinal estruturado de handoff (a IA/fluxo retorna flag/tool dedicada) em vez de substring na resposta | Determinista; trata negacao por construcao | Exige alterar contrato de resposta do agente | **Adotada (sinal estruturado + fallback de substring com negacao)** |
| Catch-up de cron via verificacao de "ja rodou hoje" no startup + reabertura de `processing` antigo | Garante entrega mesmo apos restart | Precisa marcar/persistir ultima execucao bem-sucedida | **Adotada** |
| Estender janela de handoff a cada mensagem dentro da janela | Atendimento humano nao e interrompido | Janela pode nunca expirar se houver trafego continuo — mitigado com teto absoluto | **Adotada com teto maximo** |
| Substituir `seen_calls` por contador de repeticoes (abortar so apos N) | Permite repeticao legitima | Ajuste de N | **Adotada** |
| Estender `_convert_history` para reconhecer `DENTISTA:` e mapear para contexto | Recupera intervencoes da dentista | Precisa decidir papel (system/human) | **Adotada** |

## 3. Especificação Técnica

### 3.1 Visão Geral da Arquitetura

A correcao toca quatro camadas, sem alterar a arquitetura limpa:

- **domain/policies** — `appointment_offer_service.py`: tornar `is_affirmative_confirmation` baseada em palavras e adicionar deteccao de conflito (afirmativo + pedido de mudanca na mesma frase).
- **application/services** — `appointment_confirmation_service.py`: catch-up de cron, recuperacao de `processing`, dedup por (telefone+evento), guard de expiracao mais conservador. `handoff_service.py`: extensao de janela com teto. `clean_agent_service.py`: guard de loop por contagem, `_convert_history` reconhecendo `DENTISTA:`.
- **interfaces/http** — `app.py`: handoff por sinal estruturado com tratamento de negacao (`:282-291`); extensao de janela ao receber mensagem em handoff (`:181-199`); chamada de catch-up no `lifespan`.
- **infrastructure/persistence** — opcionalmente persistir marcador de "ultima execucao do cron" (catch-up) reutilizando a tabela `appointment_confirmations` (chave `event_id`+`appointment_start`) — sem nova migracao obrigatoria, ver 3.4.

### 3.2 Componentes Afetados

| Componente | Tipo | Ação | Descrição |
|---|---|---|---|
| `appointment_offer_service.py::is_affirmative_confirmation` (`:261-268`) | Funcao de dominio | Modificar | Tokenizar por palavra; detectar conflito afirmativo+mudanca → retornar falso/sinal de duvida |
| `appointment_offer_service.py::_AFFIRMATIVE/_NEGATIVE_CONFIRMATION_TOKENS` (`:65-85`) | Constantes | Modificar | Separar tokens curtos (match exato por palavra) de frases (match por substring de frase) |
| `app.py` handoff automatico (`:282-291`) | Bloco em `process_whatsapp_message` | Modificar | Trocar substring crua por deteccao com negacao; preferir sinal estruturado |
| `handoff_service.py::activate`/novo `extend` (`:30-43`) | Servico | Modificar/Adicionar | Estender `handoff_until_utc` ao receber mensagem na janela, com teto maximo |
| `app.py` handoff ativo (`:181-199`) | Bloco em `process_whatsapp_message` | Modificar | Ao ignorar por handoff, chamar `HandoffService.extend(phone)` |
| `_run_appointment_confirmation_scheduler` (`app.py:44-67`) + `lifespan` (`:70-103`) | Loop/ciclo de vida | Modificar | Catch-up no startup se o disparo das 20h foi perdido |
| `appointment_confirmation_service.py::_try_claim_reminder_send` (`:90-129`) | Metodo | Modificar | Reabrir linhas presas em `processing` alem de `failed` |
| `appointment_confirmation_service.py::send_next_day_confirmations` (`:255-351`) | Metodo | Modificar | `try/except` por consulta para marcar `failed` em excecao pos-claim; nao deixar `processing` orfao |
| `appointment_confirmation_service.py::_select_unique_appointments` (`:220-229`) | Metodo | Modificar | Deduplicar por (telefone, event_id) em vez de so telefone |
| `appointment_confirmation_service.py` guard de expiracao (`:282-303`) | Bloco | Modificar | Nao apagar estado de conversa real; so pular |
| `clean_agent_service.py::_run_loop` (`:292-316`) | Metodo | Modificar | Guard de loop por contagem (>=N) em vez de 1a repeticao |
| `clean_agent_service.py::_convert_history` (`:260-274`) | Funcao | Modificar | Reconhecer prefixo `DENTISTA:` |

### 3.3 Interfaces e Contratos

- `AppointmentOfferService.is_affirmative_confirmation(patient_message: str) -> bool` — **mantem assinatura**. Comportamento novo: retorna `False` quando ha conflito afirmativo+mudanca (paciente confirma mas pede troca). Opcionalmente adicionar metodo auxiliar `has_change_request(patient_message: str) -> bool` para o fluxo decidir entre confirmar e perguntar.
- `HandoffService.activate(phone, duration_minutes=None) -> datetime` — **mantem**. Novo metodo `HandoffService.extend(phone, duration_minutes=None) -> datetime | None`: se `is_active`, empurra `handoff_until_utc` respeitando teto `MAX_WINDOW_MINUTES`; retorna nova expiracao ou `None`.
- `AppointmentConfirmationService.send_next_day_confirmations(reference_time=None) -> dict[str,int]` — **mantem**, com nova chave de stat `recovered` (linhas `processing` reenviadas) e `skipped_busy` preservada.
- Novo (catch-up): `AppointmentConfirmationService.run_catchup_if_missed(now=None) -> dict[str,int] | None` chamado no `lifespan` antes de agendar o proximo run.
- `_convert_history(history_text) -> list[BaseMessage]` — **mantem**; passa a emitir mensagem para linhas `DENTISTA:` (papel a definir em 3.5).

### 3.4 Modelos de Dados

Tabela `appointment_confirmations` (`connection.py:54-66`) permanece. Estados de `status`: `processing` (claim feito), `sent`, `failed`, e os de resposta (`mark_patient_response`, `appointment_confirmation_service.py:173-198`).

- **CO-05:** nenhuma coluna nova. Recuperacao de `processing` reusa a constraint `UNIQUE(event_id, reminder_type, appointment_start)` (`:65`) — o retry reabre linhas com `status IN ('failed','processing')` que estejam "velhas" (ex.: `sent_at` anterior ao run atual).
- **Catch-up (CO-04):** sem migracao obrigatoria. Detectar "dia perdido" por ausencia de linha `sent`/`processing` para os eventos de amanha, ou registrar a ultima execucao bem-sucedida (chave sintetica). Decisao: reaproveitar a propria tabela (idempotencia ja garantida pelo claim).

Estado de conversa (`conversation_state`, `connection.py:40`) e seus campos (`stage`, `metadata`) ja suportam `handoff_until_utc` e os metadados de confirmacao (`METADATA_*`, `appointment_confirmation_service.py:29-31`). Sem alteracao de schema.

### 3.5 Fluxo de Execução

**Confirmacao afirmativa (corrigida):** paciente responde ao lembrete → `_handle_appointment_confirmation` (`app.py:1229`) → se contem termos de remarcacao (`:1241`) trata reschedule → senao `is_affirmative_confirmation` agora (a) tokeniza por palavra e (b) se detecta afirmacao **e** pedido de mudanca na mesma frase, retorna `False`; o fluxo entao **pergunta** ("Voce quer confirmar este horario ou prefere trocar?") em vez de confirmar.

**Handoff automatico (corrigido):** apos a IA responder (`app.py:282-291`), em vez de `marker in normalized_resp` cru, avaliar negacao: ignorar quando o marcador esta precedido por negacao (`nao ...`); idealmente o agente sinaliza handoff de forma estruturada e a substring vira fallback.

**Janela de handoff (corrigida):** mensagem chega com handoff ativo (`app.py:181-199`) → registra a mensagem → chama `HandoffService.extend(phone)` antes de retornar `handoff_active`, empurrando `handoff_until_utc` ate `MAX_WINDOW_MINUTES`.

**Cron com catch-up (corrigido):** no `lifespan` (`app.py:70-103`), antes de `asyncio.create_task(...)`, chamar `run_catchup_if_missed()`. Se ja passou das 20h de hoje e nao ha confirmacoes registradas para os eventos de amanha, executa `send_next_day_confirmations` imediatamente.

**Recuperacao de processing (corrigido):** em `send_next_day_confirmations`, cada consulta roda em `try/except`; qualquer excecao apos `_try_claim_reminder_send` chama `_mark_reminder_failed` para liberar o retry. `_try_claim_reminder_send` reabre linhas `processing` antigas.

**Historico com dentista (corrigido):** `_convert_history` reconhece `DENTISTA:`; mapeia para `SystemMessage`/`HumanMessage` rotulada (ex.: prefixo "[DENTISTA] ...") para o LLM saber que houve intervencao humana.

### 3.6 Tratamento de Erros

- **Excecao pos-claim no cron:** capturada por consulta; marca `failed`; continua o loop (nao aborta o batch). Stats incrementam `failed`.
- **Falha de envio (`delivered=False`):** ja tratada (`appointment_confirmation_service.py:325-331`) com `_mark_reminder_failed`; manter.
- **Conflito afirmativo+mudanca:** nao confirma e nao remarca silenciosamente — pergunta ao paciente (escala a decisao para o proprio paciente). Coerente com PRD "na duvida escalar".
- **Handoff por negacao:** se o texto contem o marcador mas precedido de negacao, **nao** ativa handoff.
- **Loop guard:** so aborta apos N repeticoes (default 2-3) da mesma assinatura; antes disso permite reexecucao. Mensagem de erro de loop (`clean_agent_service.py:315`) mantida como ultimo recurso.
- **Estado expirado no cron:** nunca apagar conversa nao-`idle`; apenas pular (contar em `skipped_busy`).

## 4. Requisitos

### 4.1 Requisitos Funcionais

- **RF-001 (WE-08/CA-11):** `is_affirmative_confirmation` deve usar correspondencia por palavra para tokens curtos (`sim`, `ok`, `okay`), de modo que `"assim"` nao seja confirmado e `"ok"` so case como palavra isolada.
- **RF-002 (WE-08/CA-11):** quando a mensagem contiver simultaneamente afirmacao e pedido de mudanca/troca (ex.: "pode confirmar so que preciso trocar o dia"), o sistema NAO deve confirmar; deve perguntar ao paciente se quer confirmar ou trocar.
- **RF-003 (WE-13):** o handoff automatico por conteudo da resposta da IA (`app.py:282-291`) NAO deve ativar quando o marcador estiver negado (ex.: "nao vou encaminhar"); deve preferir sinal estruturado.
- **RF-004 (HO-02):** ao receber mensagem do paciente com handoff ativo, a janela de handoff deve ser estendida (ate um teto maximo), de forma que o bot nao retome no meio do atendimento humano.
- **RF-005 (CO-04):** o cron deve executar catch-up no startup: se o disparo diario das 20h foi perdido (restart/queda) e os lembretes do dia seguinte ainda nao foram enviados, eles devem ser enviados.
- **RF-006 (CO-05):** lembretes presos em `status='processing'` por excecao parcial pos-claim devem ser detectados e reenviados em execucoes posteriores; nenhuma excecao por consulta deve abortar o batch inteiro.
- **RF-007 (CO-07):** o cron NAO deve apagar (`clear`) o estado de uma conversa real em andamento ao considera-la "expirada" (>2h); deve apenas pular o envio.
- **RF-008 (CO-06):** a deduplicacao do cron deve permitir mais de um lembrete por paciente quando houver mais de uma consulta (eventos distintos) no dia seguinte.
- **RF-009 (AG-07):** o guard de loop (`seen_calls`) deve tolerar repeticao legitima de tool call, abortando apenas apos N repeticoes da mesma assinatura.
- **RF-010 (AG-10):** a conversao de historico deve reconhecer linhas `DENTISTA:` e disponibiliza-las ao LLM como contexto (intervencao humana), em vez de descarta-las.

### 4.2 Não-Funcionais

- **RNF-001:** todas as correcoes em portugues, sem dependencias externas novas; reuso das tabelas e servicos existentes.
- **RNF-002:** idempotencia do cron preservada (claim via `INSERT OR IGNORE`, `appointment_confirmation_service.py:103-110`); catch-up nao pode duplicar lembrete ja enviado.
- **RNF-003:** o teto da janela de handoff (`MAX_WINDOW_MINUTES`) evita silenciar o bot indefinidamente.
- **RNF-004:** as heuristicas de confirmacao devem ser deterministicas (sem chamada LLM), preservando latencia atual.

### 4.3 Restrições

- Nao alterar o contrato publico de `is_affirmative_confirmation`, `HandoffService.activate`, nem `send_next_day_confirmations` (so adicionar campos/metodos).
- Nao introduzir migracao de schema obrigatoria; reusar `appointment_confirmations` e `conversation_state`.
- Bloqueios do Calendar permanecem invioláveis; nada aqui altera a logica de disponibilidade.
- Manter PRD: na duvida, escalar/perguntar; nunca sucesso silencioso em estado ambiguo.

## 5. Critérios de Aceitação

- [ ] **CA-001 (RF-001):** mensagem "assim fica bom" NAO retorna confirmacao afirmativa; "sim" e "ok" isolados retornam afirmativo.
- [ ] **CA-002 (RF-002):** "pode confirmar so que preciso trocar o dia" NAO confirma; gera resposta perguntando confirmar vs trocar.
- [ ] **CA-003 (RF-003):** resposta da IA contendo "nao vou encaminhar" NAO ativa `HandoffService.activate`; resposta com "vou encaminhar para a doutora" ativa.
- [ ] **CA-004 (RF-004):** com handoff ativo, ao chegar nova mensagem do paciente, `handoff_until_utc` e empurrado (ate o teto); o bot continua silenciado.
- [ ] **CA-005 (RF-005):** simulando restart apos as 20h sem lembretes enviados, o startup envia os lembretes do dia seguinte uma unica vez.
- [ ] **CA-006 (RF-006):** linha em `processing` deixada por excecao pos-claim e reenviada em execucao subsequente; excecao em uma consulta nao impede o envio das demais.
- [ ] **CA-007 (RF-007):** estado de conversa nao-`idle` com mais de 2h NAO e apagado pelo cron; o telefone e apenas contado em `skipped_busy`.
- [ ] **CA-008 (RF-008):** paciente com 2 consultas (eventos distintos) no dia seguinte recebe lembrete para ambas.
- [ ] **CA-009 (RF-009):** duas chamadas legitimas da mesma tool com mesmos args nao abortam a conversa; abortam apos N repeticoes.
- [ ] **CA-010 (RF-010):** historico contendo `DENTISTA: ...` produz mensagem de contexto entregue ao LLM (nao descartada).
- [ ] **CA-011:** suite de testes existente continua verde apos as mudancas.

## 6. Plano de Testes

### 6.1 Unitários

- `is_affirmative_confirmation`: tabela de casos — "sim", "ok", "okay", "confirmo", "pode confirmar" → True; "assim", "vou pensar", "pode confirmar so que preciso trocar o dia", "nao" → False.
- `HandoffService.extend`: com handoff ativo, empurra expiracao; respeita teto; retorna `None` se nao ativo.
- `_select_unique_appointments`: dois eventos do mesmo telefone (event_id distintos) → ambos retornados.
- `_try_claim_reminder_send`: linha `processing` antiga → reaberta; linha `sent` → nao reaberta.
- `_convert_history`: linha `DENTISTA:` produz mensagem; `PACIENTE:`/`ASSISTENTE:` preservados.
- `_run_loop`: repeticao da mesma assinatura abaixo de N nao aborta; >= N aborta.

### 6.2 Integração

- `send_next_day_confirmations` com mock de Calendar/WhatsApp: injetar excecao apos o claim → status final `failed` e proximo run reenvia; verificar stats `failed`/`recovered`.
- Catch-up: simular `reference_time` apos 20h sem registros → `run_catchup_if_missed` envia; rodar de novo → nenhum envio duplicado (idempotencia).
- Handoff em `process_whatsapp_message`: mensagem com handoff ativo estende janela (`app.py:181-199`).

### 6.3 Aceitação

- Reproduzir cada CA-001..CA-011 ponta a ponta no fluxo de webhook (`process_whatsapp_message`) e no cron, validando resposta enviada e estado final.

### 6.4 Casos de Borda

- "ok" dentro de "okdoutora" (sem espaco) → nao confirma.
- Frase so com negacao "nao." → tratada como recusa (coerente com `app.py:1269`).
- Handoff com trafego continuo por horas → expira no teto, nao infinito.
- Cron rodando exatamente as 20h x reinicio as 20h05 → exatamente um envio por evento.
- Paciente com 2 consultas, uma ja `sent` e outra `failed` → so a `failed` e reenviada.
- Historico com `DENTISTA:` vazio (linha sem conteudo) → ignorado sem erro.

## 7. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Tokenizacao por palavra rejeitar confirmacoes validas curtas | Media | Medio | Suite ampla de casos positivos; manter tokens de frase ("pode confirmar") por substring de frase |
| Catch-up duplicar lembrete | Baixa | Alto | Idempotencia via `INSERT OR IGNORE` (`:103`) + UNIQUE constraint (`:65`) |
| Extensao de janela silenciar o bot indefinidamente | Media | Medio | Teto `MAX_WINDOW_MINUTES` |
| Reabrir `processing` reenviar lembrete que ja foi entregue mas nao marcado `sent` | Baixa | Medio | So reabrir `processing` "velho" (anterior ao run atual); preferir corrigir o `try/except` para nunca deixar orfao |
| Mapear `DENTISTA:` como papel errado confundir o LLM | Baixa | Baixo | Rotular explicitamente como intervencao humana; revisar prompt |
| Sinal estruturado de handoff exigir mudanca no agente | Media | Medio | Entregar fallback de substring-com-negacao primeiro; sinal estruturado como incremento |

## 8. Dependências

### 8.1 Internas

- **Implementacao 002 — Recuperação da Rede de Testes** — pre-requisito; suíte verde para validar o ciclo proativo/cron/handoff sem regressão.
- **Implementacao 003 — Robustez do Estado Conversacional** — pre-requisito; estado consistente para a confirmação proativa não cair em fluxo órfão.
- **Implementacao 004 — Identidade do Paciente e Normalização de Telefone** — pre-requisito; telefone canônico afeta `build_conversation_phone` e o roteamento do webhook.
- **Implementacao 005 — Cancelamento Seguro** — pre-requisito; o branch de cancelamento da confirmação proativa reutiliza o cancelamento seguro.
- Modulos: `appointment_confirmation_service.py`, `handoff_service.py`, `appointment_offer_service.py`, `clean_agent_service.py`, `conversation_service.py`, `conversation_state_service.py`, `app.py`.

### 8.2 Externas

- Google Calendar (`CalendarService.find_patient_appointments_for_date`) — fonte dos eventos do dia seguinte.
- Evolution API / WhatsApp (`WhatsAppService.send_message`) — entrega dos lembretes.
- SQLite (tabela `appointment_confirmations`, `conversation_state`).
- `asyncio` (loop do scheduler interno).

## 9. Observações e Decisões de Design

- A confirmacao afirmativa permanece deterministica (sem LLM) para latencia e previsibilidade; o conflito afirmativo+mudanca resolve-se perguntando ao paciente, alinhado ao PRD "na duvida escalar".
- O handoff por sinal estruturado e o caminho ideal; como mitigacao incremental, a primeira entrega trata negacao na deteccao por substring (`app.py:282-291`).
- Catch-up reusa a idempotencia ja existente (claim + UNIQUE), evitando nova tabela de "ultima execucao".
- A causa raiz de CO-05 e a ausencia de `try/except` por consulta apos o claim; a reabertura de `processing` e uma rede de seguranca, nao a correcao principal.
- O guard de loop por contagem deve registrar o numero de repeticoes em log para diagnostico; o N default sugerido e 2 (aborta na 3a ocorrencia identica).
