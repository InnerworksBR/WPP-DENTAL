# Implementações — Programa de Recuperação do WPP-DENTAL

> **Índice do projeto (Spec-Driven Development).** Este arquivo é a fonte de verdade do
> status e da ordem das implementações. Atualize-o sempre que uma implementação mudar de status.
>
> **Criado em:** 2026-06-15 · **Última atualização:** 2026-06-23 (Fase 3 — correções 018–019 planejadas)

---

## 1. Contexto

O agente de agenda da Dra. Priscila (WhatsApp + Google Calendar + OpenAI) apresentava quatro
queixas do dono do produto: **(1)** a API "toda hora dá erro"; **(2)** responde errado aos
clientes; **(3)** foge do escopo (preço/clínico); **(4)** marca errado e traz transtorno.

Uma auditoria multi-agente da codebase (78 agentes, 106 findings verificados adversarialmente)
mapeou as causas-raiz e mediu o estado real da suíte: **67 testes quebrados / 114 passando** —
os 67 são `ModuleNotFoundError` de módulos removidos em refactors, ou seja, **não havia rede de
segurança viva** sobre o motor de produção (`CleanAgentService`). Isso explica a reincidência dos
bugs (commits `#0002`→`#0005` corrigindo a mesma família).

Os findings foram decompostos em **12 implementações coesas** (5–15 tarefas cada), seguindo a
metodologia Spec-Driven. A `000` preserva o trabalho de remarcação parcial já realizado.

---

## 2. Tabela de Decomposição

| # | Implementação | Status | Prioridade | Escopo (resumo) | Findings | ~Tarefas |
|---|---|---|---|---|---|---|
| 000 | Consistência de Remarcação Parcial | 🟢 Concluída (parcial) | 🟠 Alta | Remarcação não deixa evento antigo ativo; falha parcial não vira sucesso silencioso | — | 9 |
| 001 | Estabilidade da API e Resiliência de IO | 🟢 Concluída | 🔴 Crítica | LLM com timeout/retry, offload do event loop, SQLite/Google sem crash | AG-01, EVENT-LOOP, WE-10, CONNECTION, AG-06/CA-03, WH-07 | 12 |
| 002 | Recuperação da Rede de Testes | 🟢 Concluída | 🟠 Alta | Suíte verde; corrigir 67 testes mortos; cobrir o motor `CleanAgentService` | TE-01/02/03/05/07, EN-01/05 | 12 |
| 003 | Robustez do Estado Conversacional | 🟢 Concluída | 🔴 Crítica | Anti-crash de schema drift, TTL, reset limpo, handoff sem destruir contexto | CO-01/02/03/07/08, HO-01 | 13 |
| 004 | Identidade do Paciente e Normalização de Telefone | 🟢 Concluída | 🔴 Crítica | Telefone canônico (9º dígito), fim do paciente duplicado/trocado, cadastro não-destrutivo | PH-01..05, PA-01/02 | 12 |
| 005 | Cancelamento Seguro | 🟢 Concluída | 🔴 Crítica | Nunca declarar cancelamento sem confirmação real; não cancelar a consulta errada | WE-01, CO-04/02, CA-01, CA-06, CA-07 | 12 |
| 006 | Remarcação Atômica e Criação Idempotente | 🟢 Concluída | 🔴 Crítica | Remarcação consistente em todos os caminhos; criação idempotente (sem double-booking) | AG-02/CA-02, CA-05, WH-01 | 11 |
| 007 | Regras de Agenda e Disponibilidade | 🟢 Concluída | 🟠 Alta | 2 dias úteis como fonte única; oferta↔criação coerentes; fuso/virada de ano/feriados | WE-05, AG-03/04/08, CA-03/07/08/09/10, WE-11, CO-05 | 15 |
| 008 | Guarda de Escopo Robusto | 🟢 Concluída | 🟠 Alta | Bloquear preço/clínico (sem curto-circuito) sem expulsar paciente em fluxo legítimo | SC-01..06, AG-05, WE-07, CO-03 | 14 |
| 009 | Mensageria Confiável e Alertas | 🟢 Concluída | 🟠 Alta | Mensagem chega ao paciente e alerta chega à doutora; retry; eco vs resposta manual | WH-02/03/04/05/06/08/09, CO-01, WE-02/HO-03, WE-12 | 13 |
| 010 | Confirmação Proativa, Cron e Handoff | 🟢 Concluída | 🟡 Média | Heurística de confirmação/handoff robusta; cron com catch-up; janela de handoff | WE-08/CA-11, WE-13, HO-02, CO-04/05/06/07, AG-07/10 | 14 |
| 011 | Configuração Resiliente e Limpeza de Engine | 🟢 Concluída | 🟠 Alta | YAML/reload/env à prova de falha; remover engine "langgraph" fantasma | CO-02/03/06/07/09/10, EN-02/03/04 | 12 |
| 012 | Segurança do Webhook e Painel Admin | 🟢 Concluída | 🟠 Alta | Fechar webhook e painel admin; parar vazamento de segredos/PII em logs | WE-03, WE-09, CO-08, AD-01/02/03/04/06 | 14 |
| 013 | Disponibilidade Reativa e Cobertura do Cron | 🟢 Concluída | 🔴 Crítica | Recusa/horário/dia específico re-ofertam corretamente; cron cobre pacientes de eventos manuais | Reportes Dra. Priscila | 12 |

