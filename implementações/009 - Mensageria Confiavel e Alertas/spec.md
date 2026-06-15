# Mensageria Confiável e Alertas

> **ID:** 009
> **Status:** 🟡 Planejada
> **Prioridade:** 🟠 Alta
> **Criada em:** 2026-06-15
> **Última atualização:** 2026-06-15
> **Autor:** Agente AI

---

## 1. Resumo Executivo

Esta implementação ataca diretamente a queixa nº 1 do dono ("a API toda hora dá erro") e a nº 3/4 (foge do escopo / marca errado e traz transtorno) na camada de **entrega de mensagens**. Hoje o sistema tem dois pontos cegos críticos:

1. **A doutora pode nunca ser avisada.** Quando o bot escala uma situação fora do escopo (preço, dúvida clínica, convênio referral), o alerta é enviado por `AlertService.send_alert` (`src/infrastructure/integrations/alert_service.py:18`), que chama `WhatsAppService.send_message_sync` (`src/infrastructure/integrations/whatsapp_service.py:103`). O retorno booleano dessa cadeia é **descartado** em todos os pontos de chamada (`_send_scope_alert` em `app.py:1300`, `_notify_doctor_of_processing_error` em `app.py:1488`, e o tool `alertar_doutora` em `whatsapp_tool.py:72`), e em falha o serviço apenas escreve em log. Se `DOCTOR_PHONE` estiver vazio/inválido, **todos** os alertas são descartados em silêncio (`get_doctor_phone` em `config_service.py:288` retorna `""` → `send_alert` retorna `False` sem persistir nada).

2. **O paciente pode achar que está confirmado sem estar.** Em `_handle_appointment_confirmation` (`app.py:1229`), o resultado de `_send_response` é guardado na variável `delivered` (linhas 1249, 1261, 1276) mas **nunca verificado**: o handler marca a mensagem como processada e limpa o estado da conversa (`ConversationStateService.clear`) mesmo que a entrega tenha falhado. Para o caso de cancelamento (linha 1271), o evento é cancelado no Calendar antes do envio falhar, deixando a agenda inconsistente.

Além disso, o envio WhatsApp não tem retry nem idempotência de chunk (envio em múltiplos blocos pode duplicar a primeira parte em uma reentrega), a detecção de eco por conteúdo pode classificar uma resposta manual da doutora como eco do bot (quebrando o hand-off), e `mark_patient_response` nunca é chamado (confirmações ficam "sent" para sempre).

A implementação garante: (a) toda entrega é **verificada**; (b) falha de alerta é **persistida e/ou re-tentada**, nunca silenciosa; (c) `DOCTOR_PHONE` é **validado no startup**; (d) o ciclo de confirmação é fechado.

## 2. Contexto e Motivação

### 2.1 Problema Atual

O motor de produção é `CleanAgentService` (`src/application/services/clean_agent_service.py`, instanciado como `dental_crew` em `app.py:114`), e o fluxo determinístico vive em `src/interfaces/http/app.py` (1554 linhas). A entrega física de mensagens é centralizada em `WhatsAppService` (Evolution API). Os problemas concretos, confirmados linha a linha:

