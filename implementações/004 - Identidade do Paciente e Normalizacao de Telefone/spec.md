# Identidade do Paciente e Normalizacao de Telefone

> **ID:** 004
> **Status:** 🟢 Concluída
> **Prioridade:** 🔴 Critica
> **Criada em:** 2026-06-15
> **Última atualização:** 2026-06-15
> **Autor:** Agente AI

---

## 1. Resumo Executivo

O telefone e o identificador principal do paciente no WPP-DENTAL, mas hoje a normalizacao e a busca sao inconsistentes de ponta a ponta. O mesmo paciente pode gerar identificadores diferentes por causa do 9o digito do celular brasileiro, a busca por `LIKE %termo%` casa pacientes errados por colisao de substring, e a busca de consultas no Google Calendar (`find_appointments_by_phone`) casa eventos de OUTRO paciente por `endswith` cruzado. Alem disso, os cadastros sao destrutivos: `PatientService.upsert` e `SavePatientTool._run` sobrescrevem o nome bom por nome vazio/placeholder e a tool chega a ZERAR o plano do paciente.

Essa implementacao introduz uma **forma canonica unica de telefone brasileiro** (com tratamento explicito do 9o digito), troca a busca por substring por **match exato pelo telefone canonico**, corrige o casamento cruzado de eventos do Calendar e torna os upserts **nao-destrutivos** (merge). O resultado direto e atacar as queixas (2) "responde errado", (4) "marca errado e traz transtorno" e (1) "API toda hora da erro", eliminando pacientes duplicados/trocados e eventos duplicados em remarcacao.

## 2. Contexto e Motivação

### 2.1 Problema Atual

A camada de telefone vive em `src/domain/policies/phone_service.py` e e consumida por toda a aplicacao (confirmado via grep): `patient_service.py`, `patient_tool.py`, `calendar_service.py`, `connection.py`, `outbound_message_store.py` e `app.py`. Os defeitos concretos lidos no codigo:

- **9o digito nao tratado canonicamente.** `normalize_internal_phone` (`phone_service.py:26-36`) apenas remove o prefixo `55` e corta para 11 digitos (`digits[-11:]`), mas NAO reconcilia celular com e sem o 9o digito. Assim `11987654321` (com 9) e `1187654321` (sem 9) viram identificadores diferentes para o mesmo paciente. `build_phone_search_term` (`phone_service.py:39-42`) so faz `normalized[-11:]`, propagando o problema.
- **Busca por substring (`LIKE %termo%`).** `PatientService.find_by_phone` (`patient_service.py:15-30`) e `PatientService.upsert` (`patient_service.py:47-50`) usam `WHERE phone LIKE ?` com `(f"%{search_term}%",)`. `FindPatientTool._run` (`patient_tool.py:30-35`), `SavePatientTool._run` (`patient_tool.py:72-76`) e `SaveInteractionTool._run` (`patient_tool.py:117-123`) fazem o mesmo. Um numero sem DDD (`87654321`) casa por substring com qualquer paciente cujo telefone contenha essa sequencia, identificando paciente ERRADO.
- **Casamento cruzado de eventos no Calendar.** `find_appointments_by_phone` (`calendar_service.py:595-627`) considera match quando `summary_digits.endswith(search_term)` OU `phone_digits.endswith(summary_digits)` (`calendar_service.py:620-624`). O `endswith` mutuo casa o evento de outro paciente cujo final do numero coincide, devolvendo a consulta errada para cancelar/remarcar.
- **Prefixo "55" cego.** `normalize_conversation_phone` (`phone_service.py:20-21`) faz `if not digits.startswith("55") and len(digits) in (10, 11): digits = f"55{digits}"`, prefixando "55" em QUALQUER numero de 10/11 digitos, o que corrompe numeros nao-BR.
- **JIDs nao-telefone aceitos como telefone.** `normalize_conversation_phone` extrai a parte local de qualquer JID (`phone_service.py:15`) e devolve digitos; JIDs de grupo (`@g.us`), LID (`@lid`) ou numeros curtos passam como se fossem telefone valido. O `app.py` ja distingue `@s.whatsapp.net`/`@c.us` (`_is_whatsapp_jid`, `app.py:337-339`) e `@lid` (`_is_lid_jid`, `app.py:333-334`), mas `phone_service.py` em si nao valida.
- **Upsert destrutivo (nome).** `PatientService.upsert` (`patient_service.py:40-64`) faz `patient_name = (name or "").strip()` e grava esse valor mesmo quando vazio/placeholder, sobrescrevendo um nome bom existente. `_build_patient_name`/`_save_patient_if_missing` (`app.py:498-512`) pode chamar upsert com o proprio telefone como "nome" (`app.py:506`).
- **Tool destrutiva (nome + plano).** `SavePatientTool._run` (`patient_tool.py:78-85`) no UPDATE grava `name` e `plan` diretamente: se a chamada vier sem plano, `plan=None` ZERA o plano existente do paciente; e o nome e sobrescrito sem checar se e vazio/placeholder.

