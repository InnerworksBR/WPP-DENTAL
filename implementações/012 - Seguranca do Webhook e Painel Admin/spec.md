# Segurança do Webhook e Painel Admin

> **ID:** 012
> **Status:** 🟢 Concluída
> **Prioridade:** 🟠 Alta
> **Criada em:** 2026-06-15
> **Última atualização:** 2026-06-15
> **Autor:** Agente AI

---

## 1. Resumo Executivo

Hoje o webhook `/webhook/message` e o painel administrativo `/admin` do WPP-DENTAL estão efetivamente abertos. O webhook é chamado com `allow_unauthorized=True` (`src/interfaces/http/app.py:142`): quando há chave configurada mas a requisição não traz chave válida, o código apenas registra um aviso e **segue processando** (`_authenticate_request`, `src/interfaces/http/app.py:1398-1405`). Qualquer pessoa pode disparar mensagens, criar/cancelar consultas e gerar custo de OpenAI. Em paralelo, o painel `/admin` fica completamente aberto quando nenhuma chave está configurada (`_require_admin` retorna sem checar nada — `src/interfaces/http/admin.py:62-69`), e o `.env.example:9` distribui o placeholder público `your-admin-panel-key`, que tende a ir para produção.

Além disso há vazamento de dados sensíveis: a chave de API é aceita vinda do **corpo** do payload (`_extract_request_api_key`, `src/interfaces/http/app.py:480-484`) e o payload inteiro é logado em `logger.debug("Webhook recebido: %s", payload)` (`src/interfaces/http/app.py:144`), enquanto o conteúdo da mensagem do paciente (PII) é logado em `logger.info("Mensagem de %s (%s): %s...", ...)` (`src/interfaces/http/app.py:179`). No painel, o `DELETE /api/blocks/{event_id}` chama `CalendarService.delete_day_block` (`src/infrastructure/integrations/calendar_service.py:363-369`) que apaga **qualquer** evento do Google Calendar pelo ID — inclusive consultas reais de pacientes. Vários endpoints do painel não têm `try/except` e podem retornar 500, e os campos `error` dos endpoints de agenda/bloqueio expõem mensagens internas/credenciais.

Esta implementação fecha o webhook (rejeita 401 quando há chave e não bate; só tolera quando NENHUMA chave existe, logando crítico), restringe a origem da chave a header/query, redige PII dos logs, exige chave forte no painel (nunca abrir por padrão em produção), impede a remoção de eventos que não sejam bloqueios, blinda os endpoints do painel com tratamento de erro e remove o vazamento de mensagens internas no campo `error`. Cobre os findings WE-03, WE-09, CO-08, AD-01, AD-02, AD-03, AD-04 e AD-06.

## 2. Contexto e Motivação

### 2.1 Problema Atual

