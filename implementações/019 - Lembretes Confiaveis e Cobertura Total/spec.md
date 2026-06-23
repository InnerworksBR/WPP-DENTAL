# Lembretes Confiáveis e Cobertura Total

> **ID:** 019
> **Status:** 🟢 Concluída
> **Prioridade:** 🔴 Crítica
> **Criada em:** 2026-06-23
> **Última atualização:** 2026-06-23
> **Autor:** Agente AI
> **Fase do roadmap:** B (`docs/ANALISE_SOLUCAO_DEFINITIVA.md` §7, §10)

---

## 1. Resumo Executivo

Elimina o **descarte silencioso** de pacientes no cron de confirmação do dia seguinte. Hoje há
vários pontos onde uma consulta é pulada sem que a clínica saiba (sem telefone, evento manual fora
do formato, estado de conversa "ocupado"). Esta implementação torna a cobertura **observável e
recuperável**: um **relatório diário** à clínica (`enviados / pulados / falhas`, com nome e motivo de
cada pulado), uma **fila de re-tentativa** para falhas de envio, e a exposição dos pulados/pendentes
no painel `/admin`. Resolve o sintoma **(c) "o lembrete não chega a todos os pacientes"**. Mantém
OpenAI e Evolution API — é uma correção determinística do cron, sem troca de transporte.

## 2. Contexto e Motivação

### 2.1 Problema Atual

`AppointmentConfirmationService.send_next_day_confirmations`
(`src/application/services/appointment_confirmation_service.py:286`) já retorna um dict de estatísticas
(`candidates`, `sent`, `skipped_duplicates`, `skipped_busy`, `failed`), mas **ninguém consome esse
resumo** — ele só vai para o log. Pontos de perda mapeados:

| # | Ponto de descarte | Local | Comportamento hoje |
|---|---|---|---|
| 1 | Sem telefone **e** sem 1 cadastro único pelo nome | `_resolve_missing_phones:224–247` | Só `WARNING` no log; segue adiante com telefone vazio e falha depois |
| 2 | Telefone/`event_id` vazio na deduplicação | `_select_unique_appointments:249–260` | Descarte **silencioso** |
| 3 | Estado de conversa não-`idle` (recente **ou** expirado) | `send_next_day_confirmations:314–338` | `INFO`; pulado (`skipped_busy`) para não interromper |
| 4 | Evento *all-day* / sem `dateTime` | `calendar_service.find_patient_appointments_for_date:750–753` | Removido na busca, **silencioso** |
| 5 | Falha de envio (`whatsapp.send_message` → False) | `send_next_day_confirmations:359–366` | Marca `failed` no DB; **sem re-tentativa automática** |

`PatientService.find_by_name` (`patient_service.py:60–78`) só resolve quando há **exatamente 1**
cadastro com o nome normalizado — homônimos ⇒ `None` ⇒ cai no ponto 1.

### 2.2 Impacto do Problema

A clínica acredita que "todos foram lembrados", mas pacientes foram pulados em silêncio — gerando
faltas, buracos na agenda e a queixa direta do dono ("o lembrete não chega a todos"). Como o resumo
existe mas não é entregue, **não há visibilidade** nem ação manual possível. O `failed_alert_store` e
o `outbound_message_store` já existem para apoiar recuperação, mas não são usados pelo cron de
lembretes.

### 2.3 Soluções Consideradas

| Solução | Prós | Contras | Decisão |
|---------|------|---------|---------|
| **Tornar a cobertura observável (relatório diário) + fila de re-tentativa + pendências no /admin** | Nunca pula em silêncio; clínica age manualmente; determinístico e testável; reusa stores existentes | Requer disciplina de cobrir cada caminho de descarte com teste | ✅ **Escolhida** |
| "Forçar envio" mesmo sem telefone/estado | Aparenta cobrir tudo | Manda mensagem errada/para ninguém; interrompe conversa em andamento; piora a experiência | ❌ Descartada |
| Trocar de transporte/agenda | — | Fora de escopo; dono decidiu manter Evolution e Google Calendar | ❌ Descartada |

## 3. Especificação Técnica

