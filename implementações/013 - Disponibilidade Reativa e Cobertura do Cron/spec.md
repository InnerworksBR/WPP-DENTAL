# Disponibilidade Reativa e Cobertura do Cron

> **ID:** 013
> **Status:** 🟢 Concluída
> **Prioridade:** 🔴 Crítica
> **Criada em:** 2026-06-16
> **Última atualização:** 2026-06-16
> **Autor:** Agente AI

---

## 1. Resumo Executivo

Corrige três falhas reportadas em produção pela Dra. Priscila: (A) o bot "cisma" nos 2
primeiros horários e re-oferta os mesmos mesmo quando o paciente recusa; (B) pedidos de
horário/dia específico ("11:00", "dia 23 às 18:30") são rejeitados em vez de atendidos; e
(C) o cron de confirmação do dia seguinte não envia para parte dos pacientes porque extrai o
telefone do evento do calendário (que, quando criado manualmente, não tem telefone parseável).

## 2. Contexto e Motivação

### 2.1 Problema Atual
- **A — Oferta presa nos 2 primeiros slots:** `extract_request_constraints` só reconhece recusa
  via `_REJECTION_TOKENS` (`appointment_offer_service.py:102`). "Nenhum", "outro", "não gostei",
  "?" não estão na lista → `rejects_current_slot=False` → nada vai para `rejected_slots` → o LLM
  re-busca sem `exclude_slots` e retorna os mesmos 2 (`suggestions_count=2`).
- **B — Horário/dia específico rejeitado:** `_handle_offered_slot_selection` (`app.py:1388`) trata
  qualquer mensagem com horário como tentativa de escolher um slot ofertado; se não bate, dispara
  `_build_current_offer_message` ("não está entre as opções"), descartando o pedido real. O
  `earliest_time` só é capturado com "depois das/a partir das", então "11:00"/"18:30" avulsos
  são perdidos; e um dia explícito diferente ("dia 23") é ignorado.
- **C — Cron não cobre todos:** `find_patient_appointments_for_date` (`calendar_service.py:688`)
  pula silenciosamente o evento quando `_extract_patient_phone_from_event` retorna vazio. Eventos
  criados manualmente pela doutora frequentemente não têm telefone no título/descrição, então o
  paciente (cadastrado corretamente no sistema) fica sem lembrete e nada é logado.

### 2.2 Impacto do Problema
Pacientes não conseguem escolher horários reais (perda de agendamentos e transtorno), e parte
dos pacientes do dia seguinte não recebe confirmação (faltas e buracos na agenda).

### 2.3 Soluções Consideradas
| Solução | Prós | Contras | Decisão |
|---|---|---|---|
| Re-oferta determinística (não depender do LLM) | Robusto, previsível | Mais código | ✅ Escolhida |
| Só ajustar prompt do LLM | Menos código | LLM já demonstrou loop; não confiável | ❌ Descartada |
| Cron: casar paciente pelo nome no cadastro + logar pulados | Recupera eventos manuais | Nome ambíguo precisa de guarda | ✅ Escolhida |

## 3. Especificação Técnica

### 3.1 Visão Geral
Centraliza a busca de próximos horários em `CalendarService.find_next_available_slots`, reutilizada
pela tool do LLM e por uma re-oferta determinística no webhook. Amplia a captura de restrições
(recusa ampla, horário específico, dia específico). No cron, adiciona fallback de telefone por
nome no cadastro e logging dos pulados.

### 3.2 Componentes Afetados
| Componente | Ação | Descrição |
|---|---|---|
| `src/domain/policies/appointment_offer_service.py` | Modificar | Vocabulário de recusa ampliado; capturar `requested_time` e `requested_date` |
| `src/infrastructure/integrations/calendar_service.py` | Modificar | `find_next_available_slots`; fallback de telefone por nome + logging |
| `src/interfaces/http/app.py` | Modificar | Captura de novas restrições; re-oferta determinística (substitui o beco sem saída) |
| `src/interfaces/tools/calendar_tool.py` | Modificar | (opcional) reusar `find_next_available_slots` |
| `tests/test_reactive_availability_impl013.py` | Criar | Testes de regressão A/B/C |

