# Guarda de Escopo Robusto

> **ID:** 008
> **Status:** 🟢 Concluída
> **Prioridade:** 🟠 Alta
> **Criada em:** 2026-06-15
> **Última atualização:** 2026-06-16
> **Autor:** Agente AI

---

## 1. Resumo Executivo

Esta implementação reforça o guarda de escopo (`ScopeGuardService` em `src/domain/policies/scope_guard_service.py`) e os pontos de integração no fluxo determinístico (`src/interfaces/http/app.py`) para impedir que o assistente da Dra. Priscila **vaze informações proibidas** (preço/valor e conteúdo clínico/procedimento) sem, ao mesmo tempo, **expulsar pacientes em fluxo legítimo de agendamento**.

O foco corrige a queixa do dono nº 3 ("foge do escopo — preço/clínico — PROIBIDO"). Hoje há duas falhas opostas e simultâneas:

- **Falsos negativos (vaza):** o método `response_is_safe` (`scope_guard_service.py:149-168`) faz curto-circuito: se a resposta contém **qualquer** marcador "seguro" de `_SAFE_RESPONSE_MARKERS` (linha 155-156), ela é aprovada **antes** de verificar os padrões proibidos `_UNSAFE_RESPONSE_PATTERNS` (linha 158). Isso permite que uma resposta como "Posso te ajudar com sua consulta. O clareamento custa R$ 800" passe como segura (SC-01, crítico). Detecção de preço por keyword é frágil (SC-02), valor "nu" sem `R$`/`reais` escapa (SC-03), sintomas comuns não são detectados (SC-05) e a normalização não neutraliza ofuscação (SC-06). Além disso, a tool `verificar_convenio` (`config_tool.py:43-61`) devolve restrições/coberturas de procedimento ao paciente (AG-05).
- **Falsos positivos (escala demais):** a checagem clínica/procedimento de `classify_patient_message` derruba mensagens de agendamento legítimas (SC-04).

Há também dois defeitos de ordenação/estado no fluxo: `_handle_scope_escalation` roda **antes** dos handlers de confirmação/slot e chama `ConversationStateService.clear` (`app.py:1331`), descartando `pending`/`reschedule` (WE-07); e `_handle_appointment_confirmation` retorna `None` (`app.py:1284`) para mensagens sem token reconhecido durante a confirmação, caindo no LLM e podendo vazar escopo (CO-03).

## 2. Contexto e Motivação

### 2.1 Problema Atual

O guarda de escopo é a última linha de defesa contra respostas proibidas, mas tem furos estruturais e gera atrito desnecessário:

1. **SC-01 (crítico, scope_leak):** Em `response_is_safe` (`scope_guard_service.py:155-159`):
   ```python
   if any(marker in normalized for marker in cls._SAFE_RESPONSE_MARKERS):
       return True
   if any(pattern.search(normalized) for pattern in cls._UNSAFE_RESPONSE_PATTERNS):
       return False
   ```
   O `return True` por marcador seguro ocorre **antes** da checagem de conteúdo proibido. Como os marcadores são substrings genéricas ("posso te ajudar com sua consulta", "apenas com agendamentos"), basta a resposta conter um deles para que valores em reais ou recomendações clínicas (linhas 81-88 de `_UNSAFE_RESPONSE_PATTERNS`) sejam liberados. O guard de saída em `app.py:274` (`if not ScopeGuardService.response_is_safe(...)`) confia nesse retorno, então o vazamento chega ao paciente.

2. **SC-02 (alto):** `_PRICE_PATTERNS` (`scope_guard_service.py:19-25`) usa `\bpreco\b`, `\bvalor\b`, `\bcusta(r|m)?\b`, `\bquanto (fica|custa)\b`, `\borcamento\b`. Plural ("precos", "valores"), sinônimos/gírias ("tabela", "quanto sai", "ta quanto", "$$") e variações passam direto, tanto na classificação de entrada quanto, indiretamente, na avaliação de saída.