- **WE-03 (alto, security):** `receive_message` chama `_authenticate_request(..., require_key=False, include_evolution_fallback=True, allow_unauthorized=True)` (`src/interfaces/http/app.py:137-143`). Dentro de `_authenticate_request`, quando há chaves aceitas configuradas mas a chave fornecida não bate, o bloco `if allow_unauthorized:` (`src/interfaces/http/app.py:1398-1405`) loga um aviso e faz `return`, aceitando a requisição. Resultado: qualquer requisição anônima dispara o pipeline (processa mensagem, escreve no banco, chama OpenAI, manipula Google Calendar).
- **WE-09 (médio, security):** `_extract_request_api_key` (`src/interfaces/http/app.py:464-486`) aceita a chave vinda do **corpo** do payload (`payload.get("apikey"/"token"/"key")`, linhas 480-484), além de header e query string. Como o payload inteiro é logado em debug (`src/interfaces/http/app.py:144`), a chave acaba persistida em log.
- **CO-08 (médio, config):** `logger.debug("Webhook recebido: %s", payload)` (`src/interfaces/http/app.py:144`) registra o payload completo (telefone, nome de contato, texto da mensagem). `logger.info("Mensagem de %s (%s): %s...", phone, contact_name, text[:50])` (`src/interfaces/http/app.py:179`) registra telefone, nome e início do texto do paciente em nível INFO (sempre ativo). Isso é PII em log.
- **AD-01 (CRÍTICO, security):** `_require_admin` (`src/interfaces/http/admin.py:62-69`) retorna imediatamente quando `_configured_admin_keys()` está vazio (`if not keys: return`), deixando todo o `/admin` aberto. As chaves vêm de `ADMIN_API_KEY`/`WEBHOOK_API_KEY`/`EVOLUTION_WEBHOOK_API_KEY` (`src/interfaces/http/admin.py:39-46`); em produção a chave costuma ser o placeholder público `your-admin-panel-key` (`.env.example:9`). Não há detecção de ambiente: o código nunca distingue desenvolvimento de produção.
- **AD-02 (alto, wrong_booking):** `DELETE /api/blocks/{event_id}` (`delete_block`, `src/interfaces/http/admin.py:339-349`) chama `calendar.delete_day_block(event_id)` (`src/infrastructure/integrations/calendar_service.py:363-369`), que apaga o evento sem verificar se ele é realmente um bloqueio. `CalendarService.event_is_day_block` (`src/infrastructure/integrations/calendar_service.py:308-318`) existe e identifica bloqueios pela propriedade privada `wpp_dental_type == DAY_BLOCK_MARKER` ou pelo prefixo `[WPP-DENTAL] Bloqueio`, mas não é consultada antes da exclusão. Logo, um ID de uma consulta real apaga a consulta.
- **AD-03 (alto, api_error):** `get_summary` (`src/interfaces/http/admin.py:103-138`), `list_patients` (`141-177`), `list_conversations` (`180-219`) e `list_errors` (`252-274`) executam consultas SQLite sem `try/except`. Qualquer erro de banco vira HTTP 500 não tratado, derrubando a tela do painel.
- **AD-04 (médio):** `create_block` (`src/interfaces/http/admin.py:326-336`) chama `_parse_date(payload.date)` (`76-80`), que valida apenas o formato `YYYY-MM-DD`, e cria o bloqueio mesmo para datas no passado. Bloquear o passado é inútil e polui a agenda.
- **AD-06 (médio, security):** `_calendar_error_payload(exc)` (`src/interfaces/http/admin.py:83-88`) retorna `str(exc)` no campo `error`. Usado em `list_appointments` (`286-287`), `list_blocks` (`319-322`), `create_block` (`334-335`) e `delete_block` (`345-346`), pode expor mensagens internas/credenciais do Google ao cliente HTTP, e o front renderiza esse texto (`loadAppointments`/`loadBlocks` em `src/interfaces/http/admin.py:682,693`).

### 2.2 Impacto do Problema

Mapeia diretamente para as quatro queixas do dono. Webhook aberto e endpoints sem tratamento geram **erros e instabilidade** (queixa 1) e permitem que terceiros provoquem **marcações erradas e transtorno** (queixa 4), criando/cancelando consultas e até apagando consultas reais via `DELETE /api/blocks`. O custo de OpenAI fica exposto a abuso anônimo. O vazamento de PII em logs e de mensagens internas/credenciais no campo `error` é risco de privacidade e de segurança (LGPD). O placeholder público em produção é uma porta destrancada para o painel inteiro, com acesso a histórico de conversas, dados de pacientes e à agenda.

### 2.3 Soluções Consideradas

| Solução | Prós | Contras | Decisão |
|---|---|---|---|
| Rejeitar 401 sempre que houver chave configurada e não bater; tolerar (logando crítico) só quando NENHUMA chave existir | Fecha WE-03 mantendo compatibilidade com instalações ainda sem chave; mudança localizada em `_authenticate_request` | Quebra o teste atual `test_message_webhook_accepts_request_without_valid_auth_header` (precisa ser ajustado) | **Adotada** |
| Exigir chave sempre (`require_key=True`, sem tolerância) | Máxima segurança | Bloqueia ambientes legados sem chave; muda contrato sem fallback; risco de derrubar produção na virada | Rejeitada |
| Restringir a chave a header/query e parar de logar payload completo (redação) | Fecha WE-09 e CO-08 com baixo custo; mantém compatibilidade com Evolution (envia via header `apikey`) | Exige helper de redação reutilizável | **Adotada** |
| Exigir chave forte no painel e detectar ambiente (`ENVIRONMENT`); recusar placeholder/ausência em produção | Fecha AD-01 sem travar dev local; rejeita `your-admin-panel-key` | Introduz nova variável de ambiente e regra de força mínima de chave | **Adotada** |
| Verificar `event_is_day_block` antes de deletar | Fecha AD-02 reutilizando método já existente; impede apagar consulta real | Uma leitura extra no Calendar por exclusão | **Adotada** |
| Envolver endpoints do painel em `try/except` com erro genérico; recusar data no passado em `create_block` | Fecha AD-03/AD-04/AD-06 de forma uniforme; não vaza detalhe interno | Mensagens de erro menos detalhadas para o operador (mitigado por log server-side) | **Adotada** |

