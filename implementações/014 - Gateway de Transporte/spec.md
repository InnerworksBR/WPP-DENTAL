# Gateway de Transporte

> **ID:** 014
> **Status:** 🔵 Em Andamento
> **Prioridade:** 🟠 Alta
> **Criada em:** 2026-06-22
> **Última atualização:** 2026-06-22
> **Autor:** Agente AI

---

## 1. Resumo Executivo

Isola toda a integração com o WhatsApp (hoje Evolution API) atrás de uma interface única
`MessagingGateway`, com um `EvolutionAdapter` concreto. O parsing do webhook (espalhado no
`app.py`) e o envio (no `WhatsAppService`) passam a viver num único pacote de transporte,
selecionável por variável de ambiente. É uma extração **preservadora de comportamento**: nenhuma
regra de conversa muda — o objetivo é tornar o transporte trocável (futuro WAHA) e tirar ~250
linhas de plumbing do `app.py`, abrindo caminho para as implementações 015–017.

## 2. Contexto e Motivação

### 2.1 Problema Atual
As esquisitices da Evolution v2 estão espalhadas e acopladas ao orquestrador:
- Parsing de payload e resolução de telefone vivem no `app.py`: `_extract_message_data`
  (`app.py:443`), `_resolve_message_phone` (`app.py:481`), `_build_message_data` (`app.py:512`),
  `_is_lid_jid` (`app.py:463`), `_is_whatsapp_jid` (`app.py:467`), `_get_nested_string`
  (`app.py:472`).
- Envio e formatação de telefone vivem no `WhatsAppService` (`whatsapp_service.py`), com a URL
  `/message/sendText/{instance}` e headers `apikey` hardcoded ao formato Evolution.
- O controlador HTTP conhece detalhes do provedor (campos `key.remoteJid`, `pushName`, `@lid`),
  o que torna qualquer troca de provedor uma cirurgia no coração do `app.py`.

### 2.2 Impacto do Problema
Trocar ou complementar o provedor de WhatsApp (a dor relatada com a Evolution) exige mexer no
arquivo mais crítico e maior do sistema (`app.py`, 2.256 linhas), com alto risco de regressão.
O acoplamento também impede testar o orquestrador sem simular o formato cru da Evolution.

### 2.3 Soluções Consideradas
| Solução | Prós | Contras | Decisão |
|---|---|---|---|
| Interface `MessagingGateway` + adapter por provedor | Troca = 1 adapter + env; testável; isola quirks | Uma camada a mais | ✅ Escolhida |
| Trocar Evolution direto por WAHA no código atual | Sem nova camada | Repete o acoplamento; refaz tudo no próximo provedor | ❌ Descartada |
| Manter tudo como está | Zero esforço | Mantém a dor; bloqueia 015–017 | ❌ Descartada |

## 3. Especificação Técnica

### 3.1 Visão Geral da Arquitetura
Cria o pacote `src/infrastructure/integrations/transport/`. A `MessagingGateway` define o contrato
mínimo: **parsear** um payload de webhook num `InboundMessage` neutro e **enviar** texto. O
`EvolutionAdapter` implementa esse contrato absorvendo o parsing do `app.py` e delegando o envio
ao `WhatsAppService` existente (sem reescrever a lógica de retry/formatação nesta fase). Uma
fábrica `get_gateway()` resolve o adapter por `TRANSPORT_PROVIDER` (default `evolution`).

```
app.py (webhook)
   │  payload cru
   ▼
get_gateway() ──▶ EvolutionAdapter
   │                 ├─ parse_inbound(payload) -> InboundMessage | None
   │                 └─ send_text(phone, text, kind) -> bool  ──▶ WhatsAppService (envio atual)
   ▼
InboundMessage (phone, text, contact_name, message_id, from_me)
```

