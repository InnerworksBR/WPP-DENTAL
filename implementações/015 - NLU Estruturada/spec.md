# NLU Estruturada

> **ID:** 015
> **Status:** 🟢 Concluída
> **Prioridade:** 🟠 Alta
> **Criada em:** 2026-06-22
> **Última atualização:** 2026-06-22
> **Autor:** Agente AI

---

## 1. Resumo Executivo

Cria um classificador de intenção único (`IntentClassifier`) que transforma a mensagem do paciente
em uma estrutura validada `{intent, entities}` — em vez das heurísticas de palavra-chave espalhadas
e da interpretação implícita feita pelo loop do LLM. Consolida a extração de restrições
(`AppointmentOfferService.extract_request_constraints`) e os sinais de intenção atrás de um contrato
estável que o Orquestrador (016) vai consumir. Não muda ainda o fluxo de produção — entrega a peça
de entendimento que torna o orquestrador determinístico possível.

## 2. Contexto e Motivação

### 2.1 Problema Atual
O "entendimento" do que o paciente quer está fragmentado em três lugares que se sobrepõem e
divergem: (a) heurísticas de keyword em `app.py` (`_capture_schedule_constraints`,
`_looks_like_slot_choice`, etc.); (b) `AppointmentOfferService.extract_request_constraints`
(recusa, período, horário, dia, exclusões); (c) o próprio LLM no `CleanAgentService`, que infere
intenção livremente e às vezes contradiz (a)/(b). Não há uma representação única e testável da
intenção do paciente.