3. **SC-03 (alto):** `_UNSAFE_RESPONSE_PATTERNS` (`scope_guard_service.py:81-88`) só pega preço com `r\$\s*\d` ou `\d+...\s*reais`. Valor "nu" ("fica em 350", "são 350,00") escapa. A cobertura clínica de saída depende de `_PROCEDURE_TERMS`/`_CLINICAL_PATTERNS`, que é uma lista fechada; conteúdo clínico fora da lista não é barrado.

4. **SC-04 (médio, falso positivo):** `classify_patient_message` (`scope_guard_service.py:136-144`) escala quando `has_procedure_term and asks_about_procedure`. A mitigação `supports_operational_triage` (linhas 129-134) cobre só uma lista curta (`_SUPPORTED_OPERATIONAL_PROCEDURE_TERMS`, linhas 52-58). Mensagens legítimas como "quero marcar uma limpeza" combinam termo de procedimento ("limpeza") com `\bmarc` mas, se o contexto não casar exatamente, podem escalar indevidamente.

5. **SC-05 (médio, falso negativo):** `_CLINICAL_PATTERNS` (`scope_guard_service.py:71-80`) lista dor, inchaço, sangramento, febre, inflamação, urgência, sensibilidade, infecção. Sintomas comuns ("ardência", "pus", "abscesso", "trincou", "quebrou o dente", "machucou", "lateja", "pulsando") não são detectados.

6. **SC-06 (médio):** `_normalize` (`scope_guard_service.py:104-108`) remove acentos, baixa caixa e colapsa espaços, mas não trata separadores intercalados ("p r e c o", "p.r.e.c.o"), repetição de letras ("preçoooo") nem dígitos-leet — guard puramente por keyword é contornável.

7. **AG-05 (alto, scope_leak):** `CheckPlanTool._run` (`config_tool.py:43-61`) monta uma string com `Restrições: ...` e "Estes procedimentos NÃO são cobertos por este convênio." (linhas 57-58). Como é uma tool do agente, esse detalhe de cobertura/procedimento pode ser repassado ao paciente, violando o escopo.

8. **WE-07 (médio):** Em `receive_message`, `_handle_scope_escalation` é chamado (`app.py:206-213`) **antes** de `_handle_pending_slot_plan` (215-223) e `_handle_appointment_confirmation` (225-233). Dentro dele, `ConversationStateService.clear(phone)` (`app.py:1331`) apaga `pending_event_id`/`reschedule_event_id`/stage. Se o paciente em meio a uma remarcação digitar algo que casa um padrão de escopo, o estado de agenda é destruído.

9. **CO-03 (alto):** `_handle_appointment_confirmation` (`app.py:1229-1284`) só trata remarcar/afirmativo/negativo; qualquer outra mensagem retorna `None` (linha 1284). O fluxo então segue para o LLM em `app.py:255-264`, que pode vazar escopo ou responder errado durante uma confirmação.

### 2.2 Impacto do Problema

- **Conformidade/credibilidade:** vazar preço ou orientação clínica (SC-01, SC-03, AG-05) é a violação mais grave do PRD ("PROIBIDO") e expõe a doutora a passar informação incorreta ou indevida.
- **Experiência do paciente:** falsos positivos (SC-04) interrompem agendamentos legítimos com mensagem de escalação, gerando atrito; perda de estado de remarcação (WE-07) força reinício do fluxo.
- **Risco silencioso:** valores "nus" (SC-03) e sintomas fora da lista (SC-05) passam sem alerta — ninguém percebe o vazamento.
- **Confiança no guard:** SC-06 torna o guard contornável, reduzindo a confiança na última linha de defesa.

### 2.3 Soluções Consideradas

