# Regras de Agenda e Disponibilidade

> **ID:** 007
> **Status:** 🟡 Planejada
> **Prioridade:** 🟠 Alta
> **Criada em:** 2026-06-15
> **Última atualização:** 2026-06-15
> **Autor:** Agente AI

---

## 1. Resumo Executivo

Esta implementação centraliza e endurece as regras de disponibilidade definidas no PRD do WPP-DENTAL, elegendo `CalendarService.create_appointment_if_available` (`src/infrastructure/integrations/calendar_service.py:492-529`) como **fonte única de verdade** para validar se um agendamento é permitido.

Hoje as regras estão espalhadas e inconsistentes: a janela mínima de 2 dias úteis (`config.get_min_business_days_ahead`) só é aplicada em `FindNextAvailableDayTool` (`src/interfaces/tools/calendar_tool.py:265`), não na criação nem na busca por data específica; o guard `_is_offered_slot` (`src/application/services/clean_agent_service.py:52-72`) **libera** o agendamento quando o estado não tem oferta válida ou a data é malformada; paciente Particular fica preso porque `_has_valid_direct_plan` (`clean_agent_service.py:75-88`) exige um plano marcado como não-`referral`; a seleção de horário trata qualquer número solto como hora (`appointment_offer_service.py:247-250`); a virada de ano usa `datetime.now().year` (`appointment_offer_service.py:166,200`); a regra de 2 dias úteis ignora feriados; e eventos `cancelled` podem ser considerados como ocupados/futuros.

A implementação corrige WE-05/CA-02, AG-03, AG-04, AG-08, CA-07(offer), CA-08, CA-03(feriados), CA-10, CA-09, WE-11 e CO-05(state), atacando diretamente as 4 queixas do dono (API erra, responde errado, foge do escopo, marca errado).

## 2. Contexto e Motivação

### 2.1 Problema Atual

O motor de produção é o `CleanAgentService` (`src/application/services/clean_agent_service.py`, instanciado como `dental_crew` em `src/interfaces/http/app.py:114`), apoiado por um fluxo determinístico em `src/interfaces/http/app.py` (1554 linhas). As regras de agenda vivem em três camadas que não concordam entre si:

1. **`create_appointment_if_available` (calendar_service.py:492-529)** valida passado, fim de semana, granularidade de slot, horário comercial e `max_days_ahead` (linha 518) — **mas NÃO valida a janela mínima de 2 dias úteis**. Logo, "Tem horário amanhã?" pode ser agendado dentro da janela proibida (WE-05 / CA-02).

2. **`GetAvailableSlotsTool` (calendar_tool.py:149-208)** rejeita fim de semana (linha 175) mas também **não aplica o mínimo de dias úteis**. Apenas `FindNextAvailableDayTool` o aplica (`calendar_tool.py:265`).

3. **`_is_offered_slot` (clean_agent_service.py:52-72)** é o guard que impede agendar horário não ofertado. Porém:
   - Quando `state.offered_date`/`state.offered_times` estão vazios, retorna `True` (libera) — linhas 65-66 (AG-03).
   - Em `datetime` malformado, o `except ValueError` retorna `True` (libera) — linhas 71-72 (AG-03).
   - Compara `state.requested_weekday` (string `"0".."4"`) com `dt.weekday()` (int) via `str(dt.weekday()) != str(state.requested_weekday)`; embora haja `str()`, a fonte de `requested_weekday` é texto e `dt.weekday()` é int, criando fragilidade de tipos (AG-08).

4. **`_has_valid_direct_plan` (clean_agent_service.py:75-88)** exige um plano cujo `referral` seja `False`. Para um paciente **Particular** sem plano salvo, `config.get_plan_by_name("Particular")` pode não existir e a função retorna `False`, bloqueando `criar_agendamento` até estourar `_MAX_ITERATIONS` (`clean_agent_service.py:38`) — paciente preso (AG-04).

5. **`AppointmentOfferService._HOUR_ONLY_PATTERN` (appointment_offer_service.py:43)** usa `(?:\bas\b|\ba?s?\b|\b)\s*(\d{1,2})(?:h\b| horas?\b)?`, em que a alternância `\b` casa qualquer número solto. Em `resolve_selection` (linhas 247-250) isso seleciona um horário não pretendido a partir de um número qualquer da mensagem (CA-07).

