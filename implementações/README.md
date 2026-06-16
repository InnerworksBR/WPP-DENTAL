# Implementações — Programa de Recuperação do WPP-DENTAL

> **Índice do projeto (Spec-Driven Development).** Este arquivo é a fonte de verdade do
> status e da ordem das implementações. Atualize-o sempre que uma implementação mudar de status.
>
> **Criado em:** 2026-06-15 · **Última atualização:** 2026-06-15

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
| 005 | Cancelamento Seguro | 🟡 Planejada | 🔴 Crítica | Nunca declarar cancelamento sem confirmação real; não cancelar a consulta errada | WE-01, CO-04/02, CA-01, CA-06, CA-07 | 12 |
| 006 | Remarcação Atômica e Criação Idempotente | 🟡 Planejada | 🔴 Crítica | Remarcação consistente em todos os caminhos; criação idempotente (sem double-booking) | AG-02/CA-02, CA-05, WH-01 | 11 |
| 007 | Regras de Agenda e Disponibilidade | 🟡 Planejada | 🟠 Alta | 2 dias úteis como fonte única; oferta↔criação coerentes; fuso/virada de ano/feriados | WE-05, AG-03/04/08, CA-03/07/08/09/10, WE-11, CO-05 | 15 |
| 008 | Guarda de Escopo Robusto | 🟡 Planejada | 🟠 Alta | Bloquear preço/clínico (sem curto-circuito) sem expulsar paciente em fluxo legítimo | SC-01..06, AG-05, WE-07, CO-03 | 14 |
| 009 | Mensageria Confiável e Alertas | 🟡 Planejada | 🟠 Alta | Mensagem chega ao paciente e alerta chega à doutora; retry; eco vs resposta manual | WH-02/03/04/05/06/08/09, CO-01, WE-02/HO-03, WE-12 | 13 |
| 010 | Confirmação Proativa, Cron e Handoff | 🟡 Planejada | 🟡 Média | Heurística de confirmação/handoff robusta; cron com catch-up; janela de handoff | WE-08/CA-11, WE-13, HO-02, CO-04/05/06/07, AG-07/10 | 14 |
| 011 | Configuração Resiliente e Limpeza de Engine | 🟡 Planejada | 🟠 Alta | YAML/reload/env à prova de falha; remover engine "langgraph" fantasma | CO-02/03/06/07/09/10, EN-02/03/04 | 12 |
| 012 | Segurança do Webhook e Painel Admin | 🟡 Planejada | 🟠 Alta | Fechar webhook e painel admin; parar vazamento de segredos/PII em logs | WE-03, WE-09, CO-08, AD-01/02/03/04/06 | 14 |

**Legenda de status:** 🟡 Planejada · 🔵 Em Andamento · 🟢 Concluída · 🔴 Bloqueada · ⚪ Cancelada.

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
