# Tarefas: Confirmacao Proativa, Cron e Handoff

> **Implementação:** 010 - Confirmacao Proativa, Cron e Handoff
> **Spec:** [spec.md](./spec.md)
> **Progresso:** 0/14 tarefas concluídas (0%)
> **Última atualização:** 2026-06-15

Legenda: [ ] Pendente, [x] Concluída, [!] Bloqueada, [-] Cancelada.

---

## Fase 1 — Preparação

### T-001 — Mapear pontos de mudanca e fixar baseline de testes
- **Descrição:** Confirmar arquivo:linha de cada finding (WE-08/CA-11, WE-13, HO-02, CO-04, CO-05, CO-07, CO-06, AG-07, AG-10), rodar a suite atual e registrar baseline verde.
- **Arquivos envolvidos:** `src/domain/policies/appointment_offer_service.py` (`:65-85`, `:261-268`), `src/application/services/appointment_confirmation_service.py` (`:90-129`, `:220-229`, `:255-351`), `src/application/services/handoff_service.py`, `src/application/services/clean_agent_service.py` (`:260-274`, `:292-316`), `src/interfaces/http/app.py` (`:44-103`, `:181-199`, `:282-291`, `:1229-1284`).
- **Critério de conclusão:** lista de pontos confirmada e suite atual executada com resultado registrado.
- **Dependências:** Implementacoes 002, 003, 005.
- **Estimativa:** Pequena

---

## Fase 2 — Implementação

### T-002 — Corrigir `is_affirmative_confirmation` (tokenizacao por palavra) [WE-08/CA-11]
- **Descrição:** Reescrever `is_affirmative_confirmation` para correspondencia por palavra nos tokens curtos (`sim`, `ok`, `okay`) e separar tokens de frase ("pode confirmar") que continuam por substring de frase. Garantir que "assim" e palavras com "ok" embutido nao confirmem.
- **Arquivos envolvidos:** `src/domain/policies/appointment_offer_service.py` (`:65-77`, `:261-268`).
- **Critério de conclusão:** RF-001 atendido; "assim" → False, "sim"/"ok" → True.
- **Dependências:** T-001.
- **Estimativa:** Média

### T-003 — Detectar conflito afirmativo + pedido de mudanca [WE-08/CA-11]
- **Descrição:** Adicionar deteccao de conflito (afirmacao + termos de troca/mudanca na mesma frase) e, no fluxo `_handle_appointment_confirmation`, perguntar ao paciente (confirmar vs trocar) em vez de confirmar.
- **Arquivos envolvidos:** `src/domain/policies/appointment_offer_service.py` (`:78-85`, novo auxiliar `has_change_request`), `src/interfaces/http/app.py` (`:1257-1267`).
- **Critério de conclusão:** RF-002 atendido; "pode confirmar so que preciso trocar o dia" gera pergunta, nao confirmacao.
- **Dependências:** T-002.
- **Estimativa:** Média

### T-004 — Handoff automatico com tratamento de negacao [WE-13]
- **Descrição:** Substituir a deteccao crua por substring (`marker in normalized_resp`) por deteccao que ignora marcadores negados ("nao vou encaminhar"); preparar caminho para sinal estruturado.
- **Arquivos envolvidos:** `src/interfaces/http/app.py` (`:282-291`).
- **Critério de conclusão:** RF-003 atendido; "nao vou encaminhar" nao ativa handoff; "vou encaminhar para a doutora" ativa.
- **Dependências:** T-001.
- **Estimativa:** Média

### T-005 — Extensao da janela de handoff com teto [HO-02]
- **Descrição:** Adicionar `HandoffService.extend(phone, duration_minutes=None)` com teto `MAX_WINDOW_MINUTES`; chamar no fluxo de handoff ativo ao registrar a mensagem do paciente.
- **Arquivos envolvidos:** `src/application/services/handoff_service.py` (`:13-43`), `src/interfaces/http/app.py` (`:181-199`).
- **Critério de conclusão:** RF-004 atendido; janela e empurrada ate o teto enquanto chegam mensagens.
- **Dependências:** T-001.
- **Estimativa:** Média

### T-006 — Catch-up do cron no startup [CO-04]
- **Descrição:** Adicionar `run_catchup_if_missed` e chama-lo no `lifespan` antes de agendar o proximo run; enviar lembretes do dia seguinte se as 20h foram perdidas e ainda nao foram enviados, preservando idempotencia.
- **Arquivos envolvidos:** `src/application/services/appointment_confirmation_service.py` (`:58-65`, `:255-351`), `src/interfaces/http/app.py` (`:44-103`).
- **Critério de conclusão:** RF-005 atendido; restart pos-20h envia uma unica vez.
- **Dependências:** T-001.
- **Estimativa:** Grande

### T-007 — Recuperar lembretes presos em `processing` e isolar excecoes por consulta [CO-05]
- **Descrição:** Envolver cada consulta de `send_next_day_confirmations` em `try/except` marcando `failed` em excecao pos-claim; estender `_try_claim_reminder_send` para reabrir linhas `processing` antigas. Adicionar stat `recovered`.
- **Arquivos envolvidos:** `src/application/services/appointment_confirmation_service.py` (`:90-129`, `:274-351`).
- **Critério de conclusão:** RF-006 atendido; nenhum `processing` orfao; excecao em uma consulta nao aborta o batch.
- **Dependências:** T-001.
- **Estimativa:** Grande

