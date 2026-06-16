# Cancelamento Seguro

> **ID:** 005
> **Status:** 🟢 Concluída
> **Prioridade:** 🔴 Critica
> **Criada em:** 2026-06-15
> **Última atualização:** 2026-06-15
> **Autor:** Agente AI

---

## 1. Resumo Executivo

Esta implementacao corrige o fluxo de cancelamento de consultas do WPP-DENTAL para que ele **nunca declare um cancelamento sem confirmacao real da operacao no Google Calendar**, **nunca cancele a consulta errada** e **diferencie uma falha real (rede/auth) de um evento que ja nao existe** (idempotencia).

Hoje o fluxo determinístico em `src/interfaces/http/app.py` (`_handle_appointment_confirmation`, linhas 1229-1284) responde "Consulta cancelada com sucesso" de forma incondicional (linha 1273), sem checar o retorno de `CalendarService().cancel_appointment(event_id)` (linha 1271) e disparando o cancelamento a partir de uma simples substring `"nao"` (linha 1269). A camada de tool (`CancelAppointmentTool._run` em `src/interfaces/tools/calendar_tool.py`, linhas 388-440) infere a consulta por substring fraca do nome (linhas 406-419), o que pode atingir a consulta errada. E na camada de infraestrutura (`CalendarService.cancel_appointment` em `src/infrastructure/integrations/calendar_service.py`, linhas 531-544) qualquer excecao retorna `False`, mascarando erro de rede/auth como se fosse "evento ja inexistente".

A correcao cobre cinco findings (WE-01, CO-04/CO-02, CA-01, CA-06, CA-07) atacando as queixas do dono nº 1 (API da erro), nº 2 (responde errado) e nº 4 (marca/cancela errado e traz transtorno).

## 2. Contexto e Motivação

### 2.1 Problema Atual

Codigo real confirmado por leitura:

1. **WE-01 — sucesso silencioso no fluxo de confirmacao.** Em `src/interfaces/http/app.py:1269-1282`:
   ```python
   if any(token in normalized for token in ("nao", "cancelar", "nao vou", "desmarcar")):
       if event_id:
           CalendarService().cancel_appointment(event_id)   # 1271 - retorno ignorado
       response_text = "Consulta cancelada com sucesso. ..."  # 1273 - incondicional
       ConversationStateService.clear(phone)
       ...
       return JSONResponse({"status": "appointment_cancelled", "phone": phone})
   ```
   O retorno booleano de `cancel_appointment` (definido em `calendar_service.py:531`) e descartado. Se `event_id` estiver vazio, o cancelamento nem e tentado e mesmo assim a mensagem de sucesso e enviada. `AppointmentConfirmationService.mark_patient_response` (`appointment_confirmation_service.py:173-198`) **nunca e chamado** neste branch — a tabela `appointment_confirmations` nao registra a resposta do paciente.

2. **CO-04 / CO-02 — gatilho por substring ambigua.** O branch e acionado por `any(token in normalized for token in ("nao", ...))` (linha 1269). A substring `"nao"` casa com frases como "nao sei", "ainda nao decidi", "nao tenho certeza" — cancelando a consulta diante de resposta ambígua. Nao ha confirmacao explícita antes da acao destrutiva.

3. **CA-01 — cancelamento por substring fraca do nome.** Em `calendar_tool.py:406-419`, quando nao ha `event_id`, o codigo filtra eventos cujo `summary` contenha o `patient_name` em minúsculas (`patient_name_lower in item.get("summary", "").lower()`). Se houver exatamente 1 match por nome OU exatamente 1 evento no total, cancela; caso contrário retorna erro. Nomes curtos/comuns ("Ana", "Maria") batem em homônimos e podem cancelar a consulta errada.

4. **CA-06 — erro mascarado como inexistencia.** Em `calendar_service.py:537-544`, o `except Exception` captura **qualquer** falha (rede, auth/credenciais, quota) e retorna `False`. Os chamadores nao distinguem isso de "evento ja removido": a tool responde "Erro ao cancelar a consulta" (`calendar_tool.py:440`) e o fluxo de confirmacao ignora o `False`. Um 404/410 (evento ja inexistente) deveria ser sucesso idempotente, e um erro de rede deveria propagar/alertar.