### 2.2 Impacto do Problema

| Queixa do dono | Como este defeito a causa |
|---|---|
| (2) Responde errado aos clientes | `find_by_phone` por substring devolve dados de outro paciente; o assistente cumprimenta/trata o cliente pelo nome errado e usa o convenio errado. |
| (4) Marca errado e traz transtorno | 9o digito divergente faz a remarcacao nao achar o evento existente e criar um DUPLICADO; `find_appointments_by_phone` casa o evento de outro paciente, levando a cancelar/remarcar a consulta errada. Viola a regra de remarcacao consistente (so 1 evento ativo ao final). |
| (1) API toda hora da erro | Prefixo "55" cego e JID nao-telefone produzem termos de busca invalidos, gerando comportamento erratico nas chamadas ao Calendar e ao banco. |
| Perda de dados de cadastro | Upsert/tool sobrescrevem nome bom por vazio e zeram o plano, exigindo recoleta e reagravando o transtorno. |

### 2.3 Soluções Consideradas

| Solução | Prós | Contras | Decisão |
|---|---|---|---|
| Forma canonica unica de telefone BR (com/sem 9o digito reconciliado) + match exato + merge nao-destrutivo | Resolve raiz de todos os findings; ponto unico de verdade em `phone_service.py`; baixo risco de regressao se mantida a assinatura das funcoes | Exige migracao/reconciliacao dos dados legados ja gravados | **ESCOLHIDA** |
| Manter `LIKE %termo%` e so adicionar o 9o digito | Mudanca minima | Nao elimina colisao por substring (PH-02); paciente errado continua possivel | Rejeitada |
| Trocar o identificador de telefone para um UUID interno | Identidade estavel independente de formato | Reescrita ampla; telefone deixa de ser chave; fora do escopo desta correcao | Rejeitada |
| Comparar telefones por `endswith` de N digitos fixos | Simples | Mantem casamento cruzado entre numeros com final igual (PH-03) | Rejeitada |

## 3. Especificação Técnica

### 3.1 Visão Geral da Arquitetura

A correcao concentra a logica de identidade no modulo de dominio `src/domain/policies/phone_service.py`, que ja e a fonte unica consumida por todas as camadas. Introduz-se a funcao canonica `canonical_phone(value) -> str` (forma interna BR sem 9o digito ambiguo) e `is_valid_phone(value) -> bool`. As camadas de aplicacao/interface/infra passam a comparar telefones pela forma canonica (igualdade), em vez de `LIKE %...%` e `endswith`. Os upserts passam por uma rotina de merge que so escreve campos validos.

### 3.2 Componentes Afetados