## 3. Especificação Técnica

### 3.1 Visão Geral da Arquitetura

As mudanças ficam em três pontos da camada `interfaces/infrastructure`, sem tocar o domínio:

1. `src/interfaces/http/app.py` — autenticação do webhook (`_authenticate_request`, `_extract_request_api_key`), redação de logs no `receive_message`.
2. `src/interfaces/http/admin.py` — autenticação do painel (`_require_admin`, novo helper de ambiente/força de chave), guarda de exclusão de bloqueios, `try/except` nos endpoints, validação de data passada, saneamento do campo `error`.
3. `src/infrastructure/integrations/calendar_service.py` — `delete_day_block` passa a confirmar que o evento é um bloqueio antes de apagar (defesa em profundidade junto com a checagem no endpoint).

Introduz-se um helper de redação de PII e um helper de detecção de produção (`ENVIRONMENT`), reutilizáveis.

### 3.2 Componentes Afetados

| Componente | Tipo | Ação | Descrição |
|---|---|---|---|
| `_authenticate_request` (`src/interfaces/http/app.py:1354-1406`) | Função | Modificar | No mismatch, rejeitar 401 sempre que houver chaves configuradas; só tolerar quando NENHUMA chave existir, logando em nível crítico. Remover/ignorar o caminho `allow_unauthorized` para mismatch. |
| `receive_message` (`src/interfaces/http/app.py:130-179`) | Endpoint | Modificar | Trocar `allow_unauthorized=True` para refletir nova política; substituir `logger.debug` do payload completo (linha 144) por log redigido; redigir telefone/texto em `logger.info` (linha 179). |
| `_extract_request_api_key` (`src/interfaces/http/app.py:464-486`) | Função | Modificar | Remover a extração da chave a partir do corpo do payload (linhas 480-484). Chave só via header (`apikey`/`x-api-key`/`x-webhook-key`/`Authorization: Bearer`) e query string. |
| `_redact_payload` / `_redact_phone` | Função | Criar | Helpers de redação de PII (telefone, nome, texto, chaves) para uso nos logs. |
| `_require_admin` (`src/interfaces/http/admin.py:62-69`) | Função | Modificar | Em produção (`ENVIRONMENT=production`), recusar acesso quando não houver chave forte; rejeitar placeholder `your-admin-panel-key`. Em dev, manter comportamento aberto somente fora de produção. |
| `_is_production` / `_is_strong_key` | Função | Criar | Helpers em `admin.py` para detectar ambiente e validar força mínima da chave. |
| `delete_block` (`src/interfaces/http/admin.py:339-349`) | Endpoint | Modificar | Antes de deletar, carregar o evento e exigir `CalendarService.event_is_day_block(event)`; retornar 404/403 quando o ID não for um bloqueio. |
| `delete_day_block` (`src/infrastructure/integrations/calendar_service.py:363-369`) | Método | Modificar | Defesa em profundidade: buscar o evento e só apagar se `event_is_day_block` for verdadeiro; caso contrário não apagar e retornar `False`. |
| `get_summary`, `list_patients`, `list_conversations`, `list_errors` (`src/interfaces/http/admin.py:103-274`) | Endpoints | Modificar | Envolver em `try/except`; retornar payload de erro genérico em vez de propagar 500. |
| `create_block` (`src/interfaces/http/admin.py:326-336`) + `_parse_date` (`76-80`) | Endpoint/Função | Modificar | Recusar data anterior a hoje (fuso `SAO_PAULO_TZ`). |
| `_calendar_error_payload` (`src/interfaces/http/admin.py:83-88`) | Função | Modificar | Não retornar `str(exc)` ao cliente; mensagem genérica fixa + log server-side com o detalhe real. |
| `.env.example:9` | Config | Modificar | Substituir `your-admin-panel-key` por marcador explícito de obrigatoriedade; documentar `ENVIRONMENT`. |

### 3.3 Interfaces e Contratos

