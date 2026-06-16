# Tarefas: Regras de Agenda e Disponibilidade

> **Implementação:** 007 - Regras de Agenda e Disponibilidade
> **Spec:** [spec.md](./spec.md)
> **Progresso:** 15/15 tarefas concluídas (100%)
> **Última atualização:** 2026-06-16

Legenda: [ ] Pendente, [x] Concluída, [!] Bloqueada, [-] Cancelada.

---

## Fase 1 — Preparação

### [x] T-001 — Mapear regras de disponibilidade e ponto de fonte única
- **Descrição:** Confirmar, com base no código lido, todas as validações já existentes em `create_appointment_if_available` (passado, fim de semana, granularidade, horário comercial, `max_days_ahead`) e listar onde a janela mínima de dias úteis e feriados precisam entrar. Definir a referência temporal usada para resolver ano (CA-08).
- **Arquivos envolvidos:** `src/infrastructure/integrations/calendar_service.py` (492-529, 371-441), `src/interfaces/tools/calendar_tool.py` (252-321), `src/infrastructure/config/config_service.py` (310-328).
- **Critério de conclusão:** Documento curto de mapeamento (na PR) confirmando arquivo:linha de cada regra e o plano de fonte única.
- **Dependências:** Implementações 001 e 002.
- **Estimativa:** Pequena.

### [x] T-002 — Adicionar configuração de feriados
- **Descrição:** Criar `scheduling.holidays` em settings (default `[]`) e o getter `ConfigService.get_holidays()` com parsing de `DD/MM` e `DD/MM/YYYY`, ignorando entradas inválidas com log de aviso.
- **Arquivos envolvidos:** `src/infrastructure/config/config_service.py` (próximo a 322-328), arquivo de settings/config.
- **Critério de conclusão:** `get_holidays()` retorna lista normalizada; entrada inválida não quebra; default `[]`.
- **Dependências:** T-001.
- **Estimativa:** Pequena.

---

## Fase 2 — Implementação

### [x] T-003 — Aplicar mínimo de dias úteis na criação (fonte única) — WE-05/CA-02
- **Descrição:** Em `create_appointment_if_available`, validar que `start_sp.date()` respeita `config.get_min_business_days_ahead()` contado a partir de `now_sp`, pulando fins de semana e feriados; lançar `ValueError` claro caso contrário.
- **Arquivos envolvidos:** `src/infrastructure/integrations/calendar_service.py` (492-529).
- **Critério de conclusão:** Data dentro da janela proibida não cria evento e lança `ValueError`; demais validações intactas.
- **Dependências:** T-002.
- **Estimativa:** Média.

### [x] T-004 — Aplicar mínimo de dias úteis na busca por data específica — WE-05
- **Descrição:** Em `GetAvailableSlotsTool._run`, rejeitar/não sugerir horários quando a data resolvida for anterior ao mínimo de dias úteis, retornando mensagem informativa (mesmo estilo das linhas 176-179).
- **Arquivos envolvidos:** `src/interfaces/tools/calendar_tool.py` (162-208).
- **Critério de conclusão:** Data específica dentro da janela proibida retorna mensagem e nenhum slot.
- **Dependências:** T-002.
- **Estimativa:** Pequena.

### [x] T-005 — Endurecer `_is_offered_slot` (fail-closed) — AG-03 + AG-08
- **Descrição:** Inverter o default para negar quando `offered_date`/`offered_times` vazios (linhas 65-66) e quando ocorre `ValueError` por data malformada (linhas 71-72). Padronizar a comparação de weekday para int x int (linha 63).
- **Arquivos envolvidos:** `src/application/services/clean_agent_service.py` (52-72).
- **Critério de conclusão:** Função retorna `False` nos casos de borda; weekday comparado por tipo consistente; slot ofertado válido ainda retorna `True`.
- **Dependências:** T-001.
- **Estimativa:** Média.