| Componente | Tipo | Ação | Descrição |
|---|---|---|---|
| `src/domain/policies/phone_service.py` | Dominio | Modificar | Adicionar `canonical_phone`, `is_valid_phone`, `phones_match`; corrigir `normalize_conversation_phone` (PH-04/PH-05); manter `normalize_internal_phone`/`build_phone_search_term` compativeis. |
| `src/application/services/patient_service.py` | Aplicacao | Modificar | `find_by_phone` e `upsert` por match exato canonico; `upsert` nao-destrutivo (PA-01). |
| `src/interfaces/tools/patient_tool.py` | Interface | Modificar | `FindPatientTool`/`SavePatientTool`/`SaveInteractionTool` por match exato; `SavePatientTool` merge nao-destrutivo de nome+plano (PA-02). |
| `src/infrastructure/integrations/calendar_service.py` | Infra | Modificar | `find_appointments_by_phone` (595-627) comparar por telefone canonico, sem `endswith` cruzado (PH-03). |
| `src/domain/policies/__init__.py` | Dominio | Modificar | Exportar os novos simbolos publicos. |
| `tests/` | Testes | Criar | Suites de regressao para PH-01..PH-05, PA-01, PA-02. |

### 3.3 Interfaces e Contratos

Novas/ajustadas funcoes publicas em `phone_service.py`:

- `canonical_phone(value: str) -> str` — recebe telefone/JID e devolve a forma canonica BR (DDD + numero, SEM o 9o digito de celular usado como chave de identidade), ou `""` se invalido. E a chave usada para comparar pacientes e eventos.
- `is_valid_phone(value: str) -> bool` — `True` apenas para telefones BR plausiveis (10/11 digitos locais ou 12/13 com `55`); rejeita JID de grupo/`@lid` e numeros curtos.
- `phones_match(a: str, b: str) -> bool` — `canonical_phone(a) == canonical_phone(b)` com ambos nao-vazios.
- `normalize_conversation_phone(value)` — passa a prefixar `55` apenas para numeros BR validados (nao mais qualquer 10/11 digitos).

Contrato de busca/persistencia: consultas ao banco devem usar igualdade pela coluna `phone` ja normalizada (UNIQUE NOT NULL, ver 3.4), e nao `LIKE`. Quando legados ainda divergirem, comparar em memoria por `phones_match`.

### 3.4 Modelos de Dados

Tabela `patients` (definida em `connection.py:14-21`): `id` (PK), `phone TEXT UNIQUE NOT NULL`, `name TEXT NOT NULL`, `plan TEXT`, `created_at`, `updated_at`. Nenhuma mudanca de schema e necessaria — o `phone` ja e UNIQUE; passamos a garantir que ele guarde sempre a forma canonica. A rotina legada `_normalize_patient_phone_rows` (`connection.py:113+`) ja deduplica por `normalize_internal_phone`; ela deve passar a agrupar por `canonical_phone` para reconciliar o 9o digito.

### 3.5 Fluxo de Execução

1. Webhook chega no `app.py`; `_resolve_message_phone` (`app.py:351-379`) ja seleciona JID valido e trata LID; o telefone resultante e canonizado.
2. `FindPatientTool`/`PatientService.find_by_phone` buscam o paciente por **igualdade canonica**; se ausente, "paciente novo".
3. Ao salvar, `upsert`/`SavePatientTool` localizam o existente por igualdade canonica e fazem **merge**: nao sobrescrevem nome bom por vazio/placeholder; nao zeram plano quando a chamada nao traz plano.
4. Em remarcacao/cancelamento, `find_appointments_by_phone` filtra eventos por `phones_match(summary_phone, phone)`, garantindo que so o evento do paciente correto seja afetado (regra: ao final 1 evento ativo).

### 3.6 Tratamento de Erros

- Telefone invalido (`is_valid_phone` falso): `find_by_phone` retorna `None`/"paciente novo"; `upsert` nao grava registro lixo; em fluxo de agendamento, escalar (regra "na duvida escalar").
- JID de grupo/`@lid`/curto: `canonical_phone` devolve `""`; nenhuma operacao de banco/Calendar e disparada com termo invalido.
- Colisao residual em dados legados: comparar em memoria por `phones_match` antes de decidir match; se ainda ambiguo, nao agir destrutivamente e escalar.