6. **Virada de ano**: `extract_latest_offer` (linha 166) e `extract_latest_confirmation_request` (linha 200) completam datas `DD/MM` com `datetime.now().year`. Em 31/12, uma oferta para `02/01` recebe o ano corrente, gerando data no passado (CA-08).

7. **Feriados**: a contagem de dias úteis em `FindNextAvailableDayTool` (calendar_tool.py:277-280) só pula `target.weekday() >= 5` (fim de semana); feriados são tratados como dias úteis (CA-03).

8. **Eventos cancelados**: `find_appointments_by_phone` (calendar_service.py:595-627) e `get_available_slots` (linhas 371-441) não filtram `status == "cancelled"`. A API do Calendar com `singleEvents=True` pode retornar instâncias canceladas (CA-10).

9. **Fuso/DST**: `_normalize_datetime` (calendar_service.py:167-172) rotula datetime sem offset como Sao Paulo via `replace(tzinfo=SAO_PAULO_TZ)`, sem `astimezone`. Para eventos importados sem offset isso pode produzir hora errada na borda de horário de verão (CA-09).

10. **Reset frágil de contexto**: `reset_context_if_finished` (conversation_service.py:107-117) apaga **todo o histórico** quando a última mensagem da assistente casa um substring de `_TERMINAL_ASSISTANT_PATTERNS` (conversation_service.py:18-30). Substrings como `"estou a disposicao"` ou `"posso ajudar com mais alguma coisa"` aparecem em respostas que **não** encerram o atendimento, derrubando oferta/confirmação pendente (WE-11).

11. **Confirmação proativa órfã**: em `app.py` o branch de confirmação proativa (a partir de `app.py:928`) trata `pending_confirmation`. Quando o estado fica `pending`/`reschedule` sem `reschedule_event_id` e sem um branch que o resolva, a execução cai no LLM com estado órfão, gerando resposta incoerente (CO-05).

### 2.2 Impacto do Problema

- **Marca errado / transtorno (queixa 4):** WE-05 agenda dentro da janela proibida; AG-03 agenda horário nunca ofertado; CA-07 marca horário não pretendido; CA-08 marca data no passado na virada de ano.
- **API erra (queixa 1):** AG-04 prende o paciente até estourar iterações, retornando a mensagem genérica de erro interno.
- **Responde errado (queixa 2):** WE-11 e CO-05 produzem respostas incoerentes por perda/orfandade de estado; CA-10 mostra consultas canceladas como ativas.
- **Confiabilidade de horário:** CA-09 pode deslocar horários em DST/eventos importados.

### 2.3 Soluções Consideradas

| Solução | Prós | Contras | Decisão |
|---|---|---|---|
| Centralizar todas as regras em `create_appointment_if_available` como fonte única e replicar a validação de janela mínima na busca por data específica | Uma só fonte de verdade; correção atinge LLM e fluxo determinístico; alinhado ao PRD | Exige revisar guards a montante para falharem com mensagem clara | **Escolhida** |
| Endurecer apenas os guards do `CleanAgentService` (`_is_offered_slot`, `_has_valid_direct_plan`) | Mudança localizada | Não cobre o caminho do fluxo determinístico nem a busca por data específica; regra continua duplicada | Rejeitada |
| Criar um novo `AvailabilityPolicy` no domínio e reescrever as três camadas | Arquitetura mais limpa | Escopo grande, alto risco de regressão para correção de bugs urgentes | Rejeitada (registrar como melhoria futura) |
| Filtrar feriados via biblioteca externa (`holidays`) | Cobertura nacional automática | Dependência nova; feriados municipais variam; clínica precisa de controle manual | Parcial: usar lista configurável em `settings`, opcionalmente apoiada por lib |

## 3. Especificação Técnica

### 3.1 Visão Geral da Arquitetura

A regra de disponibilidade passa a ter **uma fonte de verdade na criação** (`CalendarService.create_appointment_if_available`) e um **espelho consistente na busca** (`GetAvailableSlotsTool`). Os guards do `CleanAgentService` passam a **negar por padrão** (fail-closed). `AppointmentOfferService` deixa de inferir hora a partir de número solto e passa a ancorar o ano da oferta/confirmação na data de referência da conversa, não em `datetime.now()`. `ConversationService.reset_context_if_finished` deixa de derrubar estado de agenda pendente.

### 3.2 Componentes Afetados

