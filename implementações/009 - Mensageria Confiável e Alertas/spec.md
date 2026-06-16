# Mensageria Confiável e Alertas

> **ID:** 009
> **Status:** 🟢 Concluída
> **Prioridade:** 🟠 Alta
> **Criada em:** 2026-06-16
> **Última atualização:** 2026-06-16 (concluída)
> **Autor:** Agente AI

---

## 1. Resumo Executivo

Garante que toda mensagem ao paciente seja efetivamente entregue antes de qualquer mudança de estado, e que alertas à doutora nunca sejam descartados silenciosamente. Inclui retry com backoff, validação de telefone, coluna `kind` no eco de saída para não confundir respostas manuais da doutora com alertas, e persistência de alertas que falharam para reenvio manual.

## 2. Contexto e Motivação

### 2.1 Problema Atual
Vários bugs de mensageria coexistem: (1) `_send_response` divide por `\n\n` e chunk1 é duplicado em retentativas do webhook; (2) `WhatsAppService` não tem retry — qualquer falha transiente perde a entrega permanentemente; (3) `_format_phone` aceita números inválidos (DDD/tamanho errados); (4) `AlertService.send_alert` ignora o retorno do envio e alertas falham silenciosamente; (5) `ConversationStateService.clear` é chamado ANTES de `_send_response` no fluxo de confirmação, destruindo o estado se a entrega falhar; (6) eco de alerta da doutora pode ser confundido com resposta manual; (7) `DOCTOR_PHONE` não validado na inicialização.

### 2.2 Impacto do Problema
- Paciente não recebe confirmação de cancelamento mas o evento já foi excluído do Calendar (WE-02)
- Doutora nunca recebe alertas de escopo mas o sistema registra sucesso (WH-03)
- Respostas do paciente chegam duplicadas ou são silenciadas (WH-02)
- Alertas chegam ao número errado/inválido sem falhar explicitamente (WH-06)

### 2.3 Soluções Consideradas

| Solução | Prós | Contras | Decisão |
|---------|------|---------|---------|
| Retry exponencial + check de delivered | Resolve WH-05 + WE-02 sem mudança de arquitetura | Aumenta latência em falhas transientes | ✅ Escolhida |
| Fila persistente (Celery/Redis) | Retry robusto + dead-letter queue | Aumenta infra, overkill para volume atual | ❌ Descartada |

## 3. Especificação Técnica

### 3.1 Componentes Afetados

| Componente | Tipo | Ação |
|-----------|------|------|
| `src/infrastructure/integrations/whatsapp_service.py` | Arquivo | Modificar — retry, phone validation, kind param |
| `src/infrastructure/integrations/alert_service.py` | Arquivo | Modificar — failure handling, kind param |
| `src/infrastructure/persistence/outbound_message_store.py` | Arquivo | Modificar — kind column |
| `src/infrastructure/persistence/connection.py` | Arquivo | Modificar — pending_alerts table, kind migration |
| `src/infrastructure/persistence/failed_alert_store.py` | Arquivo | Criar — persistência de alertas falhos |
| `src/interfaces/http/app.py` | Arquivo | Modificar — _send_response, lifespan, delivered checks |

### 3.2 Fluxo de Execução

**WH-02 — _send_response simplificado:**
Envia a resposta como mensagem única (sem split por `\n\n`). Elimina a duplicação de chunk1 em retentativas.

**WH-05 — retry com backoff:**
`send_message` e `send_message_sync` tentam até `WHATSAPP_SEND_RETRIES` (default 2) vezes com espera exponencial (2^attempt segundos) entre tentativas.

**WH-06 — validação de telefone:**
`_format_phone` valida: 12 ou 13 dígitos após formatação (55 + DDD + 8 ou 9 dígitos). DDD inválido (< 11 ou > 99) → retorna `""` + `logger.warning`.

**WH-03/WH-09 — AlertService com persistência:**
`send_alert` e variantes verificam retorno de `send_message_sync`. Se `False`: `FailedAlertStore.record(...)` + `logger.critical`. Template com placeholder ausente: log `WARNING` + usa mensagem de fallback.

**CO-01 — validação de DOCTOR_PHONE no startup:**
`lifespan` chama `config.get_doctor_phone()` logo após `ConfigService()`. Se vazio: `logger.critical` com instrução de configuração.

**WE-02 — delivered check antes de clear/cancel:**
Reordena os branches de `_handle_appointment_confirmation`: send → check delivered → if fail raise HTTPException(502) → then clear state / mark processed. O `ConversationStateService.clear` nunca é chamado antes da entrega bem-sucedida.

**WE-12 — mark_patient_response nos branches faltantes:**
Branches remarcar e confirmar chamam `AppointmentConfirmationService.mark_patient_response` após entrega confirmada.