| Solução | Prós | Contras | Decisão |
|---|---|---|---|
| Reordenar a checagem em `response_is_safe`: padrões proibidos primeiro, marcador seguro só desempata o resto | Corrige SC-01 com mudança mínima e localizada; preserva o resto da lógica | Não resolve sozinha SC-02/03/05/06 | **Adotada** (parte do núcleo do fix) |
| Ampliar listas de keywords (preço, clínico, procedimento) + normalização agressiva (anti-ofuscação) | Cobre SC-02/03/05/06; determinístico e testável; sem custo de API | Listas exigem manutenção; risco de novos falsos positivos se mal calibrado | **Adotada** com testes de regressão para calibrar |
| Classificador de escopo via LLM (segunda chamada OpenAI) | Robusto a sinônimos/ofuscação | Adiciona latência/custo/ponto de falha de API (contraria queixa nº 1); não determinístico | **Rejeitada** — guard deve ser determinístico e barato |
| Guarda de contexto para distinguir agendamento de pedido de info (SC-04) | Reduz falsos positivos sem afrouxar o bloqueio | Lógica de contexto mais complexa | **Adotada** — expandir `_SUPPORTED_OPERATIONAL_*` e exigir intenção informativa explícita |
| Reordenar handlers no `receive_message` + não limpar estado de agenda ao escalar (WE-07) | Preserva remarcação/pending; corrige ordem | Toca o fluxo principal — exige testes de integração | **Adotada** |
| Bloquear saída da tool `verificar_convenio` (AG-05): remover restrições/cobertura da string ao paciente | Fecha vazamento na origem | Precisa garantir que o alerta interno à doutora ainda receba o detalhe | **Adotada** — separar mensagem-ao-paciente de detalhe-interno |
| Fallback determinístico em `_handle_appointment_confirmation` para mensagem sem token (CO-03) | Evita o LLM durante confirmação; sem vazamento | Reduz flexibilidade da confirmação | **Adotada** — reapresentar confirmação/escalar em vez de cair no LLM |

## 3. Especificação Técnica

### 3.1 Visão Geral da Arquitetura

O guarda de escopo é uma **policy de domínio** pura (`ScopeGuardService`, sem I/O) consumida por duas portas no fluxo determinístico:

- **Entrada:** `classify_patient_message` é chamada por `_handle_scope_escalation` (`app.py:1318`) no início de `receive_message`.
- **Saída:** `response_is_safe` é chamada após a geração do LLM (`app.py:274`); se reprovar, `_force_safe_escalation_response` (`app.py:895-909`) substitui por escalação segura.

As mudanças preservam essa separação: toda a lógica de classificação/normalização permanece em `scope_guard_service.py`; `app.py` só ajusta ordem, preservação de estado e o fallback de confirmação; `config_tool.py` deixa de devolver detalhe de cobertura ao paciente.

### 3.2 Componentes Afetados

| Componente | Tipo | Ação | Descrição |
|---|---|---|---|
| `src/domain/policies/scope_guard_service.py` → `response_is_safe` | Método | Modificar | Checar `_UNSAFE_RESPONSE_PATTERNS`/procedimento/clínico **antes** do `return True` por marcador seguro (SC-01). Adicionar padrão de valor "nu" (SC-03). |
| `src/domain/policies/scope_guard_service.py` → `_PRICE_PATTERNS` | Atributo | Modificar | Incluir plural e sinônimos/gírias de preço (SC-02). |
| `src/domain/policies/scope_guard_service.py` → `_CLINICAL_PATTERNS` | Atributo | Modificar | Incluir sintomas comuns ausentes (SC-05). |
| `src/domain/policies/scope_guard_service.py` → `_normalize` | Método | Modificar | Normalização agressiva anti-ofuscação (SC-06). |
| `src/domain/policies/scope_guard_service.py` → `classify_patient_message` | Método | Modificar | Reduzir falsos positivos de agendamento (SC-04) ampliando contexto operacional. |
| `src/interfaces/tools/config_tool.py` → `CheckPlanTool._run` | Método | Modificar | Não devolver restrições/cobertura de procedimento na mensagem ao paciente; manter detalhe só para o caminho de referral/alerta interno (AG-05). |
| `src/interfaces/http/app.py` → `receive_message` (206-233) | Função | Modificar | Reordenar: tratar confirmação/slot antes da escalação, ou não limpar estado de agenda ao escalar (WE-07). |
| `src/interfaces/http/app.py` → `_handle_scope_escalation` (1311-1351) | Função | Modificar | Não chamar `ConversationStateService.clear` (1331) quando há estado de agenda ativo (WE-07). |
| `src/interfaces/http/app.py` → `_handle_appointment_confirmation` (1229-1284) | Função | Modificar | Substituir o `return None` (1284) por fallback determinístico (reapresentar confirmação ou escalar) (CO-03). |

### 3.3 Interfaces e Contratos