- **WH-03** — `WhatsAppService.send_message` (`whatsapp_service.py:61`) e `send_message_sync` (`whatsapp_service.py:103`) capturam `httpx.HTTPError`, escrevem `logger.error(...)` e retornam `False`. `AlertService.send_alert` (`alert_service.py:54`) propaga esse `False`, mas todos os chamadores o ignoram. Resultado: a doutora não recebe o alerta e ninguém sabe.
- **CO-01** — `settings.yaml:4` define `phone: "${DOCTOR_PHONE}"`. `ConfigService._resolve_env_vars` (`config_service.py:76`) substitui pela env var; se a env não existir, retorna o literal `${DOCTOR_PHONE}`. `get_doctor_phone` (`config_service.py:288`) então faz fallback para `os.getenv("DOCTOR_PHONE", "")`, podendo retornar `""`. Com `doctor_phone` vazio, `send_alert` retorna `False` na linha 43 — **todos** os alertas/escalações são descartados em silêncio, e nada no startup avisa que o sistema está "cego".
- **WE-02 / HO-03** — `_handle_appointment_confirmation` (`app.py:1229`) calcula `delivered = await _send_response(...)` nas linhas 1249, 1261 e 1276, mas **ignora** o valor. Marca `_mark_message_processed` e roda `ConversationStateService.clear`/`cancel_appointment` mesmo sem entrega. No ramo de cancelamento (linha 1269-1271), `CalendarService().cancel_appointment(event_id)` executa **antes** do envio; se o envio falhar, o evento já foi removido e o paciente nunca soube.
- **WH-02** — `_send_response` (`app.py:816`) divide a resposta com `_split_response_messages` (`app.py:806`, separa por `\n\n`) e envia chunk a chunk; ao primeiro `delivered=False` retorna `False` (linha 824). Não há atomicidade: chunk1 entregue + chunk2 falho → handler lança 502 → o mesmo webhook é re-tentado → chunk1 é **reenviado** (duplicado para o paciente).
- **WH-05** — `send_message`/`send_message_sync` usam `httpx` com `timeout=30` fixo (linhas 85 e 120) e **sem retry**. Qualquer falha transitória (timeout, 5xx momentâneo da Evolution) vira `False` definitivo.
- **WH-04** — `_handle_outbound_message` (`app.py:405`) usa `OutboundMessageStore.consume_recent_match(phone, text, message_id)` (`outbound_message_store.py:56`). Quando não há `message_id`, o match cai para comparação por **conteúdo normalizado** (`outbound_message_store.py:79`). Uma resposta **manual** da doutora idêntica a uma frase já enviada pelo bot é tratada como eco → o hand-off (`HandoffService.activate`, `app.py:425`) **nunca ativa**.
- **WH-08** — Todo envio bem-sucedido grava em `OutboundMessageStore.record` (`whatsapp_service.py:92` e `:127`) sob o telefone do destinatário. Como alertas são enviados para o telefone **da doutora**, e respostas dela chegam por webhook `fromMe`, a resposta manual dela pode casar com o alerta gravado e ser tratada como eco (variação de WH-04 no canal da doutora).
- **WH-06** — `_format_phone` (`whatsapp_service.py:28`) só remove não-dígitos e prefixa `"55"` quando não começa com `55` (linha 42-43). Não valida tamanho, DDD nem o 9º dígito → risco de enviar para número errado (transtorno).
- **WE-12 / CO-08** — `AppointmentConfirmationService.mark_patient_response` (`appointment_confirmation_service.py:174`) existe e atualiza o status (`'confirmed'`/`'cancelled'`/etc.), mas **nunca é chamado**. Em `_handle_appointment_confirmation` o status da confirmação permanece `'sent'` eternamente, mesmo após o paciente responder.
- **WH-09** — `send_alert` monta a mensagem via `config.get_message("alerts.to_doctor", ...)` (`alert_service.py:45`). `get_message` (`config_service.py:263`) protege `KeyError` no `.format` (linha 280), mas devolve o **template cru** com placeholders `{patient_name}` etc. para a doutora se faltar uma chave — alerta ilegível.

### 2.2 Impacto do Problema

| Área | Impacto |
|------|---------|
| Confiabilidade (queixa nº 1) | Falhas transitórias da Evolution viram erro definitivo; sem retry o paciente não recebe resposta e a doutora não recebe alerta. |
| Escopo (queixa nº 3) | Quando o bot corretamente identifica algo fora do escopo e escala, o alerta pode sumir → a doutora não assume → paciente fica sem resposta. |
| Marcação errada (queixa nº 4) | Cancelamento confirmado no Calendar mas mensagem não entregue (WE-02); paciente acha que continua marcado → no-show ou conflito. |
| Hand-off | Resposta manual da doutora confundida com eco → bot continua respondendo por cima dela (WH-04/WH-08). |
| Observabilidade | Confirmações ficam "sent" para sempre (WE-12) → impossível medir taxa de confirmação. |

### 2.3 Soluções Consideradas