### 3.1 Visão Geral da Arquitetura

O cron mantém a lógica de envio, mas passa a **registrar e reportar** cada decisão:

```
send_next_day_confirmations
   ├─ coleta candidatos (Calendar) ──► registra "sem dateTime" como pulado observável
   ├─ resolve telefone (nome) ───────► sem resolução = pulado observável (motivo: sem telefone)
   ├─ dedup ─────────────────────────► descarte vira pulado observável (motivo)
   ├─ estado ocupado ────────────────► pulado observável (motivo: conversa em andamento)
   ├─ envia ─────────────────────────► falha → fila de re-tentativa (failed_alert_store/outbound)
   └─ AO FIM:
         ├─ Relatório diário à clínica: enviados / pulados / falhas + nome + motivo
         └─ pendências persistidas e expostas em /admin
```

### 3.2 Componentes Afetados

| Componente | Tipo | Ação | Descrição |
|-----------|------|------|-----------|
| `src/application/services/appointment_confirmation_service.py` | Arquivo | Modificar | Acumular `skipped` com `{nome, motivo, event_id}`; montar e enviar relatório diário; enfileirar falhas para re-tentativa |
| `src/interfaces/http/app.py` | Arquivo | Modificar | Scheduler (`_run_appointment_confirmation_scheduler:80`) dispara o relatório ao fim; catch-up coerente |
| `src/interfaces/http/admin.py` | Arquivo | Modificar | Expor pulados/pendentes (novo endpoint/seção) lendo a tabela de cobertura |
| `src/infrastructure/persistence/connection.py` | Arquivo | Modificar | Tabela de cobertura diária (`reminder_coverage` ou reuso de `appointment_confirmations` com status `skipped`) |
| `src/infrastructure/persistence/failed_alert_store.py` | Arquivo | Reusar/Estender | Fila de re-tentativa de falhas de envio |
| `src/infrastructure/persistence/outbound_message_store.py` | Arquivo | Reusar | Distinguir eco/reenvio na re-tentativa |
| `src/application/services/patient_service.py` | Arquivo | Reusar | `find_by_name` (resolução por nome) |
| `tests/test_appointment_confirmation_service.py` | Arquivo | Criar/Modificar | Um teste por caminho de descarte + relatório + re-tentativa |

### 3.3 Interfaces e Contratos

#### Entradas
- `send_next_day_confirmations(reference_time)` (inalterado na assinatura pública).
- Config: telefone de destino do relatório (reusar `DOCTOR_PHONE`/config da clínica).

#### Saídas
- Dict de estatísticas **estendido** com `skipped_details: list[{name, reason, event_id}]` e
  `report_sent: bool`.
- Mensagem de relatório diário enviada à clínica via o mesmo gateway de mensageria.
- Linhas de cobertura persistidas (status `sent` | `skipped` | `failed` + `reason`).

#### Contratos de API (se aplicável)
- `GET /admin/api/coverage` (novo) — retorna a cobertura do dia: enviados, e a lista de
  pulados/falhas com nome e motivo. (Alternativa: estender `/admin/api/summary:133`.)

### 3.4 Modelos de Dados (se aplicável)

Cobertura por consulta/dia. Preferir **reuso** da `appointment_confirmations`
(`connection.py:54–66`) acrescentando os status `skipped`/`failed` com a coluna `reason`
(adicionar `reason TEXT` se não existir). Caso o reuso conflite com a constraint
`UNIQUE(event_id, reminder_type, appointment_start)`, criar tabela dedicada `reminder_coverage`
(`run_date`, `event_id`, `patient_name`, `phone`, `outcome`, `reason`, `created_at`).
**Decisão registrada na execução.**

### 3.5 Fluxo de Execução

1. Scheduler dispara `send_next_day_confirmations` no horário (`REMINDER_HOUR=20`).
2. Para cada consulta candidata: resolve telefone; se falhar, **registra pulado** (motivo) em vez de
   seguir com telefone vazio.