**Legenda de status:** 🟡 Planejada · 🔵 Em Andamento · 🟢 Concluída · 🔴 Bloqueada · ⚪ Cancelada.

---

## 2-B. Fase 2 — Refatoração do Núcleo de Conversa (014–017)

Concluído o **Programa de Recuperação** (000–013, suíte 488/488 verde), a Fase 2 ataca a
**causa estrutural** dos fluxos quebrados, não mais bugs pontuais: o sistema tem **dois cérebros
disputando a conversa** — o loop de tool-calls do `CleanAgentService` e uma máquina de estados
implícita de ~2.256 linhas no `app.py` (dezenas de `_handle_*`). Os dois dessincronizam, e a
verdade da oferta é reconstruída por **regex na prosa do LLM** (`_parse_offered_slots`).

A meta é inverter o comando: **o agendamento vira uma transação determinística** (máquina de
estados única e dona da verdade) e o **LLM fica contido** em dois papéis estreitos — entender a
intenção (NLU) e dar o tom. Transporte (Evolution) é isolado atrás de uma interface para deixar de
acoplar o orquestrador. Decisões: **manter Evolution** + **refactor cirúrgico** (não rewrite),
usando os 488 testes como catraca anti-regressão. Trabalho na branch `refactor/nucleo-conversa`.

| # | Implementação | Status | Prioridade | Escopo (resumo) | ~Tarefas |
|---|---|---|---|---|---|
| 014 | Gateway de Transporte | 🟢 Concluída | 🟠 Alta | Isola a Evolution atrás de `MessagingGateway` + `EvolutionAdapter`; transporte trocável; tira plumbing do `app.py` | 9 |
| 015 | NLU Estruturada | 🟢 Concluída | 🟠 Alta | `IntentClassifier` único: mensagem → `{intent, entities}` validado; consolida heurísticas dispersas | 8 |
| 016 | Orquestrador Determinístico | 🟢 Concluída (escopo seguro) | 🔴 Crítica | FSM dona das decisões estruturadas (nome/plano, cancelamento, seleção, re-oferta) em produção; criação/remarcação atômica mantida no handler provado por decisão de risco | 13 |
| 017 | Aposentar o Cérebro Duplo | ⚪ Reavaliada (não recomendada) | 🟠 Alta | Remoção total do LLM reavaliada como over-engineering: o "ofertar" do LLM já é guardado; manter o LLM para conversa aberta/tom. O cérebro duplo de DECISÃO já foi resolvido na 016 | — |

**Sequência:** 014 → 015 → 016 (escopo seguro concluído). 017 reavaliada — ver `016/spec.md` §9/§10.

> **Resultado da Fase 2:** o orquestrador determinístico (`src/application/flow/`) é a fonte única
> das decisões estruturadas de agenda, religado no `app.py` via deferimento incremental, com os
> handlers provados de criação/remarcação intactos. Transporte isolado (014), NLU consolidada (015).
> Suíte **542/542** verde na branch `refactor/nucleo-conversa`. Mergeada para `main` (commit `383f7c8`).

---

## 2-C. Fase 3 — Correção Definitiva dos Sintomas Remanescentes (018–019)

Decompõe a parte acionável de `docs/ANALISE_SOLUCAO_DEFINITIVA.md`. A análise mostrou que os três
sintomas relatados pelo dono não são bugs independentes, mas o efeito da **fronteira que sobrou** do
cérebro duplo (a oferta inicial ainda nascia em prosa do LLM, relida por regex) somado ao **descarte
silencioso** no cron de lembretes. A Fase 3 fecha essas duas frentes.

**Decisões do dono (2026-06-23):** **manter OpenAI** (sem migrar para Ollama/Qwen — Fase C do
documento descartada) e **manter Evolution API** (sem migrar para a Cloud API oficial — Fase D
descartada). O escopo fica nas duas correções determinísticas; nenhuma troca de transporte/LLM.

| # | Implementação | Status | Prioridade | Escopo (resumo) | Sintoma | ~Tarefas |
|---|---|---|---|---|---|---|
| 018 | Fronteira da Oferta Determinística | 🟢 Concluída (escopo seguro) | 🔴 Crítica | FSM gera a **oferta inicial** como dado estruturado (`try_initial_offer`), caminho primário; mensagem == estado; seleção casa contra `offered_times`. Regex/tools do LLM mantidos como fallback (remoção total = RF-003-B). Suíte **552** verde | (a) repete + (b) horário errado | 13 (10 feitas + 3 deferidas) |
| 019 | Lembretes Confiáveis e Cobertura Total | 🟢 Concluída | 🔴 Crítica | Acaba o descarte silencioso: cada não contatado é registrado (nome+motivo) em `reminder_coverage`; **relatório diário** à clínica (`enviados/pulados/falhas`); re-tentativa cross-run + surfacing; `GET /admin/api/coverage`. Suíte **562** verde | (c) não chega a todos | 13 |