| Solução | Prós | Contras | Decisão |
|---------|------|---------|---------|
| Verificar `delivered` em todos os chamadores + retry com backoff em `WhatsAppService` + validação de `DOCTOR_PHONE` no startup + fila de alertas persistida | Resolve todos os findings na camada certa; reaproveita o padrão `_mark_message_failed`+502 já presente no arquivo; mínima superfície nova | Exige tocar vários pontos de `app.py` | **Adotada** |
| Trocar Evolution API por outra integração com confirmação de entrega nativa | Entrega garantida pelo provedor | Reescrita grande, fora de escopo, alto risco | Rejeitada |
| Apenas aumentar timeout/log sem retry nem verificação | Mínimo esforço | Não resolve silêncio nem duplicidade; mantém queixa nº 1 | Rejeitada |
| Idempotência total via deduplicação por hash no provedor | Robusto contra duplicatas | Evolution não oferece dedupe; teria de manter store próprio e reescrever envio | Rejeitada parcialmente (adotamos só redução de fragmentação + marca de chunk) |

## 3. Especificação Técnica

### 3.1 Visão Geral da Arquitetura

A correção concentra-se na camada de **infraestrutura de integração** (`WhatsAppService`, `AlertService`), na **persistência** (novo store de alertas pendentes / `OutboundMessageStore`), na **configuração** (`ConfigService` + validação no startup `lifespan`) e nos **handlers de interface** (`app.py`). Nenhuma regra de negócio do PRD muda; o que muda é a **garantia de entrega** e a **propagação de falhas**.

Princípio central já existente no código e que deve ser replicado: o padrão
```python
delivered = await _send_response(phone, response_text)
if not delivered:
    if message_id:
        _mark_message_failed(message_id, phone, "...")
    raise HTTPException(status_code=502, detail="...")
```
(visto em `app.py:875-879`, `:1201-1212`, `:1519-1523`) deve ser aplicado também em `_handle_appointment_confirmation`, **antes** de qualquer `clear`/`cancel`.

### 3.2 Componentes Afetados

| Componente | Tipo | Ação | Descrição |
|------------|------|------|-----------|
| `src/infrastructure/integrations/whatsapp_service.py` | Infra | Modificar | Retry com backoff em `send_message`/`send_message_sync`; timeout configurável; `_format_phone` robusto (valida DDD/tamanho/9º dígito). |
| `src/infrastructure/integrations/alert_service.py` | Infra | Modificar | Verificar retorno do envio; em falha persistir alerta pendente e logar crítico; tratar `KeyError` de template antes de enviar (WH-09). |
| `src/infrastructure/persistence/outbound_message_store.py` | Infra | Modificar | Distinguir mensagens de **alerta** (canal doutora) das respostas do bot ao paciente, para não classificar resposta manual da doutora como eco (WH-08). |
| `src/infrastructure/persistence/` (novo) | Infra | Criar | `failed_alert_store` (ou tabela `pending_alerts`) para persistir alertas não entregues e permitir reprocessamento. |
| `src/interfaces/tools/whatsapp_tool.py` | Interface | Modificar | `SendAlertToDoctorTool._run` deve refletir falha real (já retorna texto de erro, mas precisa garantir persistência via `AlertService`). |
| `src/interfaces/http/app.py` (`_handle_appointment_confirmation` 1229-1284) | Interface | Modificar | Verificar `delivered`; aplicar padrão `_mark_message_failed`+502 antes de `clear`/`cancel`; chamar `mark_patient_response`. |
| `src/interfaces/http/app.py` (`_send_response` 816-825 / `_split_response_messages` 806-813) | Interface | Modificar | Reduzir fragmentação (preferir 1 mensagem) e/ou marcar progresso de chunk para idempotência (WH-02). |
| `src/interfaces/http/app.py` (`lifespan` 71-103) | Interface | Modificar | Validar `DOCTOR_PHONE` no startup; logar CRÍTICO se inválido (CO-01). |
| `src/interfaces/http/app.py` (`_handle_outbound_message` 405-423) | Interface | Modificar | Não tratar mensagem manual da doutora como eco quando o registro casado for um alerta (WH-04/WH-08). |
| `src/infrastructure/config/config_service.py` (`get_doctor_phone` 288-293) | Infra | Modificar | Expor validação reutilizável de telefone da doutora (formato BR válido). |