Assinaturas públicas preservadas (mudança apenas de comportamento interno):

- `ScopeGuardService.classify_patient_message(text: str) -> EscalationDecision | None` — `EscalationDecision(reason, summary)` (`scope_guard_service.py:8-13`). Contrato: retorna decisão para preço/clínico/info-de-procedimento; `None` para agendamento legítimo.
- `ScopeGuardService.response_is_safe(response_text: str) -> bool` — **novo contrato:** retorna `False` se a resposta contém preço (incl. valor nu), procedimento ou conteúdo clínico, **independentemente** de conter marcador seguro.
- `CheckPlanTool._run(plan_name: str) -> str` — contrato ajustado: a string retornada ao agente para repasse ao paciente não deve conter "Restrições: ..." nem "Estes procedimentos NÃO são cobertos". Detalhe de cobertura, quando necessário, segue apenas no caminho de referral/alerta interno.
- `_handle_appointment_confirmation(...) -> JSONResponse | None` — passa a retornar `JSONResponse` (nunca cair no LLM) quando a mensagem não casa nenhum token durante a confirmação.

### 3.4 Modelos de Dados

N/A — justificativa: esta implementação não altera esquema persistente (SQLite) nem o modelo de estado conversacional. `EscalationDecision` (dataclass frozen, `scope_guard_service.py:8-13`) é mantido como está. Eventuais flags auxiliares (ex.: marcar que houve preservação de estado em WE-07) usam o `ConversationState` existente sem novos campos persistidos.

### 3.5 Fluxo de Execução

**Entrada (classificação):**
1. `receive_message` calcula `current_state` (`app.py:204`).
2. **(WE-07, novo)** Se `current_state.stage` for de confirmação/slot/pending, tratar primeiro o handler específico; só então avaliar escalação — ou, ao escalar com estado de agenda ativo, **não** chamar `clear` (`app.py:1331`).
3. `classify_patient_message` normaliza (`_normalize` reforçado, SC-06) e checa, na ordem: preço (`_PRICE_PATTERNS` ampliado, SC-02) → clínico (`_CLINICAL_PATTERNS` ampliado, SC-05) → triagem operacional suportada (SC-04) → info-de-procedimento.

**Saída (validação da resposta do LLM):**
1. LLM gera `response_text` (`app.py:255-264`).
2. `response_is_safe` normaliza e, **na nova ordem**, verifica primeiro `_UNSAFE_RESPONSE_PATTERNS` (incl. valor nu, SC-03), `_PROCEDURE_TERMS` e `_CLINICAL_PATTERNS`; se qualquer um casar → `False`. Só se nada proibido casar é que o marcador seguro confirma `True`.
3. Se `False`, `_force_safe_escalation_response` (`app.py:895-909`) alerta a doutora e devolve mensagem de escalação.

**Confirmação (CO-03):**
1. Em `_handle_appointment_confirmation`, se a mensagem não casa remarcar/afirmativo/negativo, em vez de `return None` (`app.py:1284`), reapresenta o pedido de confirmação ou escala — nunca segue para o LLM.

### 3.6 Tratamento de Erros

- **Texto vazio/None:** `_normalize` já trata `text or ""` (`scope_guard_service.py:106`); `classify_patient_message` retorna `None` para normalizado vazio (linha 114) e `response_is_safe` retorna `True` para vazio (linha 152-153). Comportamento mantido.
- **Falha de envio da escalação:** `_handle_scope_escalation` e `_force_safe_escalation_response` já levantam `HTTPException(502)` / marcam falha (`app.py:1334-1337`); mantido. Em WE-07, garantir que a preservação de estado ocorra **antes** de qualquer envio que possa falhar.
- **Falha do alerta interno (`_send_scope_alert`):** já é envolvido em `try/except` que apenas loga (`app.py:1307-1308`), não interrompe o fluxo; mantido.
- **Regressão de falso positivo:** mitigada por suíte de testes de calibração (seção 6) cobrindo mensagens legítimas de agendamento que NÃO devem escalar.

## 4. Requisitos

### 4.1 Requisitos Funcionais