### 3.2 Componentes Afetados
| Componente | Tipo | Ação | Descrição |
|---|---|---|---|
| `src/infrastructure/integrations/transport/__init__.py` | Arquivo | Criar | Exporta `MessagingGateway`, `InboundMessage`, `get_gateway` |
| `src/infrastructure/integrations/transport/gateway.py` | Arquivo | Criar | `InboundMessage` (dataclass) + `MessagingGateway` (ABC/Protocol) + `get_gateway()` |
| `src/infrastructure/integrations/transport/evolution_adapter.py` | Arquivo | Criar | `EvolutionAdapter`: parsing (movido do `app.py`) + envio (delega ao `WhatsAppService`) |
| `src/interfaces/http/app.py` | Arquivo | Modificar | Webhook usa `gateway.parse_inbound`; `_send_response` usa `gateway.send_text`; remove helpers de parsing migrados |
| `src/infrastructure/integrations/whatsapp_service.py` | Arquivo | Manter | Continua sendo o motor de envio, agora chamado pelo adapter |
| `tests/test_transport_gateway.py` | Arquivo | Criar | Testes unitários do adapter (parsing + roteamento de envio) |

### 3.3 Interfaces e Contratos

#### Entradas
- `parse_inbound(payload: dict) -> InboundMessage | None`: recebe o JSON cru do webhook; retorna
  `None` quando não é texto recebido/relevante (mesma semântica do `_extract_message_data` atual).
- `send_text(phone: str, message: str, kind: str = "bot") -> bool` (async) e
  `send_text_sync(...)`: mesma assinatura efetiva do `WhatsAppService.send_message[_sync]`.

#### Saídas
- `InboundMessage`: `phone`, `text`, `contact_name`, `message_id`, `from_me: bool`.
- `send_text`: `True`/`False` (entrega), idêntico ao atual.

#### Contratos de API (se aplicável)
N/A — não muda contrato HTTP externo; o endpoint `/webhook/message` permanece igual.

### 3.4 Modelos de Dados (se aplicável)
`InboundMessage` é um `@dataclass(frozen=True)` puro de transporte — sem persistência. Substitui o
`dict[str, str]` retornado hoje por `_build_message_data` (chaves `phone`, `text`, `contact_name`,
`message_id`, `from_me`), mantendo os mesmos campos.

### 3.5 Fluxo de Execução
1. `receive_message` recebe o payload e (se auth aplicável) autentica — **inalterado**.
2. Em vez de `_extract_message_data(data)`, chama `gateway.parse_inbound(payload)`.
3. Se `None` → retorna `ignored` (mesma resposta de hoje).
4. Segue o fluxo atual (claim de idempotência, handoff, estados, orquestração) usando os campos do
   `InboundMessage` — **inalterado**.
5. Respostas saem por `gateway.send_text` (que chama o `WhatsAppService`).

### 3.6 Tratamento de Erros
- `parse_inbound` nunca levanta para payload malformado: retorna `None` (espelha o comportamento
  defensivo atual de `_extract_message_data`).
- `send_text` mantém o retry/exponencial e o retorno booleano do `WhatsAppService`; falha continua
  virando `502` no webhook como hoje.
- `get_gateway()` com `TRANSPORT_PROVIDER` desconhecido: faz fallback para `evolution` e loga
  `warning` (não derruba a aplicação).

## 4. Requisitos

### 4.1 Requisitos Funcionais
- **RF-001:** Existe `MessagingGateway` com `parse_inbound` e `send_text`/`send_text_sync`.
- **RF-002:** `EvolutionAdapter.parse_inbound` produz exatamente os mesmos campos que
  `_build_message_data` produz hoje (incluindo resolução de `@lid` e seleção de JID real).
- **RF-003:** O `app.py` não contém mais lógica de parsing específica da Evolution (helpers
  migrados ou delegados ao adapter).
- **RF-004:** `get_gateway()` seleciona o adapter por `TRANSPORT_PROVIDER` (default `evolution`).
- **RF-005:** O envio de mensagens passa pelo gateway, preservando registro de eco
  (`OutboundMessageStore`) e `kind`.

