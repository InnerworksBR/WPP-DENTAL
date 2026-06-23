# WPP-DENTAL — Análise de Soluções e Plano Técnico para a Correção Definitiva

> **Status:** Proposta para aprovação · **Data:** 23/06/2026 · **Autor:** Análise técnica
> **Decisões do dono já registradas:** (1) entregar plano técnico antes de codar; (2) remover
> dependência paga de LLM usando modelo open source self-hosted (há servidor/VPS disponível);
> (3) o pedido é *analisar soluções*, não remendar — buscar a opção definitiva.

---

## 1. Sumário executivo

Os três sintomas que você relatou — **(a)** o bot repete o que já disse, **(b)** oferece horários
errados, e **(c)** o lembrete não chega a todos os pacientes — **não são três bugs independentes**.
São o efeito visível de **uma única causa estrutural**: o sistema tem dois "cérebros" decidindo a
mesma conversa ao mesmo tempo (uma máquina de estados determinística e o LLM), e eles
dessincronizam. Por isso o ciclo de remendos nunca termina: cada correção pontual conserta um
caminho e a desincronização reaparece em outro.

A boa notícia é que **o projeto já está 70% do caminho para a solução certa**. A refatoração
014–017 (documentada em `implementações/`) já isolou o transporte, consolidou a NLU e começou a
transferir as decisões para um orquestrador determinístico. O que faltou foi **terminar a inversão
de comando**: a oferta de horários ainda nasce em texto livre do LLM e é reconstruída por regex
(`_parse_offered_slots` no `app.py`). É exatamente aí que moram (a) e (b).

**Recomendação em uma frase:** concluir a refatoração já iniciada — tornar a máquina de estados a
**dona única de toda a transação de agendamento** e rebaixar o LLM a dois papéis estreitos
(entender a intenção e dar o tom) — e, nessa nova fronteira, **trocar o OpenAI por um modelo aberto
local (Ollama + Qwen2.5-7B)**, porque o modelo passa a fazer só uma tarefa simples que modelos
pequenos executam bem.

Sobre o WhatsApp há uma decisão nova e importante de 2026 que detalho na seção 6: a Meta passou a
**permitir explicitamente** bots de tarefa específica como agendamento, mas **banir** assistentes
genéricos. Isso reabre a API oficial como opção legítima — algo que não existia antes.

---

## 2. Diagnóstico da causa-raiz (confirmado no código)

### 2.1 O "cérebro duplo"

O `app.py` tem **2.308 linhas** com dezenas de handlers (`_handle_pending_slot_plan`,
`_handle_offered_slot_selection`, `_handle_reactive_reoffer`, `_handle_appointment_confirmation`,
…). Em paralelo, existe um orquestrador determinístico em `src/application/flow/`
(`orchestrator.py`, `states.py`). E ainda o `CleanAgentService` roda um laço de *tool-calls* com o
LLM. **Três peças disputam a verdade da conversa.**

O próprio README do programa de recuperação reconhece isso textualmente:

> *"o sistema tem dois cérebros disputando a conversa […] a verdade da oferta é reconstruída por
> regex na prosa do LLM (`_parse_offered_slots`)."*

### 2.2 Por que cada sintoma acontece

| Sintoma que você vê | Causa técnica | Onde está |
|---|---|---|
| **Repete o que já falou** | O estado da oferta vive em dois lugares (FSM + texto do LLM). Quando dessincronizam, o LLM "reapresenta" o que já tinha dito porque não enxerga que o passo já foi dado. | `clean_agent_service.py` + handlers em `app.py` |
| **Horários errados** | A oferta de horários é gerada como **prosa do LLM** e depois "lida de volta" por **regex** (`_parse_offered_slots`). Qualquer variação de redação do modelo quebra o casamento entre o que foi dito e o que existe na agenda. | `app.py` (`_parse_offered_slots`, `_handle_offered_slot_selection`) |
| **Lembrete não chega a todos** | O cron de confirmação tem **vários pontos de descarte silencioso**: consulta sem telefone e sem cadastro único, evento manual fora do formato esperado, e estado de conversa "ocupado". O paciente é pulado sem alerta para a clínica. | `appointment_confirmation_service.py` (`_resolve_missing_phones`, `_select_unique_appointments`, `send_next_day_confirmations`) |