- **RF-001 (SC-01):** `response_is_safe` deve retornar `False` para qualquer resposta que contenha padrão proibido (preço, procedimento ou clínico), mesmo que também contenha um marcador de `_SAFE_RESPONSE_MARKERS`. A checagem de conteúdo proibido deve ocorrer antes do `return True` por marcador seguro.
- **RF-002 (SC-02):** A detecção de preço (`_PRICE_PATTERNS`) deve cobrir plural ("precos", "valores"), e variações/sinônimos comuns ("tabela de preco", "quanto sai", "ta quanto", "quanto fica").
- **RF-003 (SC-03):** `response_is_safe` deve reprovar valores monetários "nus" (ex.: "fica em 350", "são 350,00", "uns 1.200"), além de `R$`/`reais`.
- **RF-004 (SC-04):** `classify_patient_message` NÃO deve escalar mensagens de agendamento legítimas que mencionem procedimento em contexto de marcar/agendar/consulta (ex.: "quero marcar uma limpeza", "preciso agendar avaliação de aparelho").
- **RF-005 (SC-05):** `_CLINICAL_PATTERNS` deve detectar sintomas clínicos comuns adicionais (ex.: "ardência", "pus", "abscesso", "trincou/quebrou o dente", "lateja/latejando", "pulsando", "machucou").
- **RF-006 (SC-06):** `_normalize` deve neutralizar ofuscação básica (separadores intercalados entre letras, repetição de caracteres) de modo que termos-chave de preço/clínico continuem detectáveis.
- **RF-007 (AG-05):** `CheckPlanTool._run` não deve retornar detalhes de restrições/cobertura de procedimento na mensagem destinada ao paciente; tais detalhes ficam restritos ao caminho de referral/alerta interno à doutora.
- **RF-008 (WE-07):** Quando o paciente estiver em estado de confirmação/slot/pending/reschedule, a escalação de escopo não pode descartar `pending_event_id`/`reschedule_event_id`/stage; o estado de agenda deve sobreviver à escalação.
- **RF-009 (CO-03):** Durante o estágio de confirmação (`CONFIRMATION_STAGE`), uma mensagem sem token reconhecido não pode cair no LLM; deve ser tratada deterministicamente (reapresentar confirmação ou escalar com segurança).

### 4.2 Não-Funcionais

- **RNF-001 (Determinismo):** O guarda deve ser 100% determinístico, sem chamadas externas/LLM, para não reintroduzir a queixa nº 1 (API dá erro). Tempo de execução desprezível (regex/substring em memória).
- **RNF-002 (Sem regressão de escopo):** Nenhuma das mudanças pode reabrir um vazamento previamente fechado; cobertura de testes de regressão obrigatória para SC-01, SC-03 e AG-05.
- **RNF-003 (Baixo falso positivo):** A taxa de falsos positivos em mensagens de agendamento legítimas deve ser mantida baixa; conjunto de exemplos legítimos versionado nos testes (SC-04).
- **RNF-004 (Manutenibilidade):** Listas de keywords/padrões devem permanecer centralizadas como atributos de classe nomeados em `ScopeGuardService`, sem espalhar lógica de escopo por `app.py`.

### 4.3 Restrições

- Não introduzir dependências externas novas; usar apenas `re`/`unicodedata` (já importados em `scope_guard_service.py:3-4`).
- Não alterar assinaturas públicas dos métodos do `ScopeGuardService` nem o contrato do webhook.
- Bloqueios do Calendar e demais regras de agenda permanecem responsabilidade das implementações 006/007 — fora do escopo desta.
- Depende de 001 (estabilidade/IO) e 002 (rede de testes) estarem disponíveis para rodar a suíte de regressão.

## 5. Critérios de Aceitação