### 4.2 Requisitos Não-Funcionais
- **RNF-001:** Zero mudança de comportamento observável (mesmas respostas HTTP e mesmas mensagens).
- **RNF-002:** A suíte completa permanece verde (488/488) ao fim da implementação.
- **RNF-003:** Adicionar um novo provedor deve exigir apenas um novo arquivo de adapter + valor de
  env, sem tocar `app.py`.

### 4.3 Restrições e Limitações
- Não reescrever a lógica de envio/retry do `WhatsAppService` nesta fase (apenas encapsular).
- Não alterar o esquema do webhook nem a autenticação (`WEBHOOK_AUTH_DISABLED` permanece).

## 5. Critérios de Aceitação
- [ ] **CA-001:** `gateway.parse_inbound` cobre todos os formatos hoje tratados por
  `_extract_message_data` (data dict único, lista de `messages`, parent+message merge, `@lid`).
- [ ] **CA-002:** `app.py` usa o gateway para parsing e envio; helpers de parsing da Evolution
  não existem mais no `app.py`.
- [ ] **CA-003:** `TRANSPORT_PROVIDER=evolution` (e ausente) usa o `EvolutionAdapter`.
- [ ] **CA-004:** `pytest -q` retorna 488/488 (ou mais, com os novos testes do adapter).
- [ ] **CA-005:** Novos testes do adapter cobrem parsing de `@lid`, texto simples, `extendedText`,
  `fromMe`, e payload sem texto (→ `None`).

## 6. Plano de Testes

### 6.1 Testes Unitários
`test_transport_gateway.py`: `parse_inbound` para cada formato de payload; `get_gateway` retornando
o adapter certo por env; `send_text` delegando ao `WhatsAppService` (mock) e registrando eco.

### 6.2 Testes de Integração
`test_main_webhook.py` (40 testes) roda **sem alteração de expectativas** — prova de preservação de
comportamento ponta-a-ponta do webhook.

### 6.3 Testes de Aceitação
Suíte completa verde (488/488) + CA-002 verificado por busca textual (helpers ausentes no `app.py`).

### 6.4 Casos de Borda (Edge Cases)
- Payload com `data` como lista vs dict.
- `remoteJid` `@lid` com e sem `participant` real.
- Mensagem sem corpo de texto (`None`).
- `fromMe=true` (eco/handoff manual) — roteado igual ao atual.

## 7. Riscos e Mitigações
| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Divergência sutil no parsing ao mover helpers | Média | Alto | Mover funções **sem reescrever**; `test_main_webhook` como catraca |
| Import circular (`app` ↔ transport ↔ whatsapp_service) | Baixa | Médio | Gateway depende só de `whatsapp_service`; `app` depende do gateway (sentido único) |
| Eco/handoff quebrar por mudança no envio | Baixa | Alto | Manter `OutboundMessageStore.record` dentro do `WhatsAppService` (inalterado) |

## 8. Dependências

### 8.1 Dependências Internas
- Implementações 001–013 concluídas (base estável e suíte viva).
- Nenhuma implementação pré-requisito além do baseline verde.

### 8.2 Dependências Externas
- Evolution API (inalterada). Nenhuma nova biblioteca.

## 9. Observações e Decisões de Design
- O adapter **delega** o envio ao `WhatsAppService` em vez de absorvê-lo agora, para manter a fase
  pequena e preservadora. A absorção completa (se desejada) pode ocorrer numa fase futura.
- `InboundMessage` é o ponto de desacoplamento: as fases 015–017 consomem `InboundMessage`, nunca o
  payload cru da Evolution. Isso é o que torna o WAHA (ou Cloud API) um adapter isolado depois.
- Decisão de manter `from_me` como `bool` no `InboundMessage` (hoje é string `"1"/"0"` no dict) —
  normalização barata que simplifica o consumidor.

---

> **⚠️ NOTA:** Este documento é a fonte de verdade para esta implementação.
> Qualquer alteração no escopo deve ser refletida aqui ANTES de ser implementada.