| Componente | Tipo | Ação | Descrição |
|---|---|---|---|
| `calendar_service.py::create_appointment_if_available` (492-529) | Função | Modificar | Adicionar validação de mínimo de dias úteis (fonte única); considerar feriados; ignorar eventos `cancelled` em conflito |
| `calendar_service.py::get_available_slots` (371-441) | Função | Modificar | Ignorar eventos `status=cancelled` ao montar `busy_intervals`; respeitar feriado |
| `calendar_service.py::find_appointments_by_phone` (595-627) | Função | Modificar | Filtrar `status == "cancelled"` |
| `calendar_service.py::_normalize_datetime` (167-172) | Função | Modificar | Tratar datetime sem offset de eventos importados de forma segura para DST |
| `calendar_tool.py::GetAvailableSlotsTool._run` (162-208) | Método | Modificar | Aplicar mínimo de dias úteis ao resolver data específica |
| `calendar_tool.py::FindNextAvailableDayTool._run` (252-321) | Método | Modificar | Pular feriados na contagem de dias úteis |
| `appointment_offer_service.py::_HOUR_ONLY_PATTERN` (43) e `resolve_selection` (247-250) | Regex/Método | Modificar | Exigir contexto de hora (`h`/`horas`/`as`) antes de aceitar número como horário |
| `appointment_offer_service.py::extract_latest_offer` (166) e `extract_latest_confirmation_request` (200) | Método | Modificar | Resolver ano com base na data de referência da conversa, não `datetime.now().year` |
| `clean_agent_service.py::_is_offered_slot` (52-72) | Função | Modificar | Negar por padrão quando não há oferta válida ou data malformada; comparar weekday por tipo consistente |
| `clean_agent_service.py::_has_valid_direct_plan` (75-88) | Função | Modificar | Aceitar "Particular" como plano válido |
| `conversation_service.py::reset_context_if_finished` (107-117) | Função | Modificar | Não limpar estado de agenda pendente; reset por sinal explícito |
| `app.py` (a partir de 928, CO-05) | Fluxo | Modificar | Garantir branch determinístico para confirmação proativa com estado `pending`/`reschedule` órfão |
| `config_service.py` (310-328) | Config | Adicionar | Chave `scheduling.holidays` (lista `DD/MM` ou `DD/MM/YYYY`) |

### 3.3 Interfaces e Contratos

- **`create_appointment_if_available(patient_name, patient_phone, start_time) -> dict`**: contrato mantido. Passa a lançar `ValueError` adicional quando `start_time` viola o mínimo de dias úteis ou cai em feriado. Mensagem sugerida: `"O agendamento só pode ser feito a partir de N dias úteis."`
- **`GetAvailableSlotsTool._run(date, period, earliest_time, exclude_dates, exclude_slots) -> str`**: contrato mantido. Quando `date` específico cair antes do mínimo de dias úteis, retorna mensagem de erro informativa (mesma natureza da linha 176-179).
- **`_is_offered_slot(datetime_str, state) -> bool`**: contrato mantido (bool). Semântica invertida nos casos de borda: agora retorna `False` (negar) quando não há oferta válida ou a data é malformada.
- **`_has_valid_direct_plan(patient_phone, state, config) -> bool`**: contrato mantido; "Particular" passa a ser aceito como plano direto válido.
- **`reset_context_if_finished(phone) -> bool`**: contrato mantido; passa a não limpar quando há `state.pending_slot_date`/`pending_slot_time` ou `state.intent == "reschedule"` pendente.

### 3.4 Modelos de Dados

- **Estado de conversa (`ConversationStateService`)**: usa `offered_date`, `offered_times`, `requested_weekday`, `earliest_time`, `excluded_dates`, `rejected_slots`, `pending_slot_date`, `pending_slot_time`, `intent`, `reschedule_event_id`, `stage`. Nenhum campo novo obrigatório. A data de referência para resolver ano será derivada do estado/histórico existente.
- **Settings (`config/settings`)**: nova chave opcional `scheduling.holidays: ["25/12", "01/01/2027", ...]`. Default `[]` (sem feriados) para manter compatibilidade. Acessada por novo getter `ConfigService.get_holidays() -> list[str]`.
- **Eventos Google Calendar**: passa-se a inspecionar `event.get("status")` para descartar `"cancelled"`.

### 3.5 Fluxo de Execução