5. **CA-07 — ramo incoerente com event_id valido que nao bate.** Em `calendar_tool.py:401-404`, quando um `event_id` e informado mas nao pertence aos eventos do telefone, retorna "Erro: nao encontrei esse ID de consulta para este telefone." A mensagem e aceitável, porem a logica nao trata o caso em que o evento existe no Calendar mas nao apareceu em `find_appointments_by_phone` (`calendar_service.py:595`, `maxResults=20`, janela `timeMin=now`), gerando incoerencia entre "ID valido" e "nao encontrado".

### 2.2 Impacto do Problema

| Queixa do dono | Como este problema a alimenta |
|---|---|
| (1) API da erro | CA-06 trata erro de rede/auth como `False` silencioso; paciente recebe "Erro ao cancelar" ou, pior, "cancelada com sucesso" sem cancelar. |
| (2) Responde errado | WE-01 afirma "cancelada com sucesso" mesmo com `event_id` vazio ou falha real. |
| (4) Marca/cancela errado e traz transtorno | CA-01 cancela a consulta de outro paciente por homonímia; CO-04 cancela diante de "nao sei". Paciente perde a vaga; doutora tem buraco na agenda. |

Risco clínico/operacional: paciente comparece e descobre que foi cancelado; ou acredita estar cancelado e nao comparece, gerando no-show e vaga ociosa.

### 2.3 Soluções Consideradas

| Solução | Prós | Contras | Decisão |
|---|---|---|---|
| Checar retorno de `cancel_appointment` e exigir confirmacao explícita; classificar 404/410 como sucesso idempotente; exigir `event_id` quando >1 consulta | Ataca os 5 findings na raiz; mínimo de mudanca de superfície; mantem fluxo determinístico | Exige mudar assinatura de `cancel_appointment` (booleano -> resultado tipado) | **ESCOLHIDA** |
| Manter booleano e so checar `if not cancelled` no chamador | Mudanca menor | Nao resolve CA-06 (rede vs inexistente continua indistinguível) | Rejeitada |
| Delegar todo cancelamento ao agente LLM (`CleanAgentService`) | Centraliza logica | LLM nao e determinístico em acao destrutiva; viola "marcar/cancelar so com confirmacao"; aumenta queixa nº 4 | Rejeitada |
| Pedir dupla confirmacao sempre (S/N + repetir) | Maxima seguranca | Atrito alto para paciente; fora do escopo desta correcao | Rejeitada (uma confirmacao explícita basta) |

## 3. Especificação Técnica

### 3.1 Visão Geral da Arquitetura

A correcao distribui a responsabilidade em tres camadas, respeitando a arquitetura limpa:

- **Infraestrutura** (`calendar_service.py`): `cancel_appointment` passa a retornar um resultado tipado que distingue `cancelled` (200 ou 404/410 idempotente) de `error` real (rede/auth). 404/410 = sucesso idempotente; demais excecoes propagam um resultado de erro.
- **Tool / agente** (`calendar_tool.py`): `CancelAppointmentTool` exige `event_id` quando ha mais de uma consulta futura, nao infere por substring fraca de nome, e responde de forma coerente para event_id valido-mas-nao-encontrado e para erro real.
- **Fluxo determinístico** (`app.py`): `_handle_appointment_confirmation` exige confirmacao explícita antes de cancelar, checa o resultado, so afirma sucesso quando `cancelled` for verdadeiro, alerta a doutora em falha real e registra a resposta via `mark_patient_response`.

### 3.2 Componentes Afetados

