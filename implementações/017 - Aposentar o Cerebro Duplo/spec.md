# Aposentar o Cérebro Duplo

> **ID:** 017
> **Status:** 🟡 Planejada
> **Prioridade:** 🟠 Alta
> **Criada em:** 2026-06-22
> **Última atualização:** 2026-06-22
> **Autor:** Agente AI

---

## 1. Resumo Executivo

Conclui o refactor: remove o loop de tool-calls do `CleanAgentService` como decisor, deixando o LLM
**apenas** nos papéis estreitos de NLU (015) e tom/escalação fora de escopo. Apaga os guard-rails
que só existiam para babá do loop (validação de slot por regex, bloqueio de `criar_agendamento` em
remarcação, detector de loop) e enxuga o `app.py` para um controlador fino. Resultado: **uma única
fonte de verdade** (a FSM de 016) e um `app.py` de ~150 linhas.

## 2. Contexto e Motivação

### 2.1 Problema Atual
Após 016, a FSM já decide a agenda, mas o `CleanAgentService` (loop de até 8 iterações com 10 tools)
ainda existe com toda a sua maquinaria de segurança: `_is_offered_slot` (valida slot reconstruído
por regex), bloqueio de `criar_agendamento` em remarcação, `_LOOP_ABORT_THRESHOLD`, etc. Manter os
dois caminhos é dívida e risco de o "cérebro duplo" voltar por algum atalho.

### 2.2 Impacto do Problema
Código morto/perigoso, superfície de bug maior, e o `app.py` ainda inchado dificultam manutenção e
onboarding. Enquanto os dois existirem, há risco de regressão para o padrão antigo.

### 2.3 Soluções Consideradas
| Solução | Prós | Contras | Decisão |
|---|---|---|---|
| Reduzir o LLM a NLU + tom e remover o loop decisor | 1 verdade; menos código; menos bug | Exige 016 estável antes | ✅ Escolhida |
| Manter o loop como fallback | "Rede extra" | Recria o cérebro duplo; ambiguidade | ❌ Descartada |

## 3. Especificação Técnica

### 3.1 Visão Geral da Arquitetura
O `CleanAgentService` é substituído por um `ReplyComposer` (tom) enxuto: dado um resultado
determinístico do orquestrador, gera o texto final (template `messages.yaml` no caminho feliz; LLM
só para conversa livre/fora de escopo). O `app.py` vira um controlador fino: autenticação →
idempotência/handoff/TTL → `orchestrator.handle` → `gateway.send_text`.

### 3.2 Componentes Afetados
| Componente | Tipo | Ação | Descrição |
|---|---|---|---|
| `src/application/services/clean_agent_service.py` | Arquivo | Remover/Substituir | Loop decisor sai; vira `ReplyComposer` ou é apagado |
| `src/application/render/reply_composer.py` | Arquivo | Criar | Tom: template + LLM só em fora-de-escopo/livre |
| `src/interfaces/http/app.py` | Arquivo | Modificar | Enxugar para controlador fino (~150 linhas) |
| `src/interfaces/tools/*` | Arquivo | Avaliar | Tools do LLM viram chamadas diretas de serviço (ou são removidas) |
| `tests/test_clean_agent_service.py` | Arquivo | Migrar/Remover | Comportamento coberto pelos testes da FSM (016) |

### 3.3 Interfaces e Contratos
#### Entradas
- `ReplyComposer.compose(result: OrchestratorResult, context) -> str`.
#### Saídas
- Texto final ao paciente (string).
#### Contratos de API (se aplicável)
N/A externo (webhook inalterado).

### 3.4 Modelos de Dados (se aplicável)
N/A — sem novos modelos persistidos.

### 3.5 Fluxo de Execução
1. Webhook fino recebe → autentica → idempotência/handoff/TTL (inalterados).
2. `orchestrator.handle` decide (016).
3. `ReplyComposer.compose` gera o texto (template no caminho feliz; LLM só fora de escopo/livre).
4. `gateway.send_text` envia; efeitos aplicados.