### 3.3 Interfaces

#### `extract_request_constraints` (saída ampliada)
Novos campos em `AppointmentRequestConstraints`: `requested_time: str` (HH:MM) e
`requested_date: str` (DD/MM/YYYY).

#### `CalendarService.find_next_available_slots(...)`
Entrada: `start_date`, `period`, `earliest_time`, `exclude_dates`, `exclude_slots`,
`requested_time`, `limit`, `max_days`. Saída: `{"date_str": "DD/MM/YYYY", "times": [...]}` ou `None`.

### 3.6 Tratamento de Erros
Falha de calendário na re-oferta → mensagem segura e fallback ao LLM. Cron: evento sem telefone
e sem match único por nome → log de aviso e pula (sem afirmar envio).

## 4. Requisitos

### 4.1 Requisitos Funcionais
- **RF-001:** Recusa ampla ("nenhum", "outro", "não gostei", "mais opções") marca recusa e leva os
  slots ofertados a `rejected_slots`.
- **RF-002:** Ao recusar, o bot oferta os PRÓXIMOS horários (excluindo os recusados); se não houver
  no dia, avança para o próximo dia disponível respeitando as restrições.
- **RF-003:** Pedido de horário específico ("11:00", "às 18:30") busca aquele horário; se indisponível,
  oferta os próximos respeitando a preferência.
- **RF-004:** Pedido de dia específico diferente ("dia 23") busca naquele dia, não re-oferta o dia antigo.
- **RF-005:** O cron envia confirmação para todo paciente do dia seguinte cujo telefone seja
  resolvível pelo evento OU pelo cadastro (match único por nome).
- **RF-006:** Eventos pulados pelo cron (sem telefone resolvível) são logados (sem perda silenciosa).

### 4.2 Requisitos Não-Funcionais
- **RNF-001:** Sem regressão na suíte existente.
- **RNF-002:** Re-oferta determinística não depende do LLM.

## 5. Critérios de Aceitação
- [x] **CA-001:** "Nenhum" após oferta → novos horários diferentes (ou próximo dia), nunca os mesmos.
- [x] **CA-002:** "11:00" quando não ofertado → busca 11:00/próximos, não "não está entre as opções".
- [x] **CA-003:** "dia 23 às 18:30" → busca dia 23 a partir das 18:30, não re-oferta o dia antigo.
- [x] **CA-004:** Evento manual sem telefone mas com nome cadastrado único → cron envia.
- [x] **CA-005:** Evento sem telefone e sem match → log de aviso.
- [x] **CA-006:** Suíte 100% verde.

## 6. Plano de Testes
- Unitários: vocabulário de recusa; captura de `requested_time`/`requested_date`;
  `find_next_available_slots` (com mocks de eventos).
- Integração (webhook): recusa → nova oferta; horário específico → busca; dia específico → busca.
- Integração (cron): fallback por nome; log de pulado.

## 7. Riscos e Mitigações
| Risco | Prob. | Impacto | Mitigação |
|---|---|---|---|
| Match por nome ambíguo cancela/lembra paciente errado | Média | Alto | Só usar quando houver EXATAMENTE 1 cadastro com aquele nome normalizado |
| Falsos positivos de recusa (ex.: "não sei") | Baixa | Médio | Manter tokens conservadores e testes |
| Re-oferta entrar em loop | Baixa | Alto | Excluir sempre os já recusados; limite de dias |

## 8. Dependências
### 8.1 Internas
Impls 005, 007, 010 (oferta/seleção/cron já existentes).
### 8.2 Externas
Google Calendar API (já integrado).

## 9. Observações
A re-oferta determinística segue a filosofia do projeto (camada determinística como rede de
segurança sobre o LLM), igual a impls 005/006/007.