| Componente | Tipo | Ação | Descrição |
|---|---|---|---|
| `src/infrastructure/integrations/calendar_service.py` :: `cancel_appointment` (531-544) | Método infra | Modificar | Retornar resultado tipado `CancelResult` (ou dict) com `cancelled: bool` e `error: Optional[str]`; tratar `HttpError` 404/410 como idempotente; demais excecoes -> `error`. |
| `src/interfaces/tools/calendar_tool.py` :: `CancelAppointmentTool._run` (388-440) | Tool agente | Modificar | Exigir `event_id` quando `len(events) > 1`; remover inferencia por substring fraca de nome; tratar resultado tipado; mensagens coerentes para CA-07. |
| `src/interfaces/http/app.py` :: `_handle_appointment_confirmation` (1229-1284) | Handler determinístico | Modificar | Confirmacao explícita antes de cancelar; checar resultado; nao afirmar sucesso em `False`/`event_id` vazio; alertar doutora; chamar `mark_patient_response`. |
| `src/application/services/appointment_confirmation_service.py` :: `mark_patient_response` (173-198) | Serviço aplicacao | Reutilizar | Invocado no branch de cancelamento com `event_id`, `appointment_start` (de `METADATA_START_KEY`), `status` e `reminder_type` corretos. |
| `src/infrastructure/integrations/alert_service.py` :: `send_alert` (18-25) | Serviço infra | Reutilizar | Alertar a doutora quando o cancelamento falhar de fato (erro real). |
| `tests/` (cancelamento) | Testes | Criar | Cobertura de regressao para WE-01, CO-04, CA-01, CA-06, CA-07. |

### 3.3 Interfaces e Contratos

**Contrato novo da infraestrutura** (`calendar_service.py`):

```python
@dataclass
class CancelResult:
    cancelled: bool          # True em 200 OU 404/410 (idempotente)
    already_absent: bool      # True quando 404/410 (evento ja inexistente)
    error: Optional[str]      # mensagem tecnica quando falha real (rede/auth); None caso contrario

def cancel_appointment(self, event_id: str) -> CancelResult: ...
```

Regras do contrato:
- `event_id` vazio/`None` -> `CancelResult(cancelled=False, already_absent=False, error="event_id ausente")` (nunca chama a API).
- `delete` 2xx -> `CancelResult(cancelled=True, already_absent=False, error=None)`.
- `HttpError` com status 404 ou 410 -> `CancelResult(cancelled=True, already_absent=True, error=None)` (idempotente: ja nao existe = objetivo atingido).
- Qualquer outra excecao (rede, 401/403 auth, 5xx) -> `CancelResult(cancelled=False, already_absent=False, error=<str(exc)>)` e log em `logger.error`.

**Contrato do fluxo determinístico** (`app.py`): so envia "Consulta cancelada com sucesso" quando `result.cancelled is True`. Caso `result.error`, envia mensagem neutra ao paciente ("Vou verificar isso com a Dra. Priscila e retorno o quanto antes."), chama `_send_scope_alert`/`AlertService.send_alert` e **nao** limpa o estado nem afirma sucesso. Em todos os casos resolvidos, chama `AppointmentConfirmationService.mark_patient_response(...)`.

**Contrato da tool** (`calendar_tool.py`): mantem assinatura `_run(patient_name, patient_phone, event_id=None)`; quando `len(events) > 1` e `event_id` ausente -> retorna instrucao para informar `event_id` (sem inferir por nome).

### 3.4 Modelos de Dados

- Tabela `appointment_confirmations` (ja existente; atualizada por `mark_patient_response`, `appointment_confirmation_service.py:191-198`): colunas `status`, `response_text`, `responded_at` passam a ser populadas no cancelamento (`status="cancelled"`).
- `ConversationState` (`conversation_state_service.py:12-36`): campos `pending_event_id`, `pending_event_label` e `metadata[METADATA_START_KEY]` ja existem; usados para obter `event_id` e `appointment_start`. Nenhuma nova coluna de banco e necessária. **N/A — sem migracao de schema.**

### 3.5 Fluxo de Execução

Fluxo de confirmacao (cron) em `_handle_appointment_confirmation`:

1. Resolve `event_id` de `state.metadata[METADATA_EVENT_ID_KEY]` ou `state.pending_event_id` (app.py:1237).
2. `normalized = AppointmentOfferService._normalize(text)` (domain/policies/appointment_offer_service.py:131).
3. Se intencao = remarcar -> branch existente (1241-1255) — inalterado.
4. Se `AppointmentOfferService.is_affirmative_confirmation(text)` (appointment_offer_service.py:261) -> confirma (1257-1267) — inalterado.
5. **NOVO** — Se mensagem indica cancelamento **de forma explícita** (não só substring "nao"):
   - 5a. Se ambígua ("nao sei", "talvez") -> pedir confirmacao explícita ("Voce confirma o cancelamento da consulta de {label}? Responda SIM para cancelar."), salvar estado de "aguardando confirmacao de cancelamento" e retornar; nao cancelar.
   - 5b. Se confirmacao explícita de cancelamento:
     - Se `event_id` vazio -> nao afirmar sucesso; alertar doutora; mensagem neutra ao paciente; `mark_patient_response(status="cancel_failed")` quando houver dados.
     - `result = CalendarService().cancel_appointment(event_id)`.
     - Se `result.cancelled` -> "Consulta cancelada com sucesso..."; `ConversationStateService.clear(phone)`; `mark_patient_response(status="cancelled", ...)`.
     - Se `result.error` -> mensagem neutra; `_send_scope_alert`/`AlertService.send_alert`; **nao** limpar estado; `mark_patient_response(status="cancel_failed", ...)`.