**WH-04/WH-08 — kind column:**
`outbound_messages.kind TEXT NOT NULL DEFAULT 'bot'`. Alertas registrados com `kind='doctor_alert'`. `consume_recent_match` filtra `kind != 'doctor_alert'` para evitar confundir respostas manuais da doutora com ecos.

### 3.3 Tratamento de Erros

- Entrega falhou após retries: `logger.error` + retorna `False`
- Alerta falhou: `FailedAlertStore.record` + `logger.critical`
- Phone inválido: `logger.warning` + retorna `""` (sem envio)
- DOCTOR_PHONE não configurado: `logger.critical` no startup (continua operando)

## 4. Requisitos

### 4.1 Requisitos Funcionais

- **RF-001:** `_send_response` envia como mensagem única (sem split por `\n\n`)
- **RF-002:** `send_message`/`send_message_sync` tentam até `WHATSAPP_SEND_RETRIES` vezes com backoff 2^attempt
- **RF-003:** `_format_phone` rejeita números com DDD inválido ou tamanho ≠ 12/13 dígitos
- **RF-004:** `AlertService` persiste alertas falhos em `pending_alerts` + loga `logger.critical`
- **RF-005:** `lifespan` loga `logger.critical` se `DOCTOR_PHONE` não configurado
- **RF-006:** `ConversationStateService.clear` só é chamado APÓS entrega bem-sucedida nos branches de confirmação
- **RF-007:** Branches remarcar e confirmar chamam `mark_patient_response` após entrega
- **RF-008:** `outbound_messages` tem coluna `kind`; alertas registrados como `'doctor_alert'`
- **RF-009:** `consume_recent_match` exclui registros `kind='doctor_alert'` do match de conteúdo

### 4.2 Requisitos Não-Funcionais

- **RNF-001:** Retry backoff máximo = 4s (2 retries: 1s + 2s = 3s extras)
- **RNF-002:** `FailedAlertStore` não pode derrubar a thread do AlertService

## 5. Critérios de Aceitação

- [ ] **CA-001:** `_send_response("5511999999999", "linha1\n\nlinha2")` chama `send_message` uma única vez com o texto completo
- [ ] **CA-002:** `send_message` com serviço falhando: tenta 3x (1 original + 2 retries), retorna False
- [ ] **CA-003:** `_format_phone("5599999999")` retorna `""` (DDD 99 mas length 12 is valid... wait)
- [ ] **CA-003:** `_format_phone("551099999999")` retorna `""` (DDD 10 inválido)
- [ ] **CA-004:** `AlertService.send_alert` com `send_message_sync` retornando False: chama `FailedAlertStore.record` + `logger.critical`
- [ ] **CA-005:** `lifespan` com `DOCTOR_PHONE=""`: logger.critical chamado durante startup
- [ ] **CA-006:** Branch confirmar de `_handle_appointment_confirmation`: `ConversationStateService.clear` chamado APÓS `_send_response` retornar True
- [ ] **CA-007:** Branch remarcar: `mark_patient_response(status="rescheduled")` chamado após entrega
- [ ] **CA-008:** Branch confirmar: `mark_patient_response(status="confirmed")` chamado após entrega
- [ ] **CA-009:** `consume_recent_match` retorna False para content match em registro `kind='doctor_alert'`

## 6. Plano de Testes

### 6.1 Testes Unitários (T-011)
- WH-05: mock httpx, verificar número de chamadas (retry count)
- WH-06: tabela parametrizada de phones válidos/inválidos
- WH-03: mock `send_message_sync` → False; verificar `FailedAlertStore.record` chamado
- WH-09: mock `config.get_message` → string com placeholder; verificar fallback
- CO-01: mock `config.get_doctor_phone` → ""; verificar logger.critical

### 6.2 Testes de Integração (T-012)
- WE-02: mock `_send_response` → False em branch confirmar; verificar HTTPException(502) e clear NÃO chamado
- WE-12: mock `_send_response` → True em branch remarcar/confirmar; verificar mark_patient_response chamado
- WH-02: verificar send_message chamado 1x com texto completo
- WH-04/WH-08: `consume_recent_match` com kind='doctor_alert' no DB

## 7. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|-------|--------------|---------|-----------|
| Retry aumenta latência perceptível | Baixa | Baixo | Backoff curto (1s + 2s); timeout mantido em 30s |
| FailedAlertStore falhar e mascarar o erro | Baixa | Médio | try/except ao redor de record(); critical já logado antes |
| kind=DEFAULT quebrar DBs existentes | Baixa | Baixo | Migration via `_ensure_column` com DEFAULT 'bot' |

## 8. Dependências

### 8.1 Dependências Internas
- Impl. 001, Impl. 002

### 8.2 Dependências Externas
- `httpx` (já presente)
- `asyncio` (já presente)

---

> **⚠️ NOTA:** Este documento é a fonte de verdade para esta implementação.