**Sequência:** 018 ✅ → 019 ✅ → **Fase E ✅** (merge + deploy). Ambas concluídas em 2026-06-23,
suíte **562/562** verde, **mergeadas para `main` (fast-forward, commit `6feba98`) e push em
`origin/main`** → EasyPanel redeploya. **Fases C/D do documento (Ollama, WhatsApp oficial) NÃO
entram** por decisão do dono.

> **Mapa sintoma → implementação (Fase 3):** (a) "repete" → **018** · (b) "horário errado" → **018** ·
> (c) "lembrete não chega a todos" → **019**.

---

## 3. Ordem de Execução Recomendada

A ordem prioriza **estabilizar** (parar de dar erro), depois **restaurar a rede de testes**
(habilitador de verificação), depois **corretude crítica de agenda**, e por fim os eixos de
escopo, mensageria, configuração e segurança.

```
001  Estabilidade da API ───────────────┐ (base de tudo)
        │                                │
        ▼                                │
002  Rede de Testes  ◄───────────────────┘ (habilitador: valida todo o resto)
        │
        ├──► 003  Estado Conversacional ──┐
        │                                 │
        ├──► 004  Identidade/Telefone ────┤
        │                                 ▼
        │                            005  Cancelamento Seguro
        │                                 │
        │                                 ▼
        │                            006  Remarcação Atômica
        │
        ├──► 007  Regras de Agenda
        ├──► 008  Guarda de Escopo
        ├──► 009  Mensageria e Alertas
        ├──► 010  Confirmação/Cron/Handoff   (após 003, 004, 005)
        ├──► 011  Config Resiliente
        └──► 012  Segurança Webhook/Admin
```

**Sequência sugerida:** 001 → 002 → 003 → 004 → 005 → 006 → 007 → 008 → 009 → 010 → 011 → 012.

> As implementações 007, 008, 009, 011 e 012 dependem apenas de **001 + 002** e podem ser
> paralelizadas entre si após a base estar pronta. As críticas de agenda (003→006) e a 010
> formam uma cadeia que deve respeitar a ordem.

---

## 4. Grafo de Dependências

| # | Depende de |
|---|---|
| 000 | — (concluída) |
| 001 | — |
| 002 | 001 |
| 003 | 001, 002 |
| 004 | 001, 002 |
| 005 | 001, 002, 003, 004 |
| 006 | 001, 002, 003, 004, 005 (+ estratégia da 000) |
| 007 | 001, 002 |
| 008 | 001, 002 |
| 009 | 001, 002 |
| 010 | 002, 003, 004, 005 |
| 011 | 001, 002 |
| 012 | 001, 002 |
| 014 | baseline 000–013 verde |
| 015 | 014 |
| 016 | 014, 015 |
| 017 | 016 |
| 018 | 016 (reusa 007, 008, 009, 015; preserva 000/005/006) |
| 019 | 010, 013 (reusa 009, 012) |

---

## 5. Mapa Queixa → Implementação

| Queixa do dono | Implementações que atacam a raiz |
|---|---|
| (1) API toda hora dá erro | **001**, 003 (CO-01 crash), 009 (alertas), 011 (config/reload), 012 (AD-03) |
| (2) Responde errado | 003 (estado preso), 005 (falsa confirmação), 007 (seleção de horário), 010 (heurísticas), 011 (mensagem em branco) |
| (3) Foge do escopo | **008**, 009 (alerta à doutora chega), 011 (DOCTOR_PHONE) |
| (4) Marca errado / transtorno | **004, 005, 006**, 007 (regra 2 dias), 010 (cron/confirmação) |
| Segurança (eixo novo) | **012**, 009/011 (segredos), 002 (rede de testes como salvaguarda) |

---

## 6. Como usar

Cada pasta `NNN - Nome/` contém:

- **`spec.md`** — especificação Spec-Driven (contrato vivo): contexto, especificação técnica com
  `arquivo:linha` reais, requisitos (RF/RNF), critérios de aceitação, plano de testes, riscos e dependências.
- **`tasks.md`** — plano de execução derivado da spec (fases Preparação → Implementação → Testes →
  Documentação), com tabela de progresso.

**Para executar uma implementação:** leia `spec.md` + `tasks.md`, siga as tarefas na ordem
respeitando dependências, marque `[x]` ao concluir, atualize o contador de progresso e mude o
status no `spec.md` (e nesta tabela) ao finalizar.

> **Regra de ouro:** nenhuma correção de comportamento deve ser dada como concluída sem o
> respectivo **teste de regressão** verde (toda implementação inclui tarefas de teste para isso).
> Rodar a suíte com: `C:/Apps/WPP-DENTAL/.venv/Scripts/python.exe -m pytest -q`.
