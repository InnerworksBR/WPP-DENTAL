# Tarefas: Lembretes Confiáveis e Cobertura Total

> **Implementação:** 019 - Lembretes Confiáveis e Cobertura Total
> **Spec:** [spec.md](./spec.md)
> **Progresso:** 0/13 tarefas concluídas (0%)
> **Última atualização:** 2026-06-23

---

## Legenda

- `[ ]` — Pendente
- `[x]` — Concluída
- `[!]` — Bloqueada (ver observação)
- `[-]` — Cancelada

---

## Tarefas

### Fase 1: Preparação e Setup

- [ ] **T-001:** Fixar baseline verde e mapear caminhos de descarte
  - **Descrição:** Rodar a suíte (≥ 542 verde). Confirmar in loco os 5 pontos de descarte da spec (§2.1) e o retorno de `send_next_day_confirmations` (stats dict).
  - **Arquivos envolvidos:** `src/application/services/appointment_confirmation_service.py`
  - **Critério de conclusão:** `pytest -q` 100% verde; pontos de descarte confirmados.
  - **Dependências:** Nenhuma
  - **Estimativa:** Pequena

- [ ] **T-002:** Decidir o modelo de persistência da cobertura
  - **Descrição:** Decidir entre estender `appointment_confirmations` (status `skipped`/`failed` + coluna `reason`) ou criar `reminder_coverage`. Escrever a migração aditiva.
  - **Arquivos envolvidos:** `src/infrastructure/persistence/connection.py`
  - **Critério de conclusão:** Schema criado de forma aditiva; teste de criação/seed verde.
  - **Dependências:** T-001
  - **Estimativa:** Média
  - **Observações:** Cuidado com a constraint `UNIQUE(event_id, reminder_type, appointment_start)`.

### Fase 2: Implementação Core

- [ ] **T-003:** Registrar cada descarte com nome + motivo
  - **Descrição:** Em vez de só logar `warning`/`info`, acumular `skipped_details: [{name, reason, event_id}]` e persistir (T-002) nos pontos: sem telefone (`_resolve_missing_phones`), dedup (`_select_unique_appointments`), estado ocupado, evento sem `dateTime`.
  - **Arquivos envolvidos:** `src/application/services/appointment_confirmation_service.py`, `src/infrastructure/integrations/calendar_service.py`
  - **Critério de conclusão:** Nenhum descarte silencioso; stats estendidos retornados.
  - **Dependências:** T-002
  - **Estimativa:** Grande

- [ ] **T-004:** Resolução de telefone por nome mais robusta + pendência visível
  - **Descrição:** Tratar consulta "sem telefone" como pendência observável (não só log); homônimos → pendência "telefone ambíguo". Reusar `PatientService.find_by_name`.
  - **Arquivos envolvidos:** `src/application/services/appointment_confirmation_service.py`, `src/application/services/patient_service.py`
  - **Critério de conclusão:** Consulta sem telefone vira pendência registrada; teste verde.
  - **Dependências:** T-003
  - **Estimativa:** Média

- [ ] **T-005:** Montar e enviar o relatório diário à clínica
  - **Descrição:** Ao fim de `send_next_day_confirmations`, montar `enviados/pulados/falhas` + nome e motivo de cada pulado/falha e enviar via gateway de mensageria ao telefone da clínica (`DOCTOR_PHONE`).
  - **Arquivos envolvidos:** `src/application/services/appointment_confirmation_service.py`
  - **Critério de conclusão:** Relatório enviado (teste com gateway mockado confere conteúdo).
  - **Dependências:** T-003
  - **Estimativa:** Média

- [ ] **T-006:** Fila de re-tentativa para falhas de envio
  - **Descrição:** Falha de `whatsapp.send_message` enfileira re-tentativa (reusar `failed_alert_store`/`outbound_message_store`), com limite de tentativas + backoff; esgotado o limite → falha definitiva no relatório.
  - **Arquivos envolvidos:** `src/application/services/appointment_confirmation_service.py`, `src/infrastructure/persistence/failed_alert_store.py`, `src/infrastructure/persistence/outbound_message_store.py`
  - **Critério de conclusão:** Falha entra na fila e é reenviada; sem duplicação (idempotência preservada).
  - **Dependências:** T-003
  - **Estimativa:** Grande