- **Webhook `POST /webhook/message`**
  - Com `WEBHOOK_API_KEY`/`EVOLUTION_WEBHOOK_API_KEY` (ou fallback `EVOLUTION_API_KEY`) configurada **e** chave válida em header/query → `200` (processa).
  - Com chave configurada **e** chave ausente/inválida → `401 {"detail": "Unauthorized webhook request"}` (não processa).
  - Sem nenhuma chave configurada → `200` (tolerado por compatibilidade) **+** log de nível crítico avisando que o webhook está exposto.
  - Chave fornecida **apenas no corpo** do payload → tratada como ausente (não autentica).
- **Painel `/admin/api/*`**
  - `ENVIRONMENT=production` sem chave forte (ausente, vazia ou igual ao placeholder `your-admin-panel-key`) → `503 {"detail": "Admin panel authentication not configured"}`.
  - Chave configurada e válida → `200`. Chave configurada e inválida → `401`.
  - Fora de produção sem chave → aberto (mantém DX local).
- **`DELETE /admin/api/blocks/{event_id}`**
  - `event_id` corresponde a um bloqueio → `{"ok": true}`.
  - `event_id` corresponde a evento que **não** é bloqueio → `{"ok": false, "error": "Evento nao e um bloqueio.", "items": []}` (consulta real preservada).
  - `event_id` inexistente → `{"ok": false, "error": "Bloqueio nao encontrado.", "items": []}`.
- **`POST /admin/api/blocks`** com `date` no passado → `422 {"detail": "Nao e possivel bloquear uma data no passado."}`.
- **Campo `error` dos endpoints de agenda/bloqueio:** sempre mensagem genérica (ex.: `"Falha ao consultar a agenda."`); o detalhe real vai apenas para o log.

### 3.4 Modelos de Dados

N/A — justificativa: nenhuma tabela do SQLite (`patients`, `conversation_history`, `processed_messages`, `appointment_confirmations`, etc.) nem o schema do Google Calendar mudam. A identificação de bloqueio reutiliza `extendedProperties.private.wpp_dental_type == DAY_BLOCK_MARKER` / prefixo `[WPP-DENTAL] Bloqueio` já existentes (`src/infrastructure/integrations/calendar_service.py:308-318`).

### 3.5 Fluxo de Execução

Webhook:
1. `receive_message` lê o JSON (`src/interfaces/http/app.py:132-135`).
2. `_authenticate_request` calcula `dedicated_keys + fallback_keys` (`_get_configured_api_keys`, `src/interfaces/http/app.py:444-461`).
3. Se há chaves: extrai a chave **apenas** de header/query (`_extract_request_api_key` ajustado) e compara com `hmac.compare_digest`. Match → segue; mismatch → `HTTPException(401)`.
4. Se NÃO há chaves: `logger.critical` (webhook exposto) e segue.
5. Log do recebimento usa payload redigido; o log de mensagem (linha 179) usa telefone/texto redigidos.

Exclusão de bloqueio:
1. `delete_block` recebe `event_id` (`src/interfaces/http/admin.py:339-349`).
2. Busca o evento no Calendar; se `event_is_day_block` falso → retorna erro sem apagar.
3. Se verdadeiro → `delete_day_block`, que reconfirma o tipo antes de chamar `events().delete` (defesa em profundidade).

Criação de bloqueio:
1. `create_block` → `_parse_date` valida formato e agora também rejeita data anterior a hoje (`SAO_PAULO_TZ`).

### 3.6 Tratamento de Erros

- **Webhook não autenticado com chave configurada:** `HTTPException(status_code=401, detail="Unauthorized webhook request")`.
- **Webhook sem nenhuma chave:** processa, mas emite `logger.critical(...)` (ao menos uma vez por processo, controlado por flag global como os `_webhook_auth_*_warning_logged` já existentes).
- **Painel em produção sem chave forte:** `HTTPException(status_code=503, detail="Admin panel authentication not configured")`.
- **Erros de banco nos endpoints do painel:** capturados em `try/except`; logados server-side (com redação) e respondidos com payload genérico (`{"ok": false, "error": "<mensagem generica>", "items": []}` ou `503` conforme o endpoint), nunca propagando `str(exc)` ao cliente.
- **Erros do Google Calendar:** `_calendar_error_payload` retorna mensagem genérica; detalhe real só no log.
- **Data no passado:** `HTTPException(422, "Nao e possivel bloquear uma data no passado.")`.

## 4. Requisitos

### 4.1 Requisitos Funcionais