- [ ] **CA-001 (RF-001/SC-01):** Resposta contendo marcador seguro + "R$ 800" (ou recomendação clínica) faz `response_is_safe` retornar `False`.
- [ ] **CA-002 (RF-001):** Resposta contendo apenas marcador seguro, sem conteúdo proibido, continua retornando `True`.
- [ ] **CA-003 (RF-002/SC-02):** "qual a tabela de precos?" e "quanto sai a consulta?" são classificadas como `fora_do_escopo` por `classify_patient_message`.
- [ ] **CA-004 (RF-003/SC-03):** Resposta "fica em 350" / "são 350,00" faz `response_is_safe` retornar `False`.
- [ ] **CA-005 (RF-004/SC-04):** "quero marcar uma limpeza" e "preciso agendar avaliação de aparelho" retornam `None` em `classify_patient_message` (não escalam).
- [ ] **CA-006 (RF-005/SC-05):** "estou com pus na gengiva" / "meu dente trincou e lateja" são classificadas como `duvida_clinica`.
- [ ] **CA-007 (RF-006/SC-06):** "p r e c o" / "preçooo" são detectados como pedido de preço após normalização.
- [ ] **CA-008 (RF-007/AG-05):** A string retornada por `CheckPlanTool._run` para um plano com restrições não contém "Restrições:" nem "NÃO são cobertos" quando destinada ao paciente.
- [ ] **CA-009 (RF-008/WE-07):** Paciente em estado de reschedule que envia mensagem de escopo mantém `reschedule_event_id`/stage após o tratamento (estado não é apagado).
- [ ] **CA-010 (RF-009/CO-03):** Mensagem ambígua durante `CONFIRMATION_STAGE` retorna `JSONResponse` determinístico e não invoca `dental_crew.process_message`.
- [ ] **CA-011 (RNF-001):** Nenhuma das mudanças adiciona chamada de rede/LLM ao caminho do guarda.

## 6. Plano de Testes

### 6.1 Unitários

- **UT-01 (SC-01):** `response_is_safe("Posso te ajudar com sua consulta. O clareamento custa R$ 800")` → `False`.
- **UT-02 (SC-01):** `response_is_safe("Posso te ajudar apenas com agendamentos.")` → `True`.
- **UT-03 (SC-02):** `classify_patient_message` para "qual a tabela de precos?", "quanto sai?", "ta quanto a consulta?" → `reason == "fora_do_escopo"`.
- **UT-04 (SC-03):** `response_is_safe("o valor da consulta fica em 350")` e `"são 350,00"` → `False`.
- **UT-05 (SC-04):** `classify_patient_message("quero marcar uma limpeza")`, `("preciso agendar avaliação de aparelho")` → `None`.
- **UT-06 (SC-05):** `classify_patient_message` para "estou com pus", "dente trincou e lateja", "abscesso" → `reason == "duvida_clinica"`.
- **UT-07 (SC-06):** `_normalize("p r e c o")` e `_normalize("preçooo")` produzem forma na qual `_PRICE_PATTERNS` casa.
- **UT-08 (AG-05):** `CheckPlanTool._run` de um plano com `restrictions` não inclui "Restrições:"/"NÃO são cobertos" na saída ao paciente.

### 6.2 Integração

- **IT-01 (WE-07):** Simular `receive_message` com `current_state.stage = "reschedule"`/pending e mensagem de escopo; assertar que após a resposta o estado de agenda persiste (`ConversationStateService.get(phone)` mantém `reschedule_event_id`).
- **IT-02 (CO-03):** Simular `receive_message` com `current_state.stage = CONFIRMATION_STAGE` e mensagem ambígua; assertar `JSONResponse` determinístico e que `dental_crew.process_message` NÃO foi chamado (mock).
- **IT-03 (ordem):** Confirmar que confirmação/slot são resolvidos sem serem suprimidos indevidamente pela escalação (ordem em `app.py:206-233`).

### 6.3 Aceitação

- **AT-01:** Executar os 11 critérios CA-001..CA-011 de forma reproduzível (script/pytest), todos verdes.
- **AT-02:** Conjunto de "mensagens legítimas de agendamento" (≥10 exemplos) — 0 escalações (calibração de falso positivo, RNF-003).
- **AT-03:** Conjunto de "tentativas de vazamento" (≥10 exemplos de preço/clínico/ofuscado) — 100% bloqueadas.

### 6.4 Casos de Borda

