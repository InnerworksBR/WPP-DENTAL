# Tarefas: Identidade do Paciente e Normalizacao de Telefone

> **Implementação:** 004 - Identidade do Paciente e Normalizacao de Telefone
> **Spec:** [spec.md](./spec.md)
> **Progresso:** 0/12 tarefas concluídas (0%)
> **Última atualização:** 2026-06-15

Legenda: [ ] Pendente, [x] Concluída, [!] Bloqueada, [-] Cancelada.

---

## Fase 1 — Preparação

### [ ] T-001 — Mapear e fixar contratos de telefone
- **Descrição:** Confirmar todos os consumidores de `phone_service` (6 modulos via grep) e definir as assinaturas novas (`canonical_phone`, `is_valid_phone`, `phones_match`) sem quebrar `normalize_internal_phone`/`build_phone_search_term`.
- **Arquivos envolvidos:** `src/domain/policies/phone_service.py`, `src/domain/policies/__init__.py`
- **Critério de conclusão:** Lista de consumidores validada e assinaturas das novas funcoes acordadas e documentadas no PR.
- **Dependências:** Implementacoes 001 e 002.
- **Estimativa:** Pequena

## Fase 2 — Implementação

### [ ] T-002 — Implementar forma canonica do telefone BR (PH-01)
- **Descrição:** Criar `canonical_phone(value)` que reconcilia o 9o digito (com/sem) e o prefixo `55`, e `phones_match(a,b)`. Garantir idempotencia (RNF-001).
- **Arquivos envolvidos:** `src/domain/policies/phone_service.py`, `src/domain/policies/__init__.py`
- **Critério de conclusão:** Atende RF-001; `canonical_phone` das 3 variacoes do mesmo numero retorna a mesma chave.
- **Dependências:** T-001
- **Estimativa:** Média

### [ ] T-003 — Validacao de telefone e fim do prefixo "55" cego (PH-04, PH-05)
- **Descrição:** Implementar `is_valid_phone(value)` rejeitando JID de grupo/`@lid`/curtos; corrigir `normalize_conversation_phone` (`phone_service.py:20-21`) para so prefixar `55` em numeros BR validos.
- **Arquivos envolvidos:** `src/domain/policies/phone_service.py`
- **Critério de conclusão:** Atende RF-004 e RF-005; numero nao-BR nao recebe `55`; JID nao-telefone -> `canonical_phone` vazio.
- **Dependências:** T-002
- **Estimativa:** Média

### [ ] T-004 — Match exato no PatientService (PH-02)
- **Descrição:** Trocar `WHERE phone LIKE ?` por igualdade canonica em `find_by_phone` (`patient_service.py:15-30`) e na busca interna do `upsert` (`patient_service.py:47-50`); fallback em memoria por `phones_match` para legados.
- **Arquivos envolvidos:** `src/application/services/patient_service.py`
- **Critério de conclusão:** Atende RF-002; busca por substring nao retorna paciente errado (CA-003).
- **Dependências:** T-002, T-003
- **Estimativa:** Média

### [ ] T-005 — Upsert nao-destrutivo no PatientService (PA-01)
- **Descrição:** Em `PatientService.upsert` (`patient_service.py:40-64`), nao sobrescrever nome existente valido por vazio/placeholder (ex.: telefone vindo de `app.py:506`); preservar plano quando ausente.
- **Arquivos envolvidos:** `src/application/services/patient_service.py`
- **Critério de conclusão:** Atende RF-006; CA-007 e CA-008 passam.
- **Dependências:** T-004
- **Estimativa:** Média

### [ ] T-006 — Match exato e merge nao-destrutivo nas tools (PH-02, PA-02)
- **Descrição:** Em `patient_tool.py`, trocar `LIKE %...%` por igualdade canonica em `FindPatientTool` (`:30-35`), `SavePatientTool` (`:72-76`) e `SaveInteractionTool` (`:117-123`); no UPDATE de `SavePatientTool` (`:78-85`) nao zerar plano (`plan=None`) nem sobrescrever nome bom por vazio.
- **Arquivos envolvidos:** `src/interfaces/tools/patient_tool.py`
- **Critério de conclusão:** Atende RF-002 e RF-007; CA-009 e CA-010 passam.
- **Dependências:** T-004, T-005
- **Estimativa:** Média