- **RF-001 (WE-03):** Quando houver chave de webhook configurada e a requisição não trouxer chave válida, o sistema DEVE responder `401` e NÃO processar a mensagem.
- **RF-002 (WE-03):** Quando NENHUMA chave de webhook estiver configurada, o sistema DEVE processar a requisição (compatibilidade) e registrar um log de nível crítico indicando que o webhook está exposto.
- **RF-003 (WE-09):** `_extract_request_api_key` DEVE aceitar a chave apenas via header (`apikey`, `x-api-key`, `x-webhook-key`, `Authorization: Bearer`) e query string; chave vinda do corpo do payload DEVE ser ignorada.
- **RF-004 (CO-08):** Os logs do webhook (`src/interfaces/http/app.py:144` e `:179`) NÃO DEVEM registrar payload completo nem PII (telefone completo, nome, texto da mensagem, chaves) em texto claro; DEVEM usar redação.
- **RF-005 (AD-01):** Em `ENVIRONMENT=production`, o painel `/admin` DEVE exigir chave forte; ausência, vazio ou o placeholder `your-admin-panel-key` DEVEM resultar em `503` e em painel inacessível.
- **RF-006 (AD-01):** Fora de produção, o painel PODE permanecer aberto quando não houver chave, preservando a experiência de desenvolvimento local.
- **RF-007 (AD-02):** `DELETE /api/blocks/{event_id}` DEVE deletar somente eventos que satisfaçam `CalendarService.event_is_day_block`; para qualquer outro evento (ex.: consulta real) DEVE recusar a exclusão sem apagar nada.
- **RF-008 (AD-02):** `CalendarService.delete_day_block` DEVE confirmar que o evento é um bloqueio antes de chamar `events().delete` (defesa em profundidade).
- **RF-009 (AD-03):** `get_summary`, `list_patients`, `list_conversations` e `list_errors` DEVEM tratar exceções e responder de forma controlada em vez de propagar HTTP 500.
- **RF-010 (AD-04):** `create_block` DEVE recusar datas anteriores a hoje (fuso `SAO_PAULO_TZ`).
- **RF-011 (AD-06):** Os campos `error` de `list_appointments`, `list_blocks`, `create_block` e `delete_block` DEVEM conter apenas mensagem genérica; detalhes internos/credenciais DEVEM ir somente para o log server-side.

### 4.2 Não-Funcionais

- **RNF-001 (segurança):** Comparação de chaves DEVE continuar usando `hmac.compare_digest` (já em uso em `src/interfaces/http/app.py:1395` e `src/interfaces/http/admin.py:68`) para evitar ataque de tempo.
- **RNF-002 (compatibilidade):** A Evolution API envia a chave no header `apikey`; a nova política NÃO PODE quebrar instalações que já enviam chave por header.
- **RNF-003 (observabilidade):** Toda rejeição (401/503) e toda tolerância por ausência de chave DEVE ser logada (com redação) para auditoria.
- **RNF-004 (privacidade/LGPD):** Nenhum log em qualquer nível (DEBUG/INFO/WARNING/ERROR/CRITICAL) DEVE conter PII em texto claro ou chaves de API.
- **RNF-005 (desempenho):** A leitura extra do Calendar antes de deletar bloqueio é aceitável (1 chamada adicional por exclusão, operação rara).

### 4.3 Restrições

- Não alterar o domínio (`src/domain`) nem o motor `CleanAgentService` (`src/application/services/clean_agent_service.py`).
- Manter o contrato dos demais webhooks (`/webhook/reload-config` já exige chave — ver `test_main_webhook.py:84-90`).
- Reutilizar `event_is_day_block`, `_get_configured_api_keys`, `_clean_key` e padrões já existentes; não introduzir nova dependência externa.
- Português BR em mensagens e logs.

## 5. Critérios de Aceitação