### 2.2 Impacto do Problema
Sinais conflitantes entre as três fontes produzem os fluxos quebrados (oferta presa, "não está
entre as opções", confirmações em loop). Sem uma NLU única, o orquestrador determinístico (016)
não tem em que se basear.

### 2.3 Soluções Consideradas
| Solução | Prós | Contras | Decisão |
|---|---|---|---|
| LLM com saída estruturada (function/JSON schema) + fallback determinístico | Robusto a linguagem natural; 1 contrato | Custo de 1 chamada; precisa validação | ✅ Escolhida |
| Só regex/keyword determinístico | Sem custo de LLM | Frágil ao português real (já falhou) | ❌ Descartada (mantido como fallback) |
| Manter o LLM inferindo livre | Zero código | Causa atual dos conflitos | ❌ Descartada |

## 3. Especificação Técnica

### 3.1 Visão Geral da Arquitetura
Novo módulo `src/application/nlu/`. O `IntentClassifier.classify(message, context) -> NluResult`
faz **uma** chamada ao LLM com saída estruturada (schema Pydantic via `with_structured_output`),
validada; em falha/instabilidade, cai para o extrator determinístico já existente. O resultado é um
objeto neutro que descreve intenção e entidades, sem decidir nada sobre a agenda.

### 3.2 Componentes Afetados
| Componente | Tipo | Ação | Descrição |
|---|---|---|---|
| `src/application/nlu/__init__.py` | Arquivo | Criar | Exporta `IntentClassifier`, `NluResult`, `Intent` |
| `src/application/nlu/schema.py` | Arquivo | Criar | `Intent` (enum) + `NluResult`/`Entities` (Pydantic) |
| `src/application/nlu/intent_classifier.py` | Arquivo | Criar | Classificação via LLM estruturado + fallback determinístico |
| `src/domain/policies/appointment_offer_service.py` | Arquivo | Reusar | Reaproveitado como fallback determinístico (sem remoção nesta fase) |
| `tests/test_intent_classifier.py` | Arquivo | Criar | Casos de português real → `NluResult` esperado |

### 3.3 Interfaces e Contratos

#### Entradas
- `classify(message: str, context: NluContext) -> NluResult`. `NluContext` carrega o mínimo:
  houve oferta pendente? houve pedido de confirmação? período/dia já pedidos? (derivado do estado).

#### Saídas
- `NluResult.intent ∈ {agendar, remarcar, cancelar, confirmar, recusar, escolher_horario,
  informar_nome, informar_plano, consultar, saudacao, fora_escopo, ambiguo}`.
- `NluResult.entities`: `period`, `date`, `time`, `earliest_time`, `weekday`, `excluded_dates`,
  `plan`, `name`, `affirmation: bool|None`, `selected_option: int|None`.

#### Contratos de API (se aplicável)
N/A — módulo interno.

### 3.4 Modelos de Dados (se aplicável)
`NluResult`/`Entities` são modelos Pydantic (validação + coerção). Sem persistência.

### 3.5 Fluxo de Execução
1. Orquestrador (016) monta `NluContext` a partir do estado da conversa.
2. `classify` chama o LLM com schema; valida.
3. Se LLM indisponível/saída inválida → fallback: `extract_request_constraints` + heurísticas
   mínimas mapeadas para o mesmo `NluResult`.
4. Retorna `NluResult` — o orquestrador decide a ação.

### 3.6 Tratamento de Erros
- Timeout/instabilidade do LLM: usa fallback determinístico (nunca propaga exceção).
- Saída do LLM fora do schema: revalida 1x; persistindo, usa fallback.
- Intenção `ambiguo`: o orquestrador pergunta esclarecimento (definido em 016).

## 4. Requisitos

### 4.1 Requisitos Funcionais
- **RF-001:** `IntentClassifier.classify` retorna um `NluResult` validado para qualquer string.
- **RF-002:** Cobre as intenções listadas em §3.3 e as entidades de agenda hoje extraídas por
  `extract_request_constraints` (período, horário, dia, recusa, exclusões, earliest_time, weekday).
- **RF-003:** Fallback determinístico ativo quando o LLM falha, sem perda de função básica.
- **RF-004:** Detecta "escolha de horário ofertado" vs "novo pedido de restrição" usando o
  `NluContext` (resolve a ambiguidade que hoje vive em `_capture_schedule_constraints`).

### 4.2 Requisitos Não-Funcionais
- **RNF-001:** Uma única chamada de LLM por classificação (custo previsível).
- **RNF-002:** `temperature=0` e schema fixo (determinismo prático).
- **RNF-003:** Testável sem rede (LLM mockado; fallback testado de verdade).

### 4.3 Restrições e Limitações
- Não remover `AppointmentOfferService` nesta fase (a remoção/absorção ocorre em 016/017).
- Não alterar o fluxo de produção ainda — 015 entrega a peça; 016 a integra.

## 5. Critérios de Aceitação
- [x] **CA-001:** `classify` mapeia corretamente um conjunto de frases reais pt-BR de cada intenção.
- [x] **CA-002:** Entidades de agenda equivalentes às de `extract_request_constraints` para os
  mesmos inputs (paridade verificada por teste parametrizado).
- [x] **CA-003:** Com LLM mockado para falhar, o fallback determinístico produz `NluResult` útil
  (intenção determinística quando possível; `AMBIGUO` caso contrário).
- [x] **CA-004:** Distingue "pode ser às 9" (escolher_horario, horário ofertado) de "só depois das
  13h" (restrição, re-oferta) — caso clássico do bug 013.
- [x] **CA-005:** Suíte total verde (517 = 500 + 17 novos).

## 6. Plano de Testes

### 6.1 Testes Unitários
`test_intent_classifier.py`: tabela de frases → intenção/entidades; fallback com LLM mockado; paridade
com `extract_request_constraints`.

### 6.2 Testes de Integração
N/A nesta fase (integração real ocorre em 016). Smoke test garantindo que o módulo importa e roda
isolado.

### 6.3 Testes de Aceitação
CA-001..CA-004 por teste parametrizado; suíte total verde.

### 6.4 Casos de Borda (Edge Cases)
- "não", "nenhum", "outro", "?" → recusar.
- "11:00", "às 18:30" sem oferta → restrição de horário; com oferta correspondente → escolher.
- "particular" → informar_plano(particular).
- Mensagem fora de escopo (preço/clínico) → fora_escopo.

## 7. Riscos e Mitigações
| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| LLM estruturado inconsistente | Média | Médio | Schema rígido + revalidação + fallback determinístico |
| Divergência de paridade com o extrator atual | Média | Médio | Teste de paridade direta sobre os mesmos inputs |
| Custo/latência de uma chamada extra | Baixa | Baixo | `gpt-4o-mini`, 1 chamada, temperatura 0 |

## 8. Dependências

### 8.1 Dependências Internas
- 014 (Gateway) concluída — o orquestrador consumirá `InboundMessage`.
- Reusa `AppointmentOfferService` e `ConfigService` (planos).

### 8.2 Dependências Externas
- `langchain-openai` (já presente). Pydantic (já presente).

## 9. Observações e Decisões de Design
- A NLU **não decide** nada de agenda — só descreve. Toda decisão é do orquestrador (016). Essa
  fronteira é o que evita o "cérebro duplo" voltar.
- `NluContext` é deliberadamente mínimo para manter a classificação barata e estável.
- **Decisão de implementação:** design híbrido — as ENTIDADES vêm 100% do extrator determinístico
  já testado (`AppointmentOfferService`), confiável e sem custo; o LLM é usado APENAS para a
  intenção de alto nível nos casos não resolvidos deterministicamente. Mais robusto e barato que
  delegar tudo ao LLM, e funciona com o LLM fora do ar (fallback → intenção determinística/`AMBIGUO`).

---

> **⚠️ NOTA:** Contrato vivo. Alterações de escopo refletidas aqui antes do código.