### [ ] T-007 — Corrigir casamento de eventos no Calendar (PH-03)
- **Descrição:** Em `find_appointments_by_phone` (`calendar_service.py:595-627`), substituir a condicao `endswith` cruzada (`:620-624`) por `phones_match(summary_phone, phone)` usando a forma canonica.
- **Arquivos envolvidos:** `src/infrastructure/integrations/calendar_service.py`
- **Critério de conclusão:** Atende RF-003; CA-004 passa (evento de outro paciente nao e retornado).
- **Dependências:** T-002
- **Estimativa:** Média

### [ ] T-008 — Reconciliacao de legados pelo canonico
- **Descrição:** Ajustar `_normalize_patient_phone_rows` (`connection.py:113+`) para agrupar por `canonical_phone`, reconciliando registros com e sem 9o digito de forma idempotente, preservando nome/plano bons.
- **Arquivos envolvidos:** `src/infrastructure/persistence/connection.py`
- **Critério de conclusão:** Atende RNF-003; dois registros legados (com/sem 9) viram um so sem perda de dados.
- **Dependências:** T-002, T-005
- **Estimativa:** Média

## Fase 3 — Testes

### [ ] T-009 — Testes de regressao do dominio (PH-01, PH-04, PH-05)
- **Descrição:** Unitarios para `canonical_phone` (3 variacoes -> mesma chave, idempotencia), `is_valid_phone` (BR/nao-BR/grupo/LID/curto), `normalize_conversation_phone` sem `55` cego.
- **Arquivos envolvidos:** `tests/` (novo, ex.: `tests/test_phone_service.py`)
- **Critério de conclusão:** CA-001, CA-005, CA-006 cobertos e verdes.
- **Dependências:** T-002, T-003
- **Estimativa:** Média

### [ ] T-010 — Testes de regressao de paciente (PH-02, PA-01, PA-02)
- **Descrição:** Integracao contra SQLite: busca exata sem colisao por substring; upsert merge de nome; `SavePatientTool` preservando plano e nome.
- **Arquivos envolvidos:** `tests/` (novo, ex.: `tests/test_patient_identity.py`)
- **Critério de conclusão:** CA-003, CA-007..CA-010 verdes.
- **Dependências:** T-004, T-005, T-006
- **Estimativa:** Média

### [ ] T-011 — Testes de regressao do Calendar e remarcacao (PH-03, PH-01)
- **Descrição:** Forjar eventos de pacientes A e B com finais coincidentes e validar isolamento em `find_appointments_by_phone`; cenario de remarcacao com 9o digito divergente garantindo 1 unico evento ativo.
- **Arquivos envolvidos:** `tests/` (novo, ex.: `tests/test_calendar_phone_match.py`)
- **Critério de conclusão:** CA-002 e CA-004 verdes.
- **Dependências:** T-007, T-008
- **Estimativa:** Média

## Fase 4 — Documentação

### [ ] T-012 — Atualizar docs e registro de progresso
- **Descrição:** Registrar as novas funcoes publicas de `phone_service`, a decisao de match exato e a reconciliacao de legados; atualizar status da spec e a tabela de progresso abaixo.
- **Arquivos envolvidos:** `implementações/004 - Identidade do Paciente e Normalizacao de Telefone/spec.md`, este `tasks.md`
- **Critério de conclusão:** CA-011 verificado (consumidores intactos) e documentacao atualizada.
- **Dependências:** T-009, T-010, T-011
- **Estimativa:** Pequena

---

## Registro de Progresso

| Tarefa | Fase | Descrição curta | Findings | Estimativa | Status |
|---|---|---|---|---|---|
| T-001 | Preparação | Mapear contratos de telefone | — | Pequena | [ ] |
| T-002 | Implementação | Forma canonica BR + phones_match | PH-01 | Média | [ ] |
| T-003 | Implementação | is_valid_phone + fim do "55" cego | PH-04, PH-05 | Média | [ ] |
| T-004 | Implementação | Match exato no PatientService | PH-02 | Média | [ ] |
| T-005 | Implementação | Upsert nao-destrutivo | PA-01 | Média | [ ] |
| T-006 | Implementação | Match exato + merge nas tools | PH-02, PA-02 | Média | [ ] |
| T-007 | Implementação | Casamento de eventos no Calendar | PH-03 | Média | [ ] |
| T-008 | Implementação | Reconciliacao de legados | PH-01 | Média | [ ] |
| T-009 | Testes | Regressao dominio | PH-01, PH-04, PH-05 | Média | [ ] |
| T-010 | Testes | Regressao paciente | PH-02, PA-01, PA-02 | Média | [ ] |
| T-011 | Testes | Regressao Calendar/remarcacao | PH-03, PH-01 | Média | [ ] |
| T-012 | Documentação | Docs + registro de progresso | — | Pequena | [ ] |