- [ ] **CA-001 (RF-001):** Com `WEBHOOK_API_KEY` setada, `POST /webhook/message` sem header de chave (ou com chave errada) retorna `401` e não chama `process_message`.
- [ ] **CA-002 (RF-002):** Sem nenhuma chave configurada, `POST /webhook/message` retorna `200` e um log de nível crítico é emitido alertando exposição.
- [ ] **CA-003 (RF-001/RNF-002):** Com `WEBHOOK_API_KEY` setada, `POST /webhook/message` com header `apikey` válido retorna `200` e processa.
- [ ] **CA-004 (RF-003):** Com `WEBHOOK_API_KEY` setada, chave correta enviada **apenas no corpo** do payload resulta em `401` (corpo é ignorado).
- [ ] **CA-005 (RF-004/RNF-004):** Os logs gerados ao processar um webhook não contêm o telefone completo, nome, texto da mensagem nem chave em texto claro (verificado por captura de logs).
- [ ] **CA-006 (RF-005):** Com `ENVIRONMENT=production` e sem chave (ou com `your-admin-panel-key`), `GET /admin/api/summary` retorna `503`.
- [ ] **CA-007 (RF-005):** Com `ENVIRONMENT=production` e `ADMIN_API_KEY` forte, `GET /admin/api/summary` com a chave correta retorna `200`; com chave errada retorna `401`.
- [ ] **CA-008 (RF-006):** Sem `ENVIRONMENT=production` e sem chave, `GET /admin/api/summary` retorna `200` (comportamento de dev preservado).
- [ ] **CA-009 (RF-007):** `DELETE /api/blocks/{id}` em que `{id}` é uma consulta real (evento sem marcador de bloqueio) NÃO apaga o evento e retorna `{"ok": false}`.
- [ ] **CA-010 (RF-007/RF-008):** `DELETE /api/blocks/{id}` em que `{id}` é um bloqueio legítimo retorna `{"ok": true}` e o evento é removido.
- [ ] **CA-011 (RF-009):** Forçando erro de banco em `get_summary`/`list_patients`/`list_conversations`/`list_errors`, a resposta é controlada (não 500 com stack trace).
- [ ] **CA-012 (RF-010):** `POST /api/blocks` com `date` anterior a hoje retorna `422`.
- [ ] **CA-013 (RF-011):** Forçando exceção no Calendar, o campo `error` da resposta de `list_appointments`/`list_blocks` é genérico e não contém `str(exc)` original (verificado por log separado contendo o detalhe).

## 6. Plano de Testes

### 6.1 Unitários

- `_extract_request_api_key`: chave em header retorna a chave; chave só no corpo retorna `""` (RF-003).
- Helper de redação: dado um payload com telefone/nome/texto/chave, a saída não contém os valores originais (RF-004).
- `_is_production` / `_is_strong_key` (admin): placeholder `your-admin-panel-key`, vazio e ausente reprovam; chave forte aprova (RF-005).
- `CalendarService.delete_day_block`: com evento marcado como bloqueio chama `events().delete` e retorna `True`; com evento comum NÃO chama `delete` e retorna `False` (RF-008) — usar mock do serviço Google.
- `_parse_date`/validação de passado: data de ontem reprova, data de hoje/futuro aprova (RF-010).

### 6.2 Integração

- Webhook com `WEBHOOK_API_KEY` setada: sem chave → 401; header válido → 200; corpo-only → 401 (RF-001, RF-003) em `tests/test_main_webhook.py`.
- Webhook sem nenhuma chave: 200 + asserção de log crítico via `caplog` (RF-002).
- Painel em produção: matriz (sem chave / placeholder / chave forte) × (sem header / header certo / header errado) em `tests/test_admin.py` (RF-005/RF-006).
- `DELETE /api/blocks/{id}` com evento de consulta vs. bloqueio, mockando `CalendarService` (RF-007).
- Endpoints `summary/patients/conversations/errors` com banco indisponível/erro injetado → resposta controlada (RF-009).
- `POST /api/blocks` com data passada → 422 (RF-010).
- `list_appointments`/`list_blocks` com Calendar lançando exceção → `error` genérico, log com detalhe (RF-011).

### 6.3 Aceitação

- Executar a matriz dos critérios CA-001..CA-013 ponta a ponta com `TestClient` (FastAPI), confirmando códigos HTTP e ausência de PII nos logs capturados.

### 6.4 Casos de Borda

- Chave com aspas (`'"admin-secret"'`) deve continuar funcionando via `_clean_key` (já coberto por `test_admin.py:127-132`); garantir que a checagem de força não reprove indevidamente após limpeza.
- `Authorization: Bearer <chave>` no webhook (header) continua válido (RF-003 não pode quebrar o header Bearer em `src/interfaces/http/app.py:471-473`).
- Múltiplas chaves aceitas (`WEBHOOK_API_KEY` + `EVOLUTION_WEBHOOK_API_KEY` + fallback `EVOLUTION_API_KEY`): bater com qualquer uma autentica (preservar comportamento de `_get_configured_api_keys`).
- `delete_day_block` com `event_id` vazio continua retornando `False` (já em `calendar_service.py:365-366`).
- Bloqueio identificado pelo prefixo de summary `[WPP-DENTAL] Bloqueio` (sem `extendedProperties`) também é aceito para exclusão (RF-007).
- Mismatch repetido não deve floodar log (reutilizar flag de "logado uma vez" como `_webhook_auth_*_warning_logged`).