1. LLM/fluxo determinístico oferta horários via `GetAvailableSlotsTool`/`FindNextAvailableDayTool`; ambos respeitam mínimo de dias úteis e pulam feriados.
2. Slots ofertados são gravados em `state.offered_date`/`offered_times` (clean_agent_service.py:359-365).
3. Ao chamar `criar_agendamento`, `_is_offered_slot` (clean_agent_service.py:321) **nega** se a data não estiver entre os horários ofertados, se o estado não tiver oferta válida, ou se a string for malformada.
4. `_has_valid_direct_plan` (clean_agent_service.py:339) aceita "Particular".
5. `CreateAppointmentTool._run` (calendar_tool.py:344-363) chama `create_appointment_if_available`, que aplica **todas** as regras (passado, fim de semana, granularidade, horário comercial, `max_days_ahead`, **mínimo de dias úteis**, **feriado**) e verifica conflito ignorando eventos cancelados.
6. Confirmação proativa em `app.py` (>=928) trata estado `pending`/`reschedule` com branch determinístico, sem cair no LLM órfão.

### 3.6 Tratamento de Erros

- Violação de janela mínima / feriado: `ValueError` em `create_appointment_if_available`, convertida em `"Erro: ..."` por `CreateAppointmentTool._run` (calendar_tool.py:353-354) e devolvida ao paciente como recusa educada com nova oferta.
- Data malformada no guard: `_is_offered_slot` retorna `False` (nega), e o loop devolve a mensagem de erro interno já existente (clean_agent_service.py:326-329) sem agendar.
- Falha de API do Calendar: mantém o comportamento atual de captura em `clean_agent_service.py:355-357` e nos `try/except` do fluxo determinístico.
- Feriado configurado inválido (`DD/MM` malformado): `get_holidays()` ignora a entrada inválida e loga aviso, sem quebrar o agendamento.

## 4. Requisitos

### 4.1 Requisitos Funcionais

- **RF-001 (WE-05/CA-02):** `create_appointment_if_available` deve rejeitar `start_time` cuja data seja anterior ao mínimo de dias úteis (`config.get_min_business_days_ahead`), contados a partir de "agora" em Sao Paulo, pulando fins de semana e feriados.
- **RF-002 (WE-05):** `GetAvailableSlotsTool._run` deve rejeitar (ou não sugerir) horários para uma data específica anterior ao mínimo de dias úteis, com mensagem informativa.
- **RF-003 (AG-03):** `_is_offered_slot` deve retornar `False` quando `state.offered_date` ou `state.offered_times` estiverem vazios.
- **RF-004 (AG-03):** `_is_offered_slot` deve retornar `False` quando `datetime_str` for malformado (atual `except ValueError` retorna `True`).
- **RF-005 (AG-04):** `_has_valid_direct_plan` deve aceitar "Particular" (e variações normalizadas) como plano direto válido.
- **RF-006 (AG-08):** A comparação de dia da semana em `_is_offered_slot` deve usar tipos consistentes (int x int) entre `dt.weekday()` e `state.requested_weekday`.
- **RF-007 (CA-07):** `resolve_selection` só deve interpretar um número como horário quando houver contexto explícito de hora (`h`, `horas`, `as HH`), nunca para um número solto qualquer.
- **RF-008 (CA-08):** A resolução do ano em `extract_latest_offer` e `extract_latest_confirmation_request` deve usar a data de referência da conversa, garantindo correção na virada de ano (ex.: oferta `02/01` feita em 31/12 cai no ano seguinte).
- **RF-009 (CA-03):** A contagem de dias úteis (criação e busca) deve pular feriados configurados, além de fins de semana.
- **RF-010 (CA-10):** `find_appointments_by_phone` e `get_available_slots` devem ignorar eventos com `status == "cancelled"`.
- **RF-011 (CA-09):** `_normalize_datetime` deve tratar datetime sem offset de eventos importados de forma segura, evitando deslocamento em DST.
- **RF-012 (WE-11):** `reset_context_if_finished` não deve limpar histórico/estado quando houver oferta/confirmação de agenda pendente; o reset deve depender de sinal terminal explícito e não de substrings ambíguos.
- **RF-013 (CO-05):** A confirmação proativa em `app.py` deve ter branch determinístico para estado `pending`/`reschedule` órfão, evitando cair no LLM sem contexto.

### 4.2 Requisitos Não-Funcionais