## 4. Requisitos

### 4.1 Requisitos Funcionais

- **RF-001 (PH-01):** O sistema DEVE produzir uma forma canonica unica de telefone BR que reconcilie a presenca/ausencia do 9o digito, usada de ponta a ponta para identificar paciente e eventos.
- **RF-002 (PH-02):** A busca de paciente DEVE usar match exato pelo telefone canonico, eliminando casamento por substring (`LIKE %...%`).
- **RF-003 (PH-03):** `find_appointments_by_phone` DEVE casar eventos por telefone canonico, sem `endswith` cruzado entre numero buscado e summary.
- **RF-004 (PH-04):** `normalize_conversation_phone` NAO DEVE prefixar `55` em numeros nao-BR; o prefixo so se aplica a numeros BR validados.
- **RF-005 (PH-05):** A normalizacao DEVE rejeitar JIDs de grupo/`@lid`/numeros curtos como telefone valido (`is_valid_phone`).
- **RF-006 (PA-01):** `PatientService.upsert` NAO DEVE sobrescrever um nome existente valido por nome vazio/placeholder (ex.: o proprio telefone).
- **RF-007 (PA-02):** `SavePatientTool` DEVE fazer merge nao-destrutivo: NAO zerar o plano existente quando a chamada nao traz plano e NAO sobrescrever nome bom por vazio/placeholder.

### 4.2 Não-Funcionais

- **RNF-001:** A forma canonica DEVE ser deterministica e idempotente (`canonical_phone(canonical_phone(x)) == canonical_phone(x)`).
- **RNF-002:** As assinaturas publicas existentes (`normalize_internal_phone`, `build_phone_search_term`) DEVEM permanecer compativeis para nao quebrar os 6 modulos consumidores listados no grep.
- **RNF-003:** Nenhuma migracao de schema; reconciliacao de dados legados executada de forma idempotente na inicializacao (reuso de `_normalize_patient_phone_rows`).

### 4.3 Restrições

- Escopo exclusivo agenda; nada de preco/clinico (PRD).
- Telefone permanece como identificador principal; sem introducao de novo identificador (UUID) nesta implementacao.
- Convenios referral nunca sao agendados (PRD) — fora do alvo desta spec, mas o cadastro deve preservar o plano para que essa regra continue avaliavel.

## 5. Critérios de Aceitação

- [ ] **CA-001 (RF-001):** `canonical_phone("5511987654321")`, `canonical_phone("11987654321")` e `canonical_phone("1187654321")` produzem a MESMA chave para o mesmo paciente.
- [ ] **CA-002 (RF-001):** Remarcacao de um paciente cujo numero foi salvo sem 9o digito e agora chega com 9o digito NAO cria evento duplicado.
- [ ] **CA-003 (RF-002):** Buscar paciente por `87654321` (sem DDD) NAO retorna paciente cujo telefone apenas contem essa substring.
- [ ] **CA-004 (RF-003):** `find_appointments_by_phone` para o paciente A NAO retorna evento do paciente B cujo final do numero coincide.
- [ ] **CA-005 (RF-004):** `normalize_conversation_phone` de um numero estrangeiro (ex.: 10 digitos nao-BR) NAO recebe prefixo `55`.
- [ ] **CA-006 (RF-005):** `is_valid_phone` retorna `False` para `123456@g.us`, `999@lid` e numeros com menos de 10 digitos; `canonical_phone` desses retorna `""`.
- [ ] **CA-007 (RF-006):** `PatientService.upsert(phone, name="")` sobre paciente existente com nome "Maria" mantem "Maria".
- [ ] **CA-008 (RF-006):** `upsert(phone, name="<telefone>")` (placeholder) NAO substitui um nome valido existente.
- [ ] **CA-009 (RF-007):** `SavePatientTool._run(phone, name, plan=None)` sobre paciente com plano "Unimed" mantem "Unimed".
- [ ] **CA-010 (RF-007):** `SavePatientTool._run(phone, name="", ...)` NAO apaga o nome existente valido.
- [ ] **CA-011 (RNF-002):** Os 6 modulos consumidores continuam importando e funcionando sem alteracao de assinatura.