### [x] T-006 — Aceitar "Particular" em `_has_valid_direct_plan` — AG-04
- **Descrição:** Tratar "Particular" (e variações normalizadas) como plano direto válido, sem depender de `config.get_plan_by_name`/`find_plan_fuzzy` retornar um plano não-`referral`.
- **Arquivos envolvidos:** `src/application/services/clean_agent_service.py` (75-88); checar consistência com `_resolve_valid_plan_name` em `src/interfaces/http/app.py` (515-536).
- **Critério de conclusão:** Paciente Particular conclui `criar_agendamento` sem estourar `_MAX_ITERATIONS`; plano `referral` continua bloqueado.
- **Dependências:** T-001.
- **Estimativa:** Média.

### [x] T-007 — Exigir contexto de hora em `resolve_selection` — CA-07
- **Descrição:** Ajustar `_HOUR_ONLY_PATTERN` (linha 43) e o uso em `resolve_selection` (247-250) para só interpretar número como hora com contexto explícito (`as`, `h`, `horas`), nunca para número solto.
- **Arquivos envolvidos:** `src/domain/policies/appointment_offer_service.py` (43, 247-250).
- **Critério de conclusão:** Número solto não seleciona horário; "9h"/"as 9" seleciona se ofertado.
- **Dependências:** T-001.
- **Estimativa:** Média.

### [x] T-008 — Resolver ano por data de referência (virada de ano) — CA-08
- **Descrição:** Substituir `datetime.now().year` (linhas 166 e 200) por resolução baseada na data de referência da conversa, garantindo que ofertas `DD/MM` na virada de ano caiam no ano correto (futuro).
- **Arquivos envolvidos:** `src/domain/policies/appointment_offer_service.py` (146-208).
- **Critério de conclusão:** Oferta `02/01` em 31/12 resolve para o ano seguinte; comportamento normal preservado fora da virada.
- **Dependências:** T-001.
- **Estimativa:** Média.

### [x] T-009 — Considerar feriados na contagem de dias úteis — CA-03
- **Descrição:** Fazer a contagem de dias úteis (busca e criação) pular feriados de `get_holidays()`, além de fins de semana.
- **Arquivos envolvidos:** `src/interfaces/tools/calendar_tool.py` (277-280, 282-284), `src/infrastructure/integrations/calendar_service.py` (validação de T-003).
- **Critério de conclusão:** Dia útil dentro da janela que seja feriado é pulado em busca e criação.
- **Dependências:** T-002, T-003.
- **Estimativa:** Média.

### [x] T-010 — Ignorar eventos `cancelled` — CA-10
- **Descrição:** Filtrar `event.get("status") == "cancelled"` em `find_appointments_by_phone` (595-627) e ao montar `busy_intervals` em `get_available_slots` (401-411).
- **Arquivos envolvidos:** `src/infrastructure/integrations/calendar_service.py` (371-441, 595-627).
- **Critério de conclusão:** Evento cancelado não aparece nas consultas futuras nem bloqueia slot; status ausente tratado como ativo.
- **Dependências:** T-001.
- **Estimativa:** Pequena.

### [x] T-011 — Tratar datetime sem offset com segurança em DST — CA-09
- **Descrição:** Ajustar `_normalize_datetime` (167-172) para tratar datetime de eventos importados sem offset de forma segura, sem deslocamento em borda de DST; preservar o caminho `astimezone` para datetime com offset.
- **Arquivos envolvidos:** `src/infrastructure/integrations/calendar_service.py` (167-172).
- **Critério de conclusão:** Datetime com offset inalterado; sem offset não desloca hora em borda de DST.
- **Dependências:** T-001.
- **Estimativa:** Média.

### [x] T-012 — Preservar estado de agenda no reset de contexto — WE-11
- **Descrição:** Em `reset_context_if_finished`, não limpar histórico/estado quando houver oferta/confirmação pendente (`pending_slot_date`/`pending_slot_time` ou `intent == "reschedule"`); endurecer a detecção terminal para não disparar por substrings ambíguos de `_TERMINAL_ASSISTANT_PATTERNS` (18-30).
- **Arquivos envolvidos:** `src/application/services/conversation_service.py` (18-30, 107-117); chamada em `src/interfaces/http/app.py` (201).
- **Critério de conclusão:** Com agenda pendente, retorna `False` e preserva o estado; reset por sinal terminal claro mantém-se.
- **Dependências:** T-001.
- **Estimativa:** Média.