6. Caso nenhuma regra case -> `return None` (1284) — fluxo cai para o agente.

### 3.6 Tratamento de Erros

| Cenário | Tratamento |
|---|---|
| `event_id` vazio no cancelamento | Nao chamar API; nao afirmar sucesso; alertar doutora; mensagem neutra. (corrige WE-01) |
| `delete` retorna 404/410 (evento ja removido) | `cancelled=True, already_absent=True`; tratar como sucesso idempotente; resposta normal de cancelamento. (corrige CA-06) |
| Erro de rede/auth/5xx no `delete` | `cancelled=False, error=<msg>`; log `logger.error`; alertar doutora; mensagem neutra; nao limpar estado. (corrige CA-06/WE-01) |
| Resposta ambígua ("nao sei") | Pedir confirmacao explícita; nao cancelar. (corrige CO-04) |
| >1 consulta futura sem `event_id` (tool) | Retornar instrucao para informar `event_id`; nao inferir por nome. (corrige CA-01) |
| `event_id` informado nao pertence ao telefone | Mensagem coerente ("Nao encontrei essa consulta para este telefone; confirme com consultar_agendamento."); nao cancelar. (corrige CA-07) |
| `mark_patient_response` com `event_id`/`start` vazio | Retorno antecipado ja existe (appointment_confirmation_service.py:184-189) — no-op seguro. |

## 4. Requisitos

### 4.1 Requisitos Funcionais

- **RF-001 (WE-01):** O fluxo de cancelamento em `_handle_appointment_confirmation` (app.py:1269-1282) DEVE checar o resultado de `cancel_appointment` e so afirmar "Consulta cancelada com sucesso" quando o cancelamento for confirmado (`cancelled=True`). Com `event_id` vazio ou falha real, NAO DEVE afirmar sucesso.
- **RF-002 (WE-01):** Em qualquer desfecho do cancelamento (sucesso, idempotente ou falha), o sistema DEVE registrar a resposta via `AppointmentConfirmationService.mark_patient_response` com `status` adequado (`cancelled` ou `cancel_failed`), `event_id` e `appointment_start` (de `METADATA_START_KEY`).
- **RF-003 (WE-01/CA-06):** Em falha real de cancelamento (`error` preenchido), o sistema DEVE alertar a doutora via `AlertService.send_alert` e enviar mensagem neutra ao paciente, sem limpar o estado da conversa.
- **RF-004 (CO-04/CO-02):** O cancelamento NAO DEVE ser disparado pela mera substring `"nao"`. Antes de cancelar, o sistema DEVE obter confirmacao explícita do paciente; respostas ambíguas DEVEM resultar em pedido de confirmacao, nao em cancelamento.
- **RF-005 (CA-01):** `CancelAppointmentTool._run` (calendar_tool.py:388-440) DEVE exigir `event_id` quando houver mais de uma consulta futura para o telefone, e NAO DEVE inferir a consulta por substring fraca do nome.
- **RF-006 (CA-06):** `CalendarService.cancel_appointment` (calendar_service.py:531-544) DEVE diferenciar 404/410 (sucesso idempotente) de erro real (rede/auth/5xx), retornando resultado tipado em vez de booleano que mascara tudo como `False`.
- **RF-007 (CA-07):** Quando um `event_id` valido nao corresponder a uma consulta do telefone, a tool DEVE responder de forma clara e coerente, sem cancelar nada e sem mensagem contraditória.

### 4.2 Não-Funcionais