A implementação 016 foi conscientemente fechada em "escopo seguro": a FSM assumiu nome/plano,
cancelamento e seleção, **mas a geração da oferta e a criação/remarcação continuaram no handler
provado**. A 017 (remover de vez o cérebro duplo) foi marcada como "não recomendada / over-
engineering". Essa decisão foi razoável para a época, mas é justamente a parte que sobrou que produz
(a) e (b). **Terminar a 016/017 na fronteira da oferta é o coração da solução definitiva.**

### 2.3 Conclusão do diagnóstico

Não falta um framework novo. Falta **fechar a arquitetura que você já começou**. Trocar tudo por
uma ferramenta de mercado (Rasa, Typebot, n8n) jogaria fora a lógica de criação/remarcação já
provada e a rede de 542 testes — e reintroduziria os mesmos problemas de estado em outra roupa
(ver seção 5).

---

## 3. As quatro decisões a tomar

A "solução definitiva" se decompõe em quatro eixos independentes. Para cada um comparei as opções
open source e dou uma recomendação:

1. **Arquitetura conversacional** — quem é o dono da verdade? (seção 4–5)
2. **Motor de linguagem (LLM)** — qual modelo aberto e como servir? (seção 6.1)
3. **Transporte WhatsApp** — Evolution, WAHA ou API oficial? (seção 6.2)
4. **Lembretes confiáveis** — eliminar o descarte silencioso (seção 7)

---

## 4. Eixo 1 — Arquitetura conversacional (a decisão mais importante)

O princípio que resolve (a) e (b) de forma definitiva é **uma única fonte de verdade**: a transação
de agendamento vira determinística do começo ao fim — disponibilidade → oferta → seleção → criação
→ remarcação → lembrete — e o LLM **nunca decide nem guarda estado**. Ele só faz duas coisas:

- **NLU:** transformar a mensagem do paciente em `{intenção, entidades}` (já existe o
  `IntentClassifier`, impl. 015).
- **Tom:** redigir, em linguagem natural, mensagens **cujo conteúdo a FSM já fixou** (os horários
  saem da agenda, não da imaginação do modelo).

Com isso, a oferta deixa de ser "prosa lida por regex" e passa a ser **dado estruturado**: a FSM
sabe exatamente quais slots ofereceu, e a seleção do paciente é casada contra essa lista — fim da
repetição e dos horários errados.

### 4.1 Opções para implementar a máquina de estados

| Opção | O que é | Prós | Contras | Veredito |
|---|---|---|---|---|
| **FSM própria (concluir a atual)** | Terminar o `src/application/flow/` que você já tem | Aproveita 542 testes; lógica de criação/remarcação já provada; zero dependência nova; controle total | Exige disciplina de não reabrir o cérebro duplo | **✅ Recomendado** |
| **LangGraph** | Runtime de grafos com estado, checkpoint e durabilidade | Padrão de mercado 2026; checkpointing e *human-in-the-loop* embutidos; sobrevive a restart | Adiciona dependência pesada; reescreveria o que já funciona; curva de aprendizado | Bom, mas é trocar uma engrenagem pronta por outra |
| **Rasa** | Framework de NLU+diálogo self-hosted | Forte em domínios regulados (saúde); multicanal | Exige pipeline de ML e treino; reescrita total; mata os testes atuais | ❌ Over-engineering para 1 clínica |
| **Typebot / n8n (fluxo visual)** | Construtor de fluxo no-code sobre Evolution | Rápido de prototipar; visual | Estado frágil para conversa livre; reintroduz acoplamento ao transporte; difícil testar; Typebot Pro é pago | ❌ Volta ao problema de estado |

> **Dado de mercado relevante:** o relatório *State of Agent Engineering 2026* (LangChain) atribui
> **mais de 60% dos incidentes de produção a gestão de estado**. Ou seja: o seu problema é o
> problema nº 1 do setor, e a resposta certa é *centralizar o estado*, não adicionar mais IA.

**Recomendação do Eixo 1:** **concluir a FSM própria.** É o caminho de menor risco, preserva o
ativo de testes, e ataca a raiz. LangGraph fica como alternativa só se no futuro você quiser
durabilidade/checkpoint multi-instância — o que hoje seria over-engineering para uma clínica.

---

## 5. Por que NÃO trocar por uma plataforma pronta

É tentador pensar "jogo fora e uso o Typebot/n8n/Rasa". Três razões para não fazer isso:

1. **Você perde a rede de 542 testes** que hoje é a única salvaguarda contra regressão — e foi a
   ausência dela que causou a reincidência histórica dos bugs (commits `#0002`→`#0005` corrigindo a
   mesma família).