### 3.3 Interfaces e Contratos

- `WhatsAppService.send_message(phone, message) -> bool` e `send_message_sync(phone, message) -> bool`: contrato mantido (retornam `True` somente em entrega confirmada **após esgotar retries**). Novo parâmetro interno de configuração de retries/timeout via env (`WHATSAPP_SEND_RETRIES`, `WHATSAPP_SEND_TIMEOUT`).
- `AlertService.send_alert(...) -> bool`: contrato mantido, mas em `False` deve **persistir** o alerta (efeito colateral novo) e logar crítico. Aplicar também a `send_referral_alert`, `notify_patient_escalation`, `notify_patient_referral`.
- `ConfigService.get_doctor_phone() -> str`: contrato mantido; adicionar `is_doctor_phone_valid() -> bool` (ou validação no startup) que valida formato BR (`55` + DDD 2 dígitos + 8/9 dígitos).
- `_send_response(phone, response_text) -> bool`: contrato mantido; semântica reforçada de atomicidade/idempotência.
- `AppointmentConfirmationService.mark_patient_response(event_id, appointment_start, status, response_text, reminder_type)`: já existe (`appointment_confirmation_service.py:174`); passa a ser **invocada** em `_handle_appointment_confirmation`.

### 3.4 Modelos de Dados

- **Nova tabela `pending_alerts`** (SQLite): `id`, `doctor_phone`, `payload` (texto do alerta já formatado), `reason`, `created_at`, `attempts`, `last_error`, `status` (`pending`/`sent`/`failed`). Permite reprocessar alertas que falharam (WH-03).
- **`outbound_messages`** (já existente, `outbound_message_store.py`): adicionar coluna/flag `kind` (`bot_reply` | `doctor_alert`) para que `consume_recent_match` ignore registros de alerta ao decidir eco no canal da doutora (WH-08). Alternativa: gravar alertas em store separado.
- **`appointment_confirmations`** (já existente, usada por `mark_patient_response`): nenhum novo campo; apenas passar a atualizar `status`/`responded_at` (WE-12).
- **`processed_messages`** (já existente, usada por `_mark_message_processed`/`_mark_message_failed`): nenhum novo campo.

### 3.5 Fluxo de Execução

**Startup (CO-01):**
1. `lifespan` (`app.py:71`) → após `ConfigService()` (linha 77), validar `get_doctor_phone()`.
2. Se inválido (vazio ou formato BR inválido): `logger.critical("DOCTOR_PHONE inválido — alertas serão descartados")`. (Decisão: logar crítico e seguir, ou abortar — ver §9.)

**Alerta (WH-03 / WH-09):**
1. Chamador (`_send_scope_alert` `app.py:1296`, `_notify_doctor_of_processing_error` `app.py:1478`, ou tool `alertar_doutora` `whatsapp_tool.py:61`) chama `AlertService.send_alert`.
2. `send_alert` monta a mensagem; se `get_message` devolver template cru (placeholders), bloquear/substituir por versão segura (WH-09).
3. Chama `WhatsAppService.send_message_sync`, que tenta enviar com **retry+backoff** (WH-05).
4. Se ainda falhar → `AlertService` grava em `pending_alerts` e loga crítico; retorna `False`.

**Confirmação de consulta (WE-02 / WE-12):**
1. `_handle_appointment_confirmation` (`app.py:1229`) decide ramo (remarcar / confirmar / cancelar).
2. **Cancelar**: enviar a resposta **primeiro**; só cancelar no Calendar se entregue. Ou: cancelar, e se o envio falhar, `_mark_message_failed`+502 para reprocessar (ver §9 sobre ordem). Aplicar padrão `if not delivered`.
3. Em qualquer ramo: chamar `mark_patient_response(event_id, ..., status=..., response_text=text)` (WE-12) **após** entrega confirmada.
4. Só então `_mark_message_processed` + `ConversationStateService.clear`.

### 3.6 Tratamento de Erros