- **RNF-001 (Determinismo):** A decisao de cancelar DEVE ser determinística no `_handle_appointment_confirmation`, sem depender do LLM para acao destrutiva.
- **RNF-002 (Idempotencia):** Reenviar o cancelamento de um evento ja removido NAO DEVE gerar erro nem mensagem contraditória (404/410 = sucesso).
- **RNF-003 (Observabilidade):** Falhas reais de cancelamento DEVEM ser logadas em `logger.error` com `event_id` (mantendo o log ja presente em calendar_service.py:538-543) e gerar alerta para a doutora.
- **RNF-004 (Compatibilidade):** A mudanca de assinatura de `cancel_appointment` (booleano -> tipado) DEVE atualizar todos os chamadores: `CancelAppointmentTool._run` (calendar_tool.py:432) e `_handle_appointment_confirmation` (app.py:1271). Nenhum chamador pode continuar tratando o retorno como booleano cru.

### 4.3 Restrições

- Manter portugues BR em todas as mensagens ao paciente.
- Evento no Calendar segue o padrao "Nome - Telefone" (nao alterar).
- Nao introduzir dependencias novas; reutilizar `AlertService` e `AppointmentConfirmationService` ja existentes.
- Convenios referral nunca sao agendados nem cancelados por este fluxo (fora de escopo deste handler).

## 5. Critérios de Aceitação

- [ ] **CA-001 (RF-001):** Com `event_id` valido e `delete` 2xx, o paciente recebe "Consulta cancelada com sucesso..." e o estado e limpo.
- [ ] **CA-002 (RF-001):** Com `event_id` vazio, o sistema NAO envia "cancelada com sucesso"; envia mensagem neutra e alerta a doutora.
- [ ] **CA-003 (RF-001/RF-003):** Com erro real (rede/auth) no `delete`, o paciente NAO recebe "cancelada com sucesso"; recebe mensagem neutra, a doutora e alertada e o estado NAO e limpo.
- [ ] **CA-004 (RF-002):** Em sucesso, `mark_patient_response` e chamado com `status="cancelled"`, `event_id` e `appointment_start` corretos; a linha em `appointment_confirmations` reflete o cancelamento.
- [ ] **CA-005 (RF-004):** Resposta "nao sei" / "ainda nao decidi" NAO cancela; gera pedido de confirmacao explícita.
- [ ] **CA-006 (RF-004):** Apenas apos confirmacao explícita ("SIM, pode cancelar") o cancelamento e executado.
- [ ] **CA-007 (RF-005):** Com 2+ consultas futuras e sem `event_id`, `CancelAppointmentTool._run` retorna instrucao para informar `event_id` e NAO cancela por substring de nome.
- [ ] **CA-008 (RF-006):** `cancel_appointment` em evento 404/410 retorna `cancelled=True, already_absent=True`; o chamador trata como sucesso idempotente.
- [ ] **CA-009 (RF-006):** `cancel_appointment` em erro de rede retorna `cancelled=False, error` preenchido e loga em `logger.error`.
- [ ] **CA-010 (RF-007):** `event_id` que nao pertence ao telefone produz resposta coerente, sem cancelar e sem mensagem contraditória.

## 6. Plano de Testes

### 6.1 Unitários

- `cancel_appointment`: mock do `service.events().delete().execute()` para 2xx -> `cancelled=True`; `HttpError(404)` e `HttpError(410)` -> `cancelled=True, already_absent=True`; `ConnectionError`/`HttpError(500)`/`HttpError(401)` -> `cancelled=False, error` preenchido + log.
- `CancelAppointmentTool._run`: 0 eventos -> "Nao encontrei nenhuma consulta..."; 1 evento sem event_id -> cancela; 2 eventos sem event_id -> instrucao para informar event_id (sem inferir por nome); event_id valido -> cancela esse; event_id inexistente para o telefone -> mensagem coerente (CA-07).
- Classificador de cancelamento: "nao sei", "talvez", "ainda nao" -> ambíguo (sem cancelar); "sim, pode cancelar", "quero desmarcar, confirmo" -> cancelamento explícito.

### 6.2 Integração

- `_handle_appointment_confirmation` com estado de confirmacao real (via `_build_confirmation_state`, appointment_confirmation_service.py:231-253): simular resposta de cancelamento e verificar (a) mensagem enviada, (b) `cancel_appointment` chamado com o `event_id` correto, (c) `mark_patient_response` chamado, (d) alerta enviado apenas em falha real, (e) estado limpo apenas em sucesso.