### [x] T-013 — Branch determinístico para confirmação proativa órfã — CO-05
- **Descrição:** Garantir que a confirmação proativa com estado `pending`/`reschedule` sem `reschedule_event_id` siga um branch determinístico (não caia no LLM órfão), reaproveitando `_build_reschedule_missing_original_message` e o tratamento existente.
- **Arquivos envolvidos:** `src/interfaces/http/app.py` (a partir de 928; 986-1009).
- **Critério de conclusão:** Estado `reschedule` órfão produz resposta determinística coerente, sem fallback para o LLM.
- **Dependências:** T-006.
- **Estimativa:** Média.

---

## Fase 3 — Testes

### [x] T-014 — Suíte de testes de regressão das regras de agenda
- **Descrição:** Cobrir com testes automatizados cada finding: WE-05/CA-02 (janela mínima em criação e busca), AG-03 e AG-08 (`_is_offered_slot` fail-closed e weekday), AG-04 (Particular), CA-07 (número solto), CA-08 (virada de ano), CA-03 (feriado), CA-10 (cancelled), CA-09 (DST), WE-11 (reset preservando estado), CO-05 (branch determinístico). Incluir casos de borda da spec (oferta única + "sim", evento all-day mantendo `[]`).
- **Arquivos envolvidos:** `tests/test_agenda_rules.py` (36 testes), `tests/conftest.py` (resolução de importação circular).
- **Critério de conclusão:** Todos os critérios CA-001..CA-014 da spec verdes; testes falham contra o código antigo e passam contra o corrigido.
- **Dependências:** T-003 a T-013.
- **Estimativa:** Grande.

---

## Fase 4 — Documentação

### [x] T-015 — Atualizar documentação e checklist de configuração
- **Descrição:** Documentar a nova chave `scheduling.holidays`, a regra de fonte única (criação como barreira final) e o comportamento fail-closed dos guards; atualizar o progresso desta implementação.
- **Arquivos envolvidos:** `implementações/007 - Regras de Agenda e Disponibilidade/spec.md`, `implementações/README.md`.
- **Critério de conclusão:** Documentação reflete o comportamento implementado; tabela de progresso atualizada.
- **Dependências:** T-014.
- **Estimativa:** Pequena.

---

## Registro de Progresso

| Tarefa | Descrição | Findings | Fase | Estimativa | Status | Concluída |
|---|---|---|---|---|---|---|
| T-001 | Mapear regras e fonte única | Todos | Preparação | Pequena | [x] | 2026-06-16 |
| T-002 | Configuração de feriados | CA-03 | Preparação | Pequena | [x] | 2026-06-16 |
| T-003 | Mínimo de dias úteis na criação | WE-05/CA-02 | Implementação | Média | [x] | 2026-06-16 |
| T-004 | Mínimo de dias úteis na busca | WE-05 | Implementação | Pequena | [x] | 2026-06-16 |
| T-005 | `_is_offered_slot` fail-closed + weekday | AG-03, AG-08 | Implementação | Média | [x] | 2026-06-16 |
| T-006 | Aceitar Particular | AG-04 | Implementação | Média | [x] | 2026-06-16 |
| T-007 | Contexto de hora em resolve_selection | CA-07 | Implementação | Média | [x] | 2026-06-16 |
| T-008 | Ano por data de referência | CA-08 | Implementação | Média | [x] | 2026-06-16 |
| T-009 | Feriados na contagem de dias úteis | CA-03 | Implementação | Média | [x] | 2026-06-16 |
| T-010 | Ignorar eventos cancelled | CA-10 | Implementação | Pequena | [x] | 2026-06-16 |
| T-011 | DST em datetime sem offset | CA-09 | Implementação | Média | [x] | 2026-06-16 |
| T-012 | Preservar estado no reset | WE-11 | Implementação | Média | [x] | 2026-06-16 |
| T-013 | Branch confirmação proativa órfã | CO-05 | Implementação | Média | [x] | 2026-06-16 |
| T-014 | Testes de regressão | Todos | Testes | Grande | [x] | 2026-06-16 |
| T-015 | Documentação e config | CA-03 + geral | Documentação | Pequena | [x] | 2026-06-16 |

> Total: 15 tarefas concluídas.