- Falha de envio ao paciente: padrão `_mark_message_failed` + `HTTPException(502)` (já é o padrão do arquivo) — permite reprocessamento idempotente do webhook.
- Falha de envio à doutora: persistir em `pending_alerts` + `logger.critical`; **nunca** silenciar.
- `httpx.HTTPError` em `WhatsAppService`: capturado, dispara retry; após esgotar tentativas retorna `False` com log detalhado (status, corpo se disponível).
- `KeyError` de template (`get_message`): `AlertService` detecta placeholders remanescentes e usa fallback seguro (WH-09).
- `_format_phone` inválido: retorna `""` (já faz para `@lid`, `whatsapp_service.py:34`); novo: também para DDD/tamanho inválido, com `logger.error` (WH-06).

## 4. Requisitos

### 4.1 Requisitos Funcionais

- **RF-001 (WH-03):** Toda falha de entrega de alerta à doutora deve ser detectada (retorno verificado) e persistida em `pending_alerts`, com log de nível crítico. Nenhum alerta pode ser descartado em silêncio.
- **RF-002 (CO-01):** No startup, o sistema deve validar `DOCTOR_PHONE`; se ausente ou em formato BR inválido, registrar log CRÍTICO identificável.
- **RF-003 (WE-02 / HO-03):** `_handle_appointment_confirmation` deve verificar o retorno de `_send_response` em todos os três ramos (remarcar/confirmar/cancelar) e, em falha, executar `_mark_message_failed` + `HTTPException(502)` **antes** de limpar estado ou cancelar evento.
- **RF-004 (WE-12 / CO-08):** Ao processar a resposta do paciente a um lembrete de confirmação, o sistema deve chamar `AppointmentConfirmationService.mark_patient_response` com o `status` correspondente (`confirmed`/`cancelled`/`rescheduled`).
- **RF-005 (WH-05):** O envio WhatsApp (`send_message` e `send_message_sync`) deve re-tentar falhas transitórias com backoff antes de retornar `False`.
- **RF-006 (WH-02):** O envio em múltiplos chunks não pode duplicar a primeira parte numa reentrega; reduzir fragmentação (preferir mensagem única) e/ou marcar progresso de chunk.
- **RF-007 (WH-04 / WH-08):** Uma resposta **manual** da doutora não pode ser classificada como eco do bot; o match de eco no canal da doutora deve ignorar registros de alerta.
- **RF-008 (WH-06):** `_format_phone` deve validar tamanho/DDD/9º dígito e recusar (retornando `""` + log) números inconsistentes, sem prefixar `55` cegamente.
- **RF-009 (WH-09):** `send_alert` não pode entregar à doutora uma mensagem com placeholders crus; em falta de variável, usar fallback seguro.

### 4.2 Não-Funcionais

- **RNF-001:** Retries não podem bloquear o event loop async indefinidamente; backoff total ≤ ~10s por envio (configurável via env).
- **RNF-002:** A validação de `DOCTOR_PHONE` no startup não deve impedir o boot por padrão (degradação controlada) — comportamento configurável (ver §9).
- **RNF-003:** Persistência de `pending_alerts` deve usar a conexão SQLite existente (`connection.get_db`), respeitando o padrão dos stores atuais.
- **RNF-004:** Mudanças não podem alterar o contrato booleano público de `send_message`/`send_alert` (compatibilidade com chamadores e tools).

### 4.3 Restrições

- Escopo exclusivo agenda (PRD): nada nesta implementação altera regras de slots/15min/2 dias úteis/seg-sex. **N/A — justificativa:** implementação é de mensageria/entrega, não de regra de agenda.
- Manter a arquitetura limpa (domain/application/infrastructure/interfaces); novos stores em `infrastructure/persistence`.
- Não introduzir dependências externas novas além de `httpx` já usado.
- Compatível com Windows/PowerShell (ambiente do projeto) e com o runner FastAPI.

## 5. Critérios de Aceitação