2. **O problema de estado viaja junto.** n8n e Typebot têm estado de conversa frágil; você
   reintroduziria a desincronização em outra forma, agora dentro de uma ferramenta que você controla
   menos.
3. **A lógica de criação/remarcação atômica já está provada** no handler atual. Reescrever isso é
   risco puro, sem ganho.

A decisão correta é **cirúrgica**, não *rewrite* — exatamente a conclusão a que o seu próprio
programa de recuperação já tinha chegado.

---

## 6. Eixo 2 (LLM) e Eixo 3 (WhatsApp)

### 6.1 LLM open source self-hosted — substituir o OpenAI

Como na arquitetura nova o LLM faz **só NLU + redação** (tarefa estreita), um modelo pequeno aberto
é mais que suficiente. Não é preciso GPT-4.

**Modelo recomendado: Qwen2.5-7B-Instruct.**

- Multilíngue forte, **bom em português**, com *function calling* nativo — aparece no topo das
  listas de 2026 para PT + tool use.
- Quantização `Q4_K_M` ocupa **~4,7 GB**; `Q5_K_M` é o melhor equilíbrio qualidade/tamanho.
- Alternativas equivalentes: **Llama 3.1 8B Instruct** (também ótimo) e, se quiser algo ainda mais
  leve só para NLU, **Qwen2.5-3B**.

**Como servir: Ollama** (não vLLM).

| | Ollama | vLLM |
|---|---|---|
| Foco | Baixa concorrência, setup em minutos | Alta concorrência, produção em escala |
| Throughput sob carga | Menor (serializa requisições) | Até ~19x maior sob carga pesada |
| Ideal para | **Poucos usuários simultâneos (uma clínica)** | Milhares de req/dia, multi-GPU |
| Setup | Trivial, REST compatível com OpenAI | Mais complexo |

Uma clínica gera **baixíssima concorrência** (poucas conversas simultâneas). A própria literatura
de 2026 diz que "cinco usuários simultâneos rodam bem no Ollama". vLLM resolveria um problema de
escala que você **não tem**. **Ollama é a escolha certa** — e expõe API compatível com OpenAI, o
que torna a troca quase um *drop-in* no código atual (mesma interface, muda a base URL).

**Requisitos de infra (no seu servidor/VPS):**

- **Com GPU** (ideal): uma placa de **8–12 GB de VRAM** (ex.: RTX 3060 12GB) roda o Qwen2.5-7B
  Q4/Q5 com folga, a ~60–75 tokens/s — respostas instantâneas.
- **Só CPU** (funciona, mais lento): mínimo **16 GB de RAM**. O 7B Q4 roda, mas a alguns tokens/s.
  Como as chamadas de NLU são curtas e o volume é baixo, é **aceitável** para começar; dá para subir
  GPU depois.

> **Para eu fechar o dimensionamento exato, preciso saber a configuração do seu VPS (GPU? quantos
> GB de RAM/VRAM?).** Está na lista de pendências da seção 9.

### 6.2 Transporte WhatsApp — a decisão mudou em 2026

Aqui há uma novidade que afeta diretamente o seu projeto. **Desde 15/01/2026 a Meta alterou os
termos do WhatsApp:**

- ❌ **Banidos:** assistentes de IA *genéricos* (estilo ChatGPT) na plataforma.
- ✅ **Permitidos e até incentivados:** bots de **tarefa específica** — a Meta cita
  explicitamente *"customer service, order inquiries, and appointment management"*. **O seu caso
  (agendamento odontológico) está na lista do que é permitido.**

Isso reabre três caminhos:

| Opção | Tipo | Custo de mensagem | Risco de ban | Veredito |
|---|---|---|---|---|
| **Evolution API** (atual) | Não-oficial (WhatsApp Web) | **R$ 0** | **Alto** — viola ToS; Meta intensificou a detecção em 2025–2026 | Mantém custo zero, mas é o seu ponto frágil |
| **WAHA** | Não-oficial (reverse-engineering) | R$ 0 | Alto (mesma natureza da Evolution) | Sem ganho real sobre Evolution |
| **Cloud API oficial (Meta)** | Oficial | Conversa de **serviço** (paciente inicia, janela 24h) = **grátis**; *template* de lembrete (utility) ≈ **US$ 0,007** | **Nenhum** (é o canal oficial) | **Mais robusto; o agendamento em si é grátis** |

**O ponto crucial sobre "sem pagamento":**