### 3.6 Tratamento de Erros
- Fora de escopo/ambíguo: LLM compõe a escalação amigável; `ScopeGuardService` segue validando a
  segurança da resposta (impl 008 preservada).
- LLM indisponível no tom: usa template neutro de fallback (sem travar o atendimento).

## 4. Requisitos

### 4.1 Requisitos Funcionais
- **RF-001:** O loop de tool-calls decisor é removido; o LLM não chama mais ferramentas de agenda.
- **RF-002:** `_parse_offered_slots`, `_is_offered_slot` e guard-rails do loop são removidos.
- **RF-003:** Caminho feliz responde por template; LLM só em conversa livre/fora de escopo.
- **RF-004:** `app.py` reduz a um controlador fino (~150 linhas), sem `_handle_*` de agenda.
- **RF-005:** `ScopeGuardService` (008) continua validando respostas livres.

### 4.2 Requisitos Não-Funcionais
- **RNF-001:** Suíte total verde (com testes do "cérebro" migrados para a FSM).
- **RNF-002:** Redução mensurável de LOC no `app.py` e remoção do `CleanAgentService` decisor.
- **RNF-003:** Custo de LLM por conversa não aumenta (idealmente cai: NLU + tom pontual vs loop).

### 4.3 Restrições e Limitações
- Só iniciar após 016 estável e verde.
- Preservar escopo/segurança (008) e mensageria/alertas (009).

## 5. Critérios de Aceitação
- [ ] **CA-001:** `CleanAgentService` decisor não existe mais (removido ou reduzido a tom).
- [ ] **CA-002:** Nenhuma decisão de agenda passa por tool-call de LLM.
- [ ] **CA-003:** `app.py` ≤ ~200 linhas, sem `_handle_*` de agenda.
- [ ] **CA-004:** Suíte total verde; testes de fluxo cobertos pela FSM.
- [ ] **CA-005:** Resposta fora de escopo continua bloqueada/segura (008 verde).

## 6. Plano de Testes
### 6.1 Testes Unitários
`test_reply_composer.py`: template no caminho feliz; LLM mockado para fora de escopo; fallback neutro.
### 6.2 Testes de Integração
Webhook fino ponta-a-ponta (reusa/ajusta `test_main_webhook`).
### 6.3 Testes de Aceitação
CA-001..CA-005; suíte total verde; verificação textual de remoção dos guard-rails.
### 6.4 Casos de Borda (Edge Cases)
- Fora de escopo após fluxo legítimo (não expulsar paciente — preserva 008).
- LLM indisponível no tom → fallback neutro.
- Handoff automático por marcador de resposta (preserva WE-13).

## 7. Riscos e Mitigações
| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Remover algo ainda usado por um caminho raro | Média | Alto | Só após 016 verde; remover incrementalmente com suíte como catraca |
| Perder nuance de tom do LLM no caminho feliz | Baixa | Baixo | Templates revisados; LLM disponível para livre/fora de escopo |
| Testes do "cérebro" sem equivalente na FSM | Média | Médio | Garantir paridade de cobertura em 016 antes de remover |

## 8. Dependências
### 8.1 Dependências Internas
- 016 concluída e estável (pré-requisito forte). Preserva 008 e 009.
### 8.2 Dependências Externas
- Nenhuma nova.

## 9. Observações e Decisões de Design
- Esta é a fase que **trava** o ganho: enquanto o loop decisor existir, o cérebro duplo pode voltar.
  Removê-lo é o que torna a arquitetura nova definitiva.
- Avaliar se as tools do LLM (`interfaces/tools/*`) viram serviços diretos chamados pela FSM ou são
  removidas — decisão registrada aqui ao executar.

---

> **⚠️ NOTA:** Contrato vivo. Alterações de escopo refletidas aqui antes do código.