### 6.3 Aceitação

- Executar os 10 criterios CA-001..CA-010 como cenarios ponta a ponta com mocks de Calendar e AlertService, validando as mensagens em portugues BR.

### 6.4 Casos de Borda

- `event_id` presente mas evento ja cancelado anteriormente (idempotencia repetida): segunda chamada continua respondendo cancelamento, sem erro.
- Paciente envia "nao vou poder, pode cancelar sim" (nega + confirma): deve cancelar (confirmacao explícita presente).
- Paciente com nome homônimo a outro evento no mesmo telefone (raro) — garantir que sem `event_id` a tool nao cancela por nome.
- `metadata[METADATA_START_KEY]` ausente: `mark_patient_response` faz no-op seguro (appointment_confirmation_service.py:188-189) e o cancelamento ainda ocorre/responde corretamente.

## 7. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Mudar assinatura de `cancel_appointment` quebra chamador esquecido | Média | Alto | RNF-004: atualizar e cobrir por teste os 2 chamadores (calendar_tool.py:432, app.py:1271); buscar com Grep por todas as chamadas. |
| Classificacao de "confirmacao explícita" gerar atrito (paciente acha que ja cancelou) | Média | Médio | Mensagem de pedido de confirmacao clara e curta; aceitar variacoes afirmativas via `is_affirmative_confirmation`. |
| 404/410 do Google nem sempre exposto como `HttpError.status` esperado | Baixa | Médio | Extrair status de `getattr(exc, "resp", None)`/`status_code`; fallback conservador (tratar como erro real se status desconhecido). |
| Alerta a doutora gerar ruído em falhas transitórias | Baixa | Baixo | Alertar apenas em `error` real (nao em idempotente); reutilizar `AlertService` ja existente. |

## 8. Dependências

### 8.1 Internas

- **001** (pré-requisito) — estabilidade/robustez de API que sustenta as chamadas ao Calendar.
- **002 — Recuperação da Rede de Testes** (pré-requisito) — suíte verde para validar as mudanças de cancelamento sem regressão.
- **003 — Robustez do Estado Conversacional** (pré-requisito) — estado consistente (`event_id`/`metadata`) que o cancelamento consome.
- **004 — Identidade do Paciente e Normalização de Telefone** (pré-requisito) — telefone canônico para localizar a consulta correta a cancelar.
- Reutiliza: `AppointmentConfirmationService.mark_patient_response` (appointment_confirmation_service.py:173), `AlertService.send_alert` (alert_service.py:18), `ConversationStateService` (conversation_state_service.py:39), `AppointmentOfferService.is_affirmative_confirmation`/`_normalize` (domain/policies/appointment_offer_service.py:261/131).

### 8.2 Externas

- Google Calendar API (`googleapiclient.discovery.build`, calendar_service.py:15) — `events().delete()` e `events().list()`; tratamento de `HttpError` (status 404/410 vs demais).
- Evolution API (envio de mensagens via `_send_response`).

## 9. Observações e Decisões de Design

- **Por que resultado tipado e nao só "checar False":** o booleano atual nao consegue separar "ja removido" de "falha real". Sem essa distincao, CA-06 (idempotencia) e WE-01 (sucesso silencioso) nao podem ser resolvidos corretamente ao mesmo tempo. O `CancelResult` e o ponto central da correcao.
- **Confirmacao explícita em vez de dupla confirmacao:** suficiente para eliminar CO-04 sem adicionar atrito excessivo; a acao destrutiva passa a exigir intencao clara, alinhada à regra do PRD "na duvida escalar".
- **Idempotencia como sucesso:** alinhada à regra de remarcacao do PRD ("ao final so 1 evento ativo, sem sucesso silencioso em falha parcial"): se o evento ja nao existe, o objetivo (vaga livre) foi atingido — nao e falha.
- **mark_patient_response no cancelamento:** hoje ausente neste branch; sua inclusao fecha o ciclo de auditoria da tabela `appointment_confirmations`, util para a queixa nº 2 (rastrear respostas erradas).
- **N/A — schema de banco:** nenhuma migracao necessária; todos os campos usados (estado, metadata, colunas de `appointment_confirmations`) ja existem.