- Quase **toda a conversa de agendamento é gratuita** mesmo na API oficial, porque acontece dentro
  da janela de serviço de 24h aberta quando o paciente manda mensagem.
- **Só o lembrete proativo** (você inicia a conversa) é um *template utility*, que custa ~US$ 0,007
  no Brasil (os primeiros 1.000/mês ≈ US$ 0,0068 cada). Para uma clínica com ~20–40 lembretes/dia,
  isso dá **algo como US$ 5–10/mês pagos direto à Meta** — não a um terceiro/BSP, e sem mensalidade
  se você usar a Cloud API diretamente.

Ou seja, há um **trade-off honesto** a decidir:

- **Caminho "R$ 0 absoluto":** manter Evolution API. Você não paga nada, mas convive com risco de
  ban e instabilidade — que é uma das origens do "toda hora dá erro".
- **Caminho "robusto e em conformidade":** migrar para a Cloud API oficial. O agendamento continua
  grátis; só os lembretes têm custo de centavos pagos à própria Meta. Elimina o risco de ban e a
  fragilidade do WhatsApp Web.

A arquitetura **já está preparada para os dois**: a impl. 014 isolou o transporte atrás de um
`MessagingGateway` com `EvolutionAdapter`. Adicionar um `CloudApiAdapter` é trocar uma peça, sem
mexer no orquestrador. **Minha recomendação:** manter a Evolution para validar a nova arquitetura
sem custo e, em paralelo, preparar o adapter oficial como rota de produção — você decide quando
virar a chave. Essa é uma decisão de negócio (risco vs. centavos), por isso a deixo explícita para
você na seção 9.

---

## 7. Eixo 4 — Lembretes que chegam a todos

Independente da arquitetura, o cron de confirmação precisa parar de **descartar pacientes em
silêncio**. Hoje há três pontos de perda em `appointment_confirmation_service.py`:

1. **Consulta sem telefone e sem cadastro único** → pulada (só gera `warning` no log).
2. **Estado de conversa "ocupado/recente"** → pulada para não interromper conversa.
3. **Evento manual fora do formato** esperado pelo parser do calendário → nem entra na lista.

Correções propostas (determinísticas, testáveis):

- **Relatório de cobertura diário:** ao fim do cron, enviar à clínica um resumo
  `enviados / pulados / falhas` com o **nome de cada paciente pulado e o motivo**. O lembrete pode
  até falhar para um caso — mas **nunca em silêncio**. A clínica aciona manualmente.
- **Fila de re-tentativa** para falhas de envio (já existe `failed_alert_store` e
  `outbound_message_store` para apoiar isso).
- **Normalização na entrada:** resolver telefone por nome de forma mais robusta e registrar a
  consulta "sem telefone" como pendência visível no painel `/admin`, não só no log.
- **Cobertura de teste** para cada caminho de descarte (a regra de ouro do projeto: nada concluído
  sem teste de regressão verde).

---

## 8. Quadro de custos — o que "open source / sem pagamento" significa de fato

| Componente | Hoje | Proposto | Custo recorrente |
|---|---|---|---|
| LLM | OpenAI (pago por token) | **Ollama + Qwen2.5-7B** no seu VPS | **R$ 0** (só energia/infra que você já tem) |
| WhatsApp | Evolution API | Evolution (R$ 0) **ou** Cloud API oficial | R$ 0 no agendamento; lembretes ≈ US$ 5–10/mês **se** optar pela oficial |
| Agenda | Google Calendar | Google Calendar (gratuito) ou **Cal.com** self-host | R$ 0 |
| Banco | SQLite | SQLite (ok para 1 instância) | R$ 0 |
| Orquestração | dupla | **FSM própria** | R$ 0 |

**Resultado:** a dependência paga que mais pesa (OpenAI) **zera**. O único custo opcional que
sobra é de centavos, pago diretamente à Meta, e **somente se** você escolher o WhatsApp oficial em
vez da Evolution. Nenhuma ferramenta de terceiro com mensalidade entra na conta.

---

## 9. Decisões em aberto (preciso de você)

1. **WhatsApp:** seguir "R$ 0 absoluto" com Evolution (com risco de ban) ou migrar para a Cloud API
   oficial (robusto, lembretes custam centavos à Meta)? Posso preparar os dois adapters e deixar a
   chave na sua mão.
2. **Infra do VPS:** qual a configuração? (Tem GPU? Quantos GB de VRAM/RAM?) Isso define se o
   Qwen2.5-7B roda instantâneo (GPU) ou aceitável (CPU 16GB), e se conviria o 3B.