- [ ] **CA-001 (RF-001):** Quando `WhatsAppService.send_message_sync` retorna `False` em um alerta, existe registro novo em `pending_alerts` e um log de nível `critical`.
- [ ] **CA-002 (RF-002):** Com `DOCTOR_PHONE` vazio/inválido, o startup emite log `critical` contendo a string identificável (ex.: "DOCTOR_PHONE"); com valor válido (`5513991198852`) não emite.
- [ ] **CA-003 (RF-003):** Em `_handle_appointment_confirmation`, se `_send_response` retorna `False`, o estado **não** é limpo, o evento **não** fica cancelado sem aviso, `_mark_message_failed` é chamado e a resposta HTTP é 502.
- [ ] **CA-004 (RF-004):** Após resposta afirmativa/negativa do paciente, `mark_patient_response` é chamado e o status em `appointment_confirmations` deixa de ser `'sent'`.
- [ ] **CA-005 (RF-005):** Uma falha transitória simulada (ex.: primeiro POST 503, segundo 200) resulta em `True` após retry, sem intervenção externa.
- [ ] **CA-006 (RF-006):** Numa reentrega do mesmo webhook após chunk1 entregue e chunk2 falho, o paciente **não** recebe chunk1 duplicado.
- [ ] **CA-007 (RF-007):** Uma mensagem `fromMe` manual da doutora idêntica a um alerta gravado ativa o hand-off (`HandoffService.activate`), não é tratada como eco.
- [ ] **CA-008 (RF-008):** `_format_phone` recusa (retorna `""`) entradas com DDD inválido/tamanho incorreto e mantém o comportamento atual para `@lid` e número BR válido.
- [ ] **CA-009 (RF-009):** Quando uma variável do template `alerts.to_doctor` está ausente, a doutora **não** recebe `{patient_name}` cru; recebe fallback legível.
- [ ] **CA-010:** Todos os testes de regressão (§6) passam e nenhum chamador existente de `send_message`/`send_alert` quebra.

## 6. Plano de Testes

### 6.1 Unitários

- `WhatsAppService._format_phone`: casos `@lid` → `""`; `"5513991198852"` → mantém; número sem `55` válido → prefixa; DDD inválido / tamanho errado → `""` (WH-06).
- `WhatsAppService.send_message_sync` com mock de `httpx.Client`: 503 depois 200 → `True` (retry, WH-05); 4 falhas → `False`.
- `AlertService.send_alert` com `send_message_sync` mockado retornando `False` → grava `pending_alerts` + log crítico (WH-03).
- `AlertService.send_alert` com template faltando variável → não envia placeholder cru (WH-09).
- `ConfigService` validação de telefone: vazio/`${...}`/formato inválido → inválido; `5513991198852` → válido (CO-01).

### 6.2 Integração

- Webhook de confirmação (`_handle_appointment_confirmation`) com `_send_response` mockado para `False`: estado preservado, evento não cancelado em silêncio, 502 retornado, `_mark_message_failed` chamado (WE-02).
- Webhook de confirmação afirmativa com envio OK: `mark_patient_response` chamado, status em `appointment_confirmations` muda de `sent` para `confirmed` (WE-12).
- `_handle_outbound_message` com mensagem manual da doutora idêntica a um alerta gravado → hand-off ativado, não eco (WH-04/WH-08).
- Startup `lifespan` com env `DOCTOR_PHONE` ausente → log crítico capturado (CO-01).

### 6.3 Aceitação

- Reproduzir o cenário do dono: paciente pede preço → bot escala → simular Evolution fora do ar → confirmar que o alerta cai em `pending_alerts` e a doutora é avisada no reprocessamento.
- Reproduzir cancelamento: paciente responde "não" ao lembrete com Evolution falhando no envio → confirmar que o evento NÃO some sem o paciente saber (CA-003).

### 6.4 Casos de Borda

- Resposta em múltiplos parágrafos (`\n\n`) onde só o 2º chunk falha (WH-02).
- Telefone com `@lid`, com `+`, com espaços/parênteses, com DDD de 1 dígito.
- `DOCTOR_PHONE` definido em `settings.yaml` como `${DOCTOR_PHONE}` mas env ausente (caminho `config_service.py:291`).
- Resposta manual da doutora **diferente** de qualquer alerta (deve ativar hand-off normalmente — não-regressão).
- Webhook duplicado (mesmo `message_id`) reprocessado após falha parcial.