3. Evento sem `dateTime` / fora do formato → **registra pulado** (motivo), não some.
4. Estado ocupado → **registra pulado** (motivo: conversa em andamento).
5. Envio bem-sucedido → status `sent`. Falha → status `failed` + **enfileira re-tentativa**.
6. Ao fim, monta o **relatório diário** (`enviados X / pulados Y / falhas Z` + nome e motivo de cada
   pulado/falha) e envia à clínica.
7. Pendências ficam disponíveis no `/admin`.

### 3.6 Tratamento de Erros

- Falha ao **enviar o relatório** → registrar em `failed_alert_store` e logar `ERROR` (o relatório
  nunca pode falhar em silêncio também).
- Re-tentativa de envio limitada (ex.: N tentativas) com backoff; após o limite, marca `failed` e
  inclui no relatório como "falha definitiva — acionar manualmente".
- Exceção por consulta (`387–396`) não derruba o lote; entra no relatório como falha.

## 4. Requisitos

> Rastreabilidade ao documento macro: `docs/ANALISE_SOLUCAO_DEFINITIVA.md` §7 (4 correções
> propostas) e §10-Fase B.

### 4.1 Requisitos Funcionais

- **RF-001:** Todo caminho de descarte (sem telefone, dedup, estado ocupado, evento sem `dateTime`)
  é **registrado** com `{nome, motivo, event_id}` — nunca silencioso.
- **RF-002:** Ao fim do cron, um **relatório diário** é enviado à clínica com
  `enviados / pulados / falhas` e o nome + motivo de cada pulado e falha.
- **RF-003:** Falhas de envio são **re-tentadas entre execuções** pelo mecanismo já existente
  (`_try_claim_reminder_send` re-reivindica reminders com status `failed`/`processing` no ciclo
  seguinte) **e** surgem no relatório diário com motivo, para ação manual imediata da clínica. Decisão
  (execução): não foi criada uma fila in-run dedicada (a resiliência de envio já vem da impl 009 +
  re-claim cross-run); ver §9.
- **RF-004:** Pulados e pendentes ficam **visíveis no `/admin`** (endpoint/seção de cobertura).
- **RF-005:** Resolução de telefone por nome mais robusta; consulta "sem telefone" vira **pendência
  visível**, não só `warning` de log.
- **RF-006:** O relatório em si nunca falha em silêncio (falha de envio do relatório é registrada).

### 4.2 Requisitos Não-Funcionais

- **RNF-001:** Suíte total **verde**, com **um teste por caminho de descarte** (regra de ouro).
- **RNF-002:** Sem aumento relevante de custo: relatório é 1 mensagem/dia; re-tentativa limitada.
- **RNF-003:** Idempotência preservada (não reenviar lembrete já confirmado — manter
  `_try_claim_reminder_send` / constraint única).
- **RNF-004:** Sem regressão na 010 (cron/catch-up) e 013 (cobertura/telefone por nome).

### 4.3 Restrições e Limitações

- **Manter** Evolution API e Google Calendar (decisão do dono). Sem troca de transporte/agenda.
- Não interromper conversa em andamento: estado ocupado continua sendo motivo legítimo de pular —
  mas agora **reportado**, não silencioso.

## 5. Critérios de Aceitação

- [ ] **CA-001:** Nenhum caminho de descarte é silencioso: cada pulado gera registro com nome+motivo.
- [ ] **CA-002:** Após o cron, a clínica recebe um relatório `enviados/pulados/falhas` com nomes e
  motivos (verificado por teste com gateway mockado).
- [ ] **CA-003:** Falha de envio entra na fila de re-tentativa e é reenviada (ou marcada falha
  definitiva após o limite).
- [ ] **CA-004:** `/admin` expõe os pulados/pendentes do dia (endpoint/seção de cobertura).
- [ ] **CA-005:** Consulta sem telefone aparece como pendência visível, não só log.
- [ ] **CA-006:** Existe teste cobrindo **cada** ponto de descarte (1–5 da §2.1).
- [ ] **CA-007:** Suíte total verde; sem regressão na 010/013.

## 6. Plano de Testes