### T-008 — Nao apagar conversa em andamento no cron [CO-07]
- **Descrição:** Remover o `ConversationStateService.clear(phone)` aplicado a estado "expirado" (>2h); apenas pular o telefone e contar em `skipped_busy`.
- **Arquivos envolvidos:** `src/application/services/appointment_confirmation_service.py` (`:282-303`).
- **Critério de conclusão:** RF-007 atendido; estado nao-`idle` com >2h preservado.
- **Dependências:** T-001.
- **Estimativa:** Pequena

### T-009 — Dedup por (telefone, evento) no cron [CO-06]
- **Descrição:** Alterar `_select_unique_appointments` para deduplicar por (telefone, event_id), permitindo multiplos lembretes quando o paciente tem mais de uma consulta no dia seguinte.
- **Arquivos envolvidos:** `src/application/services/appointment_confirmation_service.py` (`:220-229`).
- **Critério de conclusão:** RF-008 atendido; 2 eventos do mesmo telefone geram 2 lembretes.
- **Dependências:** T-001.
- **Estimativa:** Pequena

### T-010 — Guard de loop por contagem [AG-07]
- **Descrição:** Substituir o aborto na 1a repeticao por um contador por assinatura; abortar apenas apos N repeticoes (default 2 → aborta na 3a). Manter log de diagnostico.
- **Arquivos envolvidos:** `src/application/services/clean_agent_service.py` (`:292-316`).
- **Critério de conclusão:** RF-009 atendido; repeticao legitima nao aborta a conversa.
- **Dependências:** T-001.
- **Estimativa:** Média

### T-011 — Reconhecer `DENTISTA:` na conversao de historico [AG-10]
- **Descrição:** Estender `_convert_history` para reconhecer linhas com prefixo `DENTISTA:` e emiti-las ao LLM como contexto de intervencao humana (rotulo explicito).
- **Arquivos envolvidos:** `src/application/services/clean_agent_service.py` (`:260-274`), referencia em `src/application/services/conversation_service.py` (`:119-135`).
- **Critério de conclusão:** RF-010 atendido; linha `DENTISTA:` nao e mais descartada.
- **Dependências:** T-001.
- **Estimativa:** Média

---

## Fase 3 — Testes

### T-012 — Testes unitarios das heuristicas e servicos [WE-08/CA-11, WE-13, HO-02, CO-06, AG-07, AG-10]
- **Descrição:** Cobrir `is_affirmative_confirmation` (incl. conflito afirmativo+mudanca), `HandoffService.extend`, `_select_unique_appointments` (2 eventos), `_convert_history` (`DENTISTA:`) e `_run_loop` (repeticao < N e >= N).
- **Arquivos envolvidos:** `tests/` (novos casos para os modulos de T-002..T-005, T-009, T-010, T-011).
- **Critério de conclusão:** CA-001, CA-002, CA-003, CA-004, CA-008, CA-009, CA-010 cobertos por testes verdes.
- **Dependências:** T-002, T-003, T-004, T-005, T-009, T-010, T-011.
- **Estimativa:** Grande

### T-013 — Testes de integracao do cron (catch-up, processing, expiracao) [CO-04, CO-05, CO-07]
- **Descrição:** Testar `send_next_day_confirmations`/`run_catchup_if_missed` com mocks de Calendar/WhatsApp: catch-up idempotente; excecao pos-claim → `failed`/reenvio; estado nao-`idle` >2h preservado.
- **Arquivos envolvidos:** `tests/` (integracao de `appointment_confirmation_service.py` e `app.py` lifespan).
- **Critério de conclusão:** CA-005, CA-006, CA-007 cobertos; sem envio duplicado em catch-up.
- **Dependências:** T-006, T-007, T-008.
- **Estimativa:** Grande

---

## Fase 4 — Documentação

### T-014 — Atualizar documentacao e progresso
- **Descrição:** Atualizar spec.md/tasks.md (status, progresso), registrar comportamento novo (janela de handoff, catch-up, dedup) e quaisquer variaveis de ambiente; garantir suite total verde (CA-011).
- **Arquivos envolvidos:** `implementações/010 - Confirmacao Proativa Cron e Handoff/spec.md`, `implementações/010 - Confirmacao Proativa Cron e Handoff/tasks.md`.
- **Critério de conclusão:** documentacao consistente; CA-011 (suite total verde) confirmado.
- **Dependências:** T-012, T-013.
- **Estimativa:** Pequena

---

## Registro de Progresso

| Tarefa | Descrição | Finding | Status | Estimativa |
|---|---|---|---|---|
| T-001 | Mapear pontos + baseline | todos | [ ] | Pequena |
| T-002 | Tokenizacao por palavra na confirmacao | WE-08/CA-11 | [ ] | Média |
| T-003 | Conflito afirmativo + mudanca | WE-08/CA-11 | [ ] | Média |
| T-004 | Handoff com tratamento de negacao | WE-13 | [ ] | Média |
| T-005 | Extensao da janela de handoff | HO-02 | [ ] | Média |
| T-006 | Catch-up do cron no startup | CO-04 | [ ] | Grande |
| T-007 | Recuperar `processing` + isolar excecoes | CO-05 | [ ] | Grande |
| T-008 | Nao apagar conversa em andamento | CO-07 | [ ] | Pequena |
| T-009 | Dedup por (telefone, evento) | CO-06 | [ ] | Pequena |
| T-010 | Guard de loop por contagem | AG-07 | [ ] | Média |
| T-011 | Reconhecer `DENTISTA:` no historico | AG-10 | [ ] | Média |
| T-012 | Testes unitarios das heuristicas | varios | [ ] | Grande |
| T-013 | Testes de integracao do cron | CO-04/CO-05/CO-07 | [ ] | Grande |
| T-014 | Documentacao e progresso | todos | [ ] | Pequena |