## 7. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|-------|---------------|---------|-----------|
| Retry aumenta latência percebida do webhook | Média | Médio | Backoff curto e limitado (RNF-001); timeout configurável. |
| Mudar ordem cancelar/enviar gera evento órfão se enviar falhar | Média | Alto | Definir ordem canônica (§9) + 502 para reprocessar; teste CA-003. |
| Validação de `_format_phone` muito estrita rejeita número válido legado | Baixa | Alto | Cobrir casos reais (ex.: `5513991198852`) em teste; recusar só padrões claramente inválidos. |
| Bloquear boot por `DOCTOR_PHONE` derruba produção | Baixa | Alto | Default = log crítico + seguir (RNF-002), abortar só se explicitamente configurado. |
| Flag `kind` no `outbound_messages` quebra `consume_recent_match` existente | Média | Médio | Migração com default retrocompatível; testes de não-regressão de eco. |
| Persistir `pending_alerts` sem reprocessador ativo acumula registros | Média | Baixo | Documentar reprocessamento (job/admin) como follow-up; status `pending` consultável. |

## 8. Dependências

### 8.1 Internas

- **Implementação 001** (pré-requisito): base de confiabilidade/erros (padrão `_mark_message_failed`/502 e claim de `message_id`).
- **Implementação 002** (pré-requisito): listagem/observabilidade de usuários e estado, base para verificar entrega e status de confirmação.
- Reuso de: `_mark_message_processed`/`_mark_message_failed` (`app.py:1454`/`:1466`), `ConversationStateService`, `HandoffService`, `OutboundMessageStore`, `AppointmentConfirmationService` (`appointment_confirmation_service.py`).

### 8.2 Externas

- **Evolution API** (WhatsApp): endpoint `POST /message/sendText/{instance}` (`whatsapp_service.py:77`); comportamento de erros 4xx/5xx define a estratégia de retry.
- **httpx**: cliente HTTP async/sync já usado.
- **SQLite** (`connection.get_db`): persistência de `pending_alerts` e flags.

## 9. Observações e Decisões de Design

- **Ordem cancelar vs. enviar (WE-02):** decisão recomendada — para o ramo de cancelamento, manter `cancel_appointment` e, se o envio falhar, NÃO limpar estado e retornar 502 para que o webhook seja reprocessado (a mensagem é re-enviada; o cancelamento já é idempotente no Calendar). Alternativa "enviar primeiro, cancelar depois" evita evento removido sem aviso mas pode deixar evento ativo se o cancelamento falhar — preterida por inconsistência de agenda ser pior que reenvio. Documentar a escolha final no PR.
- **CO-01 — abortar ou seguir:** por padrão **seguir** com log crítico (RNF-002), pois derrubar o boot em produção amplifica a queixa nº 1. Tornar o comportamento estrito opcional via env (ex.: `STRICT_DOCTOR_PHONE=1`).
- **`.env` atual** já contém `DOCTOR_PHONE=5513991198852` (`.env:18`), mas a validação no startup permanece necessária: o sistema não pode depender de configuração manual correta; o modo de falha placeholder (`settings.yaml:4` + env ausente) precisa de defesa explícita.
- **WH-08 / OutboundMessageStore:** preferir marcar `kind='doctor_alert'` nos registros de alerta e fazer `consume_recent_match` ignorá-los no canal da doutora, em vez de criar store totalmente separado — menor superfície de mudança e reaproveita o cleanup de retenção (`RETENTION_HOURS=24`, `outbound_message_store.py:16`).
- **WH-02:** a forma mais simples e robusta de eliminar a duplicação é **reduzir a fragmentação** (enviar a resposta como mensagem única quando possível), já que `_split_response_messages` só fragmenta por `\n\n`. Idempotência de chunk fica como reforço opcional.
- **Compatibilidade de contrato:** os retornos booleanos de `send_message`/`send_alert` são consumidos por tools CrewAI (`whatsapp_tool.py:29`, `:72`); mantê-los evita efeitos colaterais no `CleanAgentService`.