- **RNF-001:** Determinismo — as validações de janela, feriado e weekday devem ser determinísticas (sem LLM) e cobertas por testes unitários.
- **RNF-002:** Compatibilidade — `scheduling.holidays` default `[]` mantém comportamento atual quando não configurado.
- **RNF-003:** Fonte única — a regra de mínimo de dias úteis deve estar implementada de modo que a criação seja sempre a barreira final, mesmo que um guard a montante falhe.
- **RNF-004:** Idempotência/segurança — manter o `_APPOINTMENT_CREATION_LOCK` (calendar_service.py:524) e a verificação de conflito sob lock.
- **RNF-005:** Observabilidade — manter/ampliar logs de recusa (ex.: clean_agent_service.py:322-325) para incluir motivo (janela, feriado, sem oferta).

### 4.3 Restrições

- Escopo exclusivo de agenda; nenhuma lógica de preço/clínico (PROIBIDO pelo PRD).
- Slots de 15 min; sugerir 2 horários; seg-sex; só a partir de 2 dias úteis; marcar somente em horário ofertado e disponível.
- Bloqueios do Calendar invioláveis (inclui dia inteiro) — manter `return []`/`return True` para eventos all-day (calendar_service.py:412-413, 456-457).
- Convênios `referral` nunca agendados (manter `referral` bloqueante em `_has_valid_direct_plan`).
- Não introduzir dependências pesadas sem necessidade (feriados via config primeiro).

## 5. Critérios de Aceitação

- [ ] **CA-001 (RF-001):** Dado "agora" em dia útil, quando solicitar `create_appointment_if_available` para uma data anterior ao mínimo de dias úteis, então lança `ValueError` e não cria evento.
- [ ] **CA-002 (RF-001):** "Tem horário amanhã?" não resulta em agendamento dentro da janela proibida em nenhum caminho (LLM ou determinístico).
- [ ] **CA-003 (RF-002):** `GetAvailableSlotsTool._run` para data específica dentro da janela proibida retorna mensagem informativa e nenhum slot.
- [ ] **CA-004 (RF-003):** `_is_offered_slot` retorna `False` quando `offered_date`/`offered_times` vazios.
- [ ] **CA-005 (RF-004):** `_is_offered_slot` retorna `False` para `datetime_str` malformado.
- [ ] **CA-006 (RF-005):** Paciente "Particular" consegue concluir `criar_agendamento` sem estourar `_MAX_ITERATIONS`.
- [ ] **CA-007 (RF-006):** `_is_offered_slot` compara weekday por tipo consistente; não rejeita falsamente por diferença int/str.
- [ ] **CA-008 (RF-007):** Mensagem com número solto (ex.: "são 2 pessoas") não seleciona horário em `resolve_selection`.
- [ ] **CA-009 (RF-008):** Oferta `02/01` feita em 31/12 resolve para o ano seguinte (data futura).
- [ ] **CA-010 (RF-009):** Contagem de dias úteis pula feriado configurado em criação e busca.
- [ ] **CA-011 (RF-010):** Evento `status=cancelled` não aparece em `find_appointments_by_phone` nem bloqueia slot em `get_available_slots`.
- [ ] **CA-012 (RF-011):** `_normalize_datetime` não desloca hora para datetime sem offset em borda de DST.
- [ ] **CA-013 (RF-012):** Com oferta/confirmação pendente, `reset_context_if_finished` retorna `False` e preserva o estado.
- [ ] **CA-014 (RF-013):** Confirmação proativa com estado `reschedule` sem `reschedule_event_id` segue branch determinístico (não cai no LLM órfão).

## 6. Plano de Testes

### 6.1 Unitários

- `_is_offered_slot`: oferta vazia → `False`; data malformada → `False`; weekday int/str consistente; slot ofertado válido → `True`.
- `_has_valid_direct_plan`: "Particular" → `True`; plano `referral` → `False`; vazio → `False`.
- `AppointmentOfferService.resolve_selection`: número solto não seleciona; "as 9" / "9h" seleciona se ofertado; data divergente → `None`.
- `extract_latest_offer`/`extract_latest_confirmation_request`: virada de ano resolve ano corretamente.
- `ConfigService.get_holidays`: parsing válido/inválido; default `[]`.
- `_normalize_datetime`: com offset, sem offset (importado) e borda de DST.

### 6.2 Integração