- [ ] **T-007:** Garantir que o relatório nunca falhe em silêncio
  - **Descrição:** Falha ao enviar o relatório → registrar em `failed_alert_store` + log `ERROR`.
  - **Arquivos envolvidos:** `src/application/services/appointment_confirmation_service.py`
  - **Critério de conclusão:** Teste de falha do relatório registra a pendência.
  - **Dependências:** T-005
  - **Estimativa:** Pequena

- [ ] **T-008:** Expor cobertura/pendentes no `/admin`
  - **Descrição:** Novo `GET /admin/api/coverage` (ou estender `/admin/api/summary`) listando enviados e pulados/falhas do dia (nome + motivo); refletir na UI do painel.
  - **Arquivos envolvidos:** `src/interfaces/http/admin.py`
  - **Critério de conclusão:** Endpoint retorna a cobertura do dia; teste verde.
  - **Dependências:** T-003
  - **Estimativa:** Média

- [ ] **T-009:** Disparar o relatório pelo scheduler e manter catch-up coerente
  - **Descrição:** Garantir que o scheduler (`_run_appointment_confirmation_scheduler`) e o `run_catchup_if_missed` acionem o relatório sem duplicar lembretes nem relatório.
  - **Arquivos envolvidos:** `src/interfaces/http/app.py`, `src/application/services/appointment_confirmation_service.py`
  - **Critério de conclusão:** Catch-up não duplica; relatório sai uma vez por execução.
  - **Dependências:** T-005
  - **Estimativa:** Média

### Fase 3: Testes e Validação

- [ ] **T-010:** Teste por caminho de descarte (1–5)
  - **Descrição:** Um teste para cada ponto: sem telefone, dedup, estado ocupado (recente e expirado), evento sem `dateTime`. Cada um confirma registro observável.
  - **Arquivos envolvidos:** `tests/test_appointment_confirmation_service.py`
  - **Critério de conclusão:** CA-006 atendido; todos verdes.
  - **Dependências:** T-003, T-004
  - **Estimativa:** Grande

- [ ] **T-011:** Testes de relatório e re-tentativa
  - **Descrição:** Relatório com contadores e nomes/motivos corretos; fila de re-tentativa reenvia e respeita limite; sem duplicação.
  - **Arquivos envolvidos:** `tests/test_appointment_confirmation_service.py`
  - **Critério de conclusão:** CA-002, CA-003 atendidos; verdes.
  - **Dependências:** T-005, T-006, T-007
  - **Estimativa:** Média

- [ ] **T-012:** Não-regressão (010/013) + suíte completa
  - **Descrição:** Rodar a suíte inteira; confirmar 010 (cron/catch-up) e 013 (telefone por nome/cobertura) intactos.
  - **Arquivos envolvidos:** suíte completa
  - **Critério de conclusão:** `pytest -q` 100% verde (≥ baseline da T-001).
  - **Dependências:** T-008, T-009, T-010, T-011
  - **Estimativa:** Pequena

### Fase 4: Documentação e Finalização

- [ ] **T-013:** Atualizar status e índice
  - **Descrição:** Marcar CA-001..CA-007, status do `spec.md` → 🟢 Concluída, atualizar `implementações/README.md` (Fase 3) e a memória de status do projeto.
  - **Arquivos envolvidos:** `implementações/019 - Lembretes Confiaveis e Cobertura Total/spec.md`, `implementações/README.md`
  - **Critério de conclusão:** Índice e spec refletindo a conclusão; suíte verde.
  - **Dependências:** T-012
  - **Estimativa:** Pequena

---

## Registro de Progresso

| Tarefa | Status | Data de Conclusão | Observações |
|--------|--------|-------------------|-------------|
| T-001  | ⬜ Pendente | — | — |
| T-002  | ⬜ Pendente | — | — |
| T-003  | ⬜ Pendente | — | — |
| T-004  | ⬜ Pendente | — | — |
| T-005  | ⬜ Pendente | — | — |
| T-006  | ⬜ Pendente | — | — |
| T-007  | ⬜ Pendente | — | — |
| T-008  | ⬜ Pendente | — | — |
| T-009  | ⬜ Pendente | — | — |
| T-010  | ⬜ Pendente | — | — |
| T-011  | ⬜ Pendente | — | — |
| T-012  | ⬜ Pendente | — | — |
| T-013  | ⬜ Pendente | — | — |

---

> **📌 NOTA:** Regra de ouro do projeto — nada concluído sem teste de regressão verde.
> Runner: `C:/Apps/WPP-DENTAL/.venv/Scripts/python.exe -m pytest -q`.