### 6.1 Testes Unitários
- `_resolve_missing_phones`: sem telefone e sem match → registrado como pulado (motivo).
- `_select_unique_appointments`: descarte por dedup → registrado, não silencioso.
- Estado ocupado (recente e expirado) → pulado com motivo.
- Evento sem `dateTime` → pulado observável.
- Montagem do relatório: contadores e lista de nomes/motivos corretos.

### 6.2 Testes de Integração
- `send_next_day_confirmations` ponta-a-ponta com gateway/calendar mockados: confere stats estendidos,
  relatório enviado, fila de re-tentativa populada em falha.
- `/admin/api/coverage` retorna os pulados do dia.

### 6.3 Testes de Aceitação
- CA-001..CA-007 verificáveis por teste.

### 6.4 Casos de Borda (Edge Cases)
- Dia sem nenhuma consulta → relatório "0 enviados" (ainda enviado, sem ruído excessivo — avaliar
  resumo curto).
- Homônimos no cadastro (`find_by_name` → None) → pendência "telefone ambíguo".
- Re-tentativa que esgota o limite → falha definitiva listada no relatório.
- Falha ao enviar o próprio relatório → registrada em `failed_alert_store`.
- Catch-up após restart (010) não duplica relatório nem lembretes.

## 7. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|-------|--------------|---------|-----------|
| Relatório virar spam/ruído para a clínica | Média | Baixo | 1 mensagem/dia, resumo objetivo; dia vazio = resumo curto |
| Re-tentativa causar envio duplicado | Média | Alto | Reusar `_try_claim_reminder_send` + constraint única; `outbound_message_store` para eco |
| Mudança de schema quebrar dados existentes | Baixa | Médio | Migração aditiva (coluna `reason`/tabela nova); testes de persistência |
| Reportar dado sensível (PII) em log/relatório | Baixa | Médio | Relatório só ao telefone da clínica; preservar política de logs da 012 |

## 8. Dependências

### 8.1 Dependências Internas
- **010** (Confirmação Proativa, Cron e Handoff) e **013** (Cobertura do Cron / telefone por nome) —
  base do cron atual. **009** (mensageria/alertas) para o envio do relatório e da fila.
- **012** (segurança/PII) — preservar política de logs.

### 8.2 Dependências Externas
- Nenhuma nova (usa o gateway de mensageria/Evolution já existente).

## 9. Observações e Decisões de Design

- O resumo de cobertura **já é calculado** por `send_next_day_confirmations` (retorno dict) — o ganho
  central é **entregá-lo** e **persistir os detalhes**, não recalcular.
- Princípio-guia (`docs/ANALISE_SOLUCAO_DEFINITIVA.md` §7): "o lembrete pode até falhar para um caso,
  mas **nunca em silêncio** — a clínica aciona manualmente".
- Decisão reuso-de-tabela vs. tabela nova (`reminder_coverage`) será registrada na execução conforme
  a constraint `UNIQUE` da `appointment_confirmations`.

**Decisões de execução (2026-06-23):**
- **Tabela nova `reminder_coverage`** (não reuso de `appointment_confirmations`): a constraint
  `UNIQUE(event_id, reminder_type, appointment_start)` e os status existentes (`sent`/`failed`/
  `processing`) tornariam o reuso ambíguo. `reminder_coverage` registra só os **não contatados**
  (`outcome` ∈ `skipped`/`failed`) com motivo; idempotente por `run_date` (re-grava ao reexecutar).
  Store isolado: `ReminderCoverageStore`.
- **Relatório guardado por `DOCTOR_PHONE`:** só envia se configurado (em produção, está no `.env`).
  Em testes sem essa config, o cron segue sem relatório — zero impacto nos testes legados.
- **Re-tentativa (RF-003):** atendida por `_try_claim_reminder_send` (re-claim cross-run de `failed`)
  + resiliência de envio da impl 009 + surfacing no relatório. Fila in-run dedicada considerada
  over-engineering para um cron diário; não construída.
- **`/admin/api/coverage`:** endpoint novo (aditivo) lê `reminder_coverage` + conta `sent` de
  `appointment_confirmations` do dia. Sem `run_date`, usa a execução mais recente.

---

> **⚠️ NOTA:** Contrato vivo. Alterações de escopo refletidas aqui antes do código.