- `create_appointment_if_available`: janela mínima de dias úteis com e sem feriado; conflito ignorando `cancelled`; manutenção das validações existentes (passado, fim de semana, granularidade, horário comercial, `max_days_ahead`).
- `GetAvailableSlotsTool._run`: data específica dentro/fora da janela; feriado.
- `find_appointments_by_phone`: lista com evento `cancelled` filtrado.
- `reset_context_if_finished`: com/sem estado pendente.

### 6.3 Aceitação

- Cenário end-to-end via fluxo determinístico (`app.py`) e via `CleanAgentService`: "Tem horário amanhã?" → assistente oferta a partir de 2 dias úteis e não agenda na janela proibida.
- Paciente Particular: fluxo completo de oferta → confirmação → agendamento.
- Virada de ano simulada (31/12) com oferta para `02/01`.

### 6.4 Casos de Borda

- Mínimo de dias úteis caindo sobre feriado (segunda feriado → próximo dia útil).
- Oferta com 1 único horário + "sim" (resolve_selection linha 234-237) ainda funciona.
- Evento all-day de bloqueio mantém `get_available_slots` retornando `[]` (calendar_service.py:412-413).
- DST: 1º domingo de novembro / 3º domingo de fevereiro (fuso Sao Paulo histórico) para datetime sem offset.
- Mensagem ambígua "dia 2 às 14" com contexto de hora explícito → seleciona; "dia 2, 2 pessoas" → não seleciona.

## 7. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Endurecer `_is_offered_slot` para fail-closed bloquear agendamentos legítimos | Média | Alto | Garantir que slots ofertados sempre sejam gravados (clean_agent_service.py:359-365) antes do `criar_agendamento`; cobrir com testes de fluxo feliz |
| Lista de feriados incompleta causar oferta em feriado | Média | Médio | Tornar `scheduling.holidays` configurável e documentar manutenção; opcionalmente apoiar com lib nacional |
| Mudança em `_normalize_datetime` regredir eventos com offset | Baixa | Alto | Preservar caminho `astimezone` para datetime com offset; testes para ambos os casos |
| Resolução de ano por data de referência usar referência errada | Média | Médio | Derivar referência de forma determinística (data atual SP como fallback) e testar virada de ano |
| Filtro de `cancelled` esconder evento válido por status ausente | Baixa | Médio | Filtrar apenas `status == "cancelled"` explícito; status ausente é tratado como ativo |
| Reset de contexto preservar estado obsoleto demais | Baixa | Médio | Preservar somente quando há agenda pendente; manter reset por sinal terminal claro |

## 8. Dependências

### 8.1 Internas

- **Implementação 001** (pré-requisito): base de configuração/serviços de calendário e estado. Esta implementação assume os getters de `ConfigService` (`get_min_business_days_ahead`, `get_max_days_ahead`, `get_slot_duration`, `get_suggestions_count`) já estáveis.
- **Implementação 002 — Recuperação da Rede de Testes** (pré-requisito): suíte verde para validar as regras de agenda sem regressão (cobertura de `AppointmentOfferService` e regras de calendário).

### 8.2 Externas

- Google Calendar API (`googleapiclient`) — campos `status`, `start.dateTime`/`start.date`.
- `zoneinfo.ZoneInfo("America/Sao_Paulo")` (calendar_service.py:20) para fuso/DST.
- (Opcional) biblioteca `holidays` se a clínica optar por feriados nacionais automáticos — não obrigatória nesta implementação.

## 9. Observações e Decisões de Design

- **Fonte única na criação:** mesmo aplicando a janela mínima na busca (RF-002), a barreira definitiva é `create_appointment_if_available` (RF-001). Isso garante que qualquer caminho (LLM alucinando data, fluxo determinístico, chamada direta da tool) seja barrado no mesmo ponto.
- **Fail-closed nos guards:** inverter o default de `_is_offered_slot` (negar quando em dúvida) está alinhado à regra do PRD "na dúvida, escalar / não agendar". É a mudança de maior impacto contra "marca errado".
- **Feriados configuráveis primeiro:** preferiu-se `scheduling.holidays` em settings a uma dependência externa, dando controle à clínica e mantendo o sistema leve; a lib nacional fica como evolução.
- **AG-08 já tem `str()` defensivo, mas a fragilidade persiste** porque a semântica mistura origem textual e `int`; padroniza-se a comparação para int x int para eliminar ambiguidade futura.
- **N/A — alterações de schema de banco:** não há mudança de schema; o estado de conversa já possui os campos necessários.