## 6. Plano de Testes

### 6.1 Unitários

- `phone_service`: `canonical_phone` com/sem 9o digito, com/sem `55`, idempotencia (RNF-001); `is_valid_phone` para BR/nao-BR/JID grupo/LID/curto; `phones_match` simetrico; `normalize_conversation_phone` sem `55` cego.
- `PatientService.upsert`: merge de nome (vazio/placeholder/valido) e plano.

### 6.2 Integração

- `find_by_phone` + `upsert` contra SQLite real: inserir paciente, buscar pelas tres variacoes do numero e confirmar registro unico.
- `find_appointments_by_phone` com eventos forjados (paciente A e B com finais coincidentes) garantindo isolamento.
- `_normalize_patient_phone_rows` reconciliando dois registros legados (com e sem 9o digito) em um so.

### 6.3 Aceitação

- Executar e validar CA-001..CA-011 como casos de teste nomeados.

### 6.4 Casos de Borda

- Numero com `55` repetido / DDD ausente / so digitos do 9o.
- Summary de evento sem telefone ou com telefone em formato alternativo (`_extract_patient_phone_from_event`, `calendar_service.py:196-210`).
- Chamada de upsert concorrente (mesmo paciente) preservando UNIQUE.
- `name` contendo apenas `+` e digitos (placeholder), espelhando o teste de `app.py:923`.

## 7. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Reconciliacao do 9o digito unir indevidamente dois pacientes distintos com numeros muito proximos | Baixa | Alto | Reconciliar apenas variacao do 9o digito do MESMO DDD+raiz; testes de borda; logar uacoes de merge. |
| Mudar `LIKE` para igualdade nao achar legados ainda nao normalizados | Media | Medio | Garantir `_normalize_patient_phone_rows` por `canonical_phone` na inicializacao; fallback de comparacao em memoria por `phones_match`. |
| Quebrar consumidores por mudanca de assinatura | Baixa | Alto | Manter assinaturas atuais (RNF-002); adicionar funcoes novas em vez de alterar contrato. |
| Falso negativo em `is_valid_phone` rejeitando numero BR legitimo | Baixa | Medio | Cobrir faixas 10/11/12/13 digitos; testes unitarios dedicados. |

## 8. Dependências

### 8.1 Internas

- **Implementacao 001** (pre-requisito) — base de fluxo/observabilidade necessaria para validar a identidade ponta-a-ponta.
- **Implementacao 002 — Recuperação da Rede de Testes** (pre-requisito) — suíte verde para validar a normalização de telefone/identidade sem regressão (`find_by_phone`/`resolve_name`).

### 8.2 Externas

- Google Calendar API (via `calendar_service.py`) — alvo de `find_appointments_by_phone`.
- SQLite (tabela `patients`, `connection.py`).
- Evolution API (origem dos JIDs tratados em `app.py:351-379`).

## 9. Observações e Decisões de Design

- A decisao de manter o telefone como chave (e nao migrar para UUID) e deliberada: minimiza blast radius e respeita o PRD que define telefone como identificador. A canonizacao resolve a ambiguidade sem trocar a chave.
- Optou-se por concentrar toda a logica em `phone_service.py` para ter um unico ponto de verdade; os 6 consumidores ja importam desse modulo (confirmado por grep), entao a correcao propaga automaticamente.
- N/A — Nenhuma alteracao de schema do banco e necessaria, pois `phone` ja e `UNIQUE NOT NULL` e a infra de migracao de colunas (`_ensure_column`) nao precisa ser tocada.