## 7. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Ativar 401 derruba produção se a Evolution não estiver enviando a chave por header | Média | Alto | Manter fallback `EVOLUTION_API_KEY`; documentar configuração do header `apikey` na Evolution; só rejeitar quando há chave configurada; comunicar antes do deploy |
| Teste existente `test_message_webhook_accepts_request_without_valid_auth_header` (`test_main_webhook.py:63-82`) passa a falhar | Alta | Médio | Atualizar o teste para esperar `401` como parte da entrega (tarefa de teste explícita) |
| Detecção de produção via `ENVIRONMENT` ausente em deploy real deixa painel aberto sem querer | Média | Alto | Padrão seguro: tratar valor desconhecido/ausente como dev apenas localmente; documentar `ENVIRONMENT=production` no `.env.example`; logar quando o painel ficar aberto |
| Leitura extra do Calendar antes de deletar bloqueio falha por erro de rede | Baixa | Médio | Capturar erro e retornar payload genérico; não apagar em caso de dúvida (fail-safe) |
| Redação de logs esconde informação útil para debug | Média | Baixo | Redigir parcialmente (ex.: últimos 4 dígitos do telefone) e manter `message_id`/contadores não sensíveis |
| Falso positivo em `event_is_day_block` impede remover um bloqueio legítimo criado manualmente | Baixa | Médio | Aceitar tanto o marcador `wpp_dental_type` quanto o prefixo de summary; documentar critério |

## 8. Dependências

### 8.1 Internas

- **Implementação 001 — Estabilidade da API e Resiliência de IO:** base de tratamento de erro/resiliência reutilizada nos `try/except` dos endpoints do painel (AD-03) e nas chamadas ao Calendar.
- **Implementação 002 — Recuperação da Rede de Testes:** suíte de testes operante é pré-requisito para validar os critérios desta spec (`tests/test_main_webhook.py`, `tests/test_admin.py`).

### 8.2 Externas

- **FastAPI / Starlette** — `Request`, `HTTPException`, `APIRouter` (já em uso).
- **Google Calendar API** (`googleapiclient`) — `events().get/delete` em `CalendarService`.
- **Evolution API** — envia o webhook com header `apikey`; a política de 401 depende dessa configuração.
- **SQLite** (`src/infrastructure/persistence/connection.py:get_db`) — fonte dos dados dos endpoints do painel.
- **`hmac`** (stdlib) — comparação de chaves em tempo constante.

## 9. Observações e Decisões de Design

- **Por que tolerar webhook sem chave:** instalações legadas podem ainda não ter `WEBHOOK_API_KEY`. Em vez de quebrar com 401 imediato, mantém-se o processamento mas eleva-se o log para CRÍTICO, criando pressão operacional para configurar a chave sem causar indisponibilidade súbita.
- **Defesa em profundidade na exclusão:** a checagem `event_is_day_block` é feita tanto no endpoint (`delete_block`) quanto no método (`delete_day_block`). Mesmo que algum chamador futuro pule o endpoint, o método de infraestrutura recusa apagar não-bloqueios — protege contra AD-02 de forma robusta.
- **Placeholder público:** `.env.example:9` distribui `your-admin-panel-key`. O helper de força de chave trata esse valor explicitamente como inválido, evitando que um deploy "configurado" com o placeholder pareça protegido sem estar.
- **Redação de PII:** opta-se por redação parcial (ex.: mascarar o telefone preservando os últimos 4 dígitos e contadores não sensíveis) para equilibrar privacidade (RNF-004) e capacidade de diagnóstico. O payload completo deixa de ser logado.
- **Compatibilidade de headers:** mantém-se a aceitação de `apikey`/`x-api-key`/`x-webhook-key`/`Authorization: Bearer` e query string; apenas a leitura da chave do corpo do payload é removida (WE-09).
- **Mensagem de erro genérica vs. operador:** AD-06 prioriza não vazar detalhes ao cliente. O detalhe real continua acessível ao time via log server-side, sem expor credenciais no front (`src/interfaces/http/admin.py:682,693`).