3. **Google Calendar vs Cal.com:** manter o Google Calendar (já integrado e gratuito) ou migrar para
   Cal.com self-hosted (100% sob seu controle)? Recomendo **manter o Google** — já funciona e não é
   a origem dos problemas.

---

## 10. Roadmap de implementação proposto (quando você aprovar)

Tudo na branch `refactor/nucleo-conversa`, usando os 542 testes como catraca anti-regressão. Cada
fase entra com seus próprios testes verdes antes de avançar.

**Fase A — Fechar a fronteira da oferta (resolve "repete" + "horário errado")**
- FSM passa a ser dona da geração da oferta: os slots viram **dado estruturado**, não prosa.
- Aposentar `_parse_offered_slots`; a seleção do paciente casa contra a lista que a FSM ofereceu.
- LLM rebaixado a redigir mensagens de conteúdo já fixado.
- Testes de conversa cobrindo repetição e seleção de horário.

**Fase B — Lembretes confiáveis (resolve "não chega a todos")**
- Relatório de cobertura diário à clínica + fila de re-tentativa + pendências visíveis no `/admin`.
- Testes para cada caminho de descarte.

**Fase C — Trocar o LLM para open source**
- Subir Ollama + Qwen2.5-7B no VPS.
- Apontar a camada de LLM (interface compatível com OpenAI) para o Ollama; remover a chave OpenAI.
- Validar NLU/tom em português com a bateria de conversas simuladas.

**Fase D (opcional/decisão sua) — WhatsApp oficial**
- Implementar `CloudApiAdapter` atrás do `MessagingGateway` existente.
- Cadastrar *templates* de utility para os lembretes.
- Virar a chave de transporte quando você decidir.

**Fase E — Merge para `main` e deploy**
- Suíte completa verde + validação manual de ponta a ponta.

---

## 11. Conclusão

Você não precisa de uma ferramenta nova nem de um *rewrite*. A solução definitiva é **terminar a
arquitetura que o projeto já começou** — máquina de estados como dona única da transação, LLM
contido em NLU + tom — e, nessa fronteira limpa, **substituir o OpenAI por Qwen2.5-7B no Ollama**
(custo zero) e **endurecer o cron de lembretes** para nunca mais perder um paciente em silêncio. O
WhatsApp ganhou, em 2026, um caminho oficial legítimo para bots de agendamento; manter a Evolution
ou migrar para o oficial é a única decisão de negócio (risco vs. centavos) que sobra.

Aprovando este plano (e respondendo às 3 perguntas da seção 9), eu começo pela Fase A.

---

### Fontes consultadas

- WhatsApp — proibição de chatbots genéricos e exceção para agendamento: [TechCrunch](https://techcrunch.com/2025/10/18/whatssapp-changes-its-terms-to-bar-general-purpose-chatbots-from-its-platform/), [respond.io](https://respond.io/blog/whatsapp-general-purpose-chatbots-ban), [Alibaba Cloud](https://www.alibabacloud.com/help/en/chatapp/use-cases/whatsapp-ai-policy-2026-guide)
- Preços WhatsApp Business API 2026 (Brasil): [Message Central](https://www.messagecentral.com/blog/whatsapp-business-api-pricing-in-brazil), [Blueticks](https://blueticks.co/blog/whatsapp-business-api-pricing-2026)
- Risco de ban Evolution/WAHA: [Kraya AI](https://blog.kraya-ai.com/whatsapp-automation-ban-risk), [WasenderAPI](https://wasenderapi.com/blog/how-to-use-evolution-api-without-getting-banned-on-whatsapp-2026-guide)
- Ollama vs vLLM: [Red Hat](https://www.redhat.com/en/topics/ai/vllm-vs-ollama), [Spheron](https://www.spheron.network/blog/ollama-vs-vllm/)
- Melhores LLMs open source para português + function calling: [SiliconFlow](https://www.siliconflow.com/articles/en/best-open-source-LLM-for-Portuguese), [Hugging Face](https://huggingface.co/blog/daya-shankar/open-source-llms)
- Requisitos Qwen2.5-7B (GGUF/Ollama/VRAM): [Local AI Master](https://localaimaster.com/models/qwen-2-5-7b), [bartowski/Qwen2.5-7B-Instruct-GGUF](https://huggingface.co/bartowski/Qwen2.5-7B-Instruct-GGUF)