- **EB-01:** Texto vazio/`None` → `classify_patient_message` `None`, `response_is_safe` `True` (comportamento atual mantido).
- **EB-02:** Mensagem mista "quero marcar, mas quanto custa?" — deve escalar por conter pedido de preço (preço prevalece sobre agendamento).
- **EB-03:** Valor nu legítimo não-monetário ("marcar para as 14") não deve ser confundido com preço — calibrar padrão de valor nu para evitar falso positivo de horário.
- **EB-04:** Plano referral em `CheckPlanTool._run` (`config_tool.py:46-53`) — caminho de encaminhamento mantém o alerta interno, mas paciente recebe só a orientação de encaminhamento.
- **EB-05:** Resposta longa com marcador seguro no início e preço no fim — deve reprovar (garante que a ordem de checagem independe da posição do marcador).

## 7. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Ampliar keywords aumenta falsos positivos e expulsa pacientes | Média | Alto | Conjunto versionado de mensagens legítimas (AT-02) como gate de regressão; ajuste fino do contexto operacional (SC-04) |
| Padrão de "valor nu" (SC-03) confunde horário/quantidade com preço | Média | Médio | EB-03: restringir o padrão a contextos de preço (proximidade de termos de custo) e validar com testes |
| Reordenar handlers em `receive_message` quebra outro fluxo determinístico | Baixa | Alto | IT-01/IT-03 cobrindo confirmação/slot/pending; mudança mínima preferindo "não limpar estado" a reordenar tudo |
| Normalização agressiva (SC-06) altera detecção existente | Baixa | Médio | UT-07 + reexecução de toda a suíte do guard antes do merge |
| Remover detalhe de cobertura (AG-05) quebra alerta interno à doutora | Baixa | Médio | EB-04: separar mensagem-ao-paciente do detalhe-interno; testar caminho referral |
| Guard ainda contornável por ofuscação não prevista | Média | Médio | Documentar limites; manter alerta interno em toda escalação para revisão humana (na dúvida, escalar) |

## 8. Dependências

### 8.1 Internas

- **Implementação 001 — Estabilidade da API e Resiliência de IO:** pré-requisito; garante que o caminho de envio/escalação seja confiável para validar WE-07/CO-03 sem ruído de falhas de I/O.
- **Implementação 002 — Recuperação da Rede de Testes:** pré-requisito; necessária para executar a suíte de regressão (unitários/integração/aceitação) desta spec.
- **Relação com 003 (Robustez do Estado Conversacional):** WE-07 depende do `ConversationStateService` consistente; não é pré-requisito formal, mas mudanças devem ser compatíveis.

### 8.2 Externas

- `ConfigService` (`src/infrastructure/config/config_service.py`) para `escalation.to_patient` e dados de planos consumidos por `config_tool.py` — usado, não modificado.
- `AlertService` (`src/infrastructure/integrations/alert_service.py`) para alerta interno à doutora (`app.py:1297-1306`) — usado, não modificado.
- Bibliotecas padrão `re` e `unicodedata` — já presentes.

## 9. Observações e Decisões de Design

- **Ordem de checagem é a correção central de SC-01.** A inversão (proibido antes de seguro) em `response_is_safe` é de baixo custo e alto retorno; o marcador seguro passa a ser apenas um "ok final" e nunca um override.
- **Guard determinístico por design (RNF-001):** rejeitamos classificador via LLM para não reintroduzir latência/erro de API (queixa nº 1). O preço é cobertura imperfeita contra ofuscação extrema — mitigado por sempre alertar a doutora em escalação (princípio do PRD "na dúvida, escalar").
- **Separação paciente vs. interno (AG-05):** a tool `verificar_convenio` deve produzir uma mensagem segura ao paciente; o detalhe de cobertura/restrição é informação operacional da doutora, não do paciente.
- **WE-07 — preferência por "não limpar" sobre "reordenar tudo":** a alteração de menor risco é condicionar `ConversationStateService.clear(phone)` (`app.py:1331`) à ausência de estado de agenda ativo, mantendo a ordem atual de `receive_message`. A reordenação completa fica como alternativa caso a escalação precise ceder prioridade aos handlers de confirmação/slot.
- **CO-03 — fechar a porta do LLM na confirmação:** trocar o `return None` (`app.py:1284`) por um fallback determinístico elimina a janela de vazamento mais sutil, em que o paciente já está num fluxo guiado e ainda assim a mensagem ambígua escaparia para o LLM.
