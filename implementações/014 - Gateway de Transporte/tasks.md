# Tarefas: Gateway de Transporte

> **Implementação:** 014 - Gateway de Transporte
> **Spec:** [spec.md](./spec.md)
> **Progresso:** 0/9 tarefas concluídas (0%)
> **Última atualização:** 2026-06-22

---

## Legenda

- `[ ]` — Pendente
- `[x]` — Concluída
- `[!]` — Bloqueada (ver observação)
- `[-]` — Cancelada

---

## Tarefas

### Fase 1: Preparação e Setup

- [ ] **T-001:** Confirmar baseline verde
  - **Descrição:** Rodar a suíte completa e registrar o número de testes como linha de base.
  - **Arquivos envolvidos:** —
  - **Critério de conclusão:** `pytest -q` = 488/488 verde, registrado.
  - **Dependências:** Nenhuma
  - **Estimativa:** Pequena
  - **Observações:** Baseline já confirmado: 488 passed em 19.87s (2026-06-22).

- [ ] **T-002:** Criar o pacote `transport` e o contrato
  - **Descrição:** Criar `transport/__init__.py` e `transport/gateway.py` com a dataclass
    `InboundMessage(frozen)` e a ABC `MessagingGateway` (`parse_inbound`, `send_text`,
    `send_text_sync`), mais a fábrica `get_gateway()` lendo `TRANSPORT_PROVIDER`.
  - **Arquivos envolvidos:** `src/infrastructure/integrations/transport/__init__.py`,
    `src/infrastructure/integrations/transport/gateway.py`
  - **Critério de conclusão:** Import do pacote funciona; `get_gateway()` retorna o `EvolutionAdapter`.
  - **Dependências:** T-001
  - **Estimativa:** Pequena

### Fase 2: Implementação Core

- [ ] **T-003:** Implementar `EvolutionAdapter.parse_inbound`
  - **Descrição:** Mover (sem reescrever a lógica) `_extract_message_data`, `_resolve_message_phone`,
    `_build_message_data`, `_is_lid_jid`, `_is_whatsapp_jid`, `_get_nested_string` do `app.py` para
    o adapter, retornando `InboundMessage | None`. Normalizar `from_me` para `bool`.
  - **Arquivos envolvidos:** `src/infrastructure/integrations/transport/evolution_adapter.py`,
    `src/interfaces/http/app.py`
  - **Critério de conclusão:** Adapter produz os mesmos campos do dict atual para os formatos cobertos.
  - **Dependências:** T-002
  - **Estimativa:** Média

- [ ] **T-004:** Implementar `EvolutionAdapter.send_text[_sync]`
  - **Descrição:** Delegar ao `WhatsAppService.send_message[_sync]`, preservando `kind` e o registro
    de eco em `OutboundMessageStore`.
  - **Arquivos envolvidos:** `src/infrastructure/integrations/transport/evolution_adapter.py`
  - **Critério de conclusão:** `send_text` chama o `WhatsAppService` e retorna o booleano de entrega.
  - **Dependências:** T-002
  - **Estimativa:** Pequena

- [ ] **T-005:** Religar o `app.py` ao gateway (parsing)
  - **Descrição:** Substituir `_extract_message_data(data)` por `gateway.parse_inbound(payload)` no
    webhook; adaptar o uso dos campos (`InboundMessage` em vez de dict). Remover os helpers migrados.
  - **Arquivos envolvidos:** `src/interfaces/http/app.py`
  - **Critério de conclusão:** Webhook usa o gateway; helpers de parsing ausentes no `app.py`.
  - **Dependências:** T-003
  - **Estimativa:** Média

- [ ] **T-006:** Religar o envio ao gateway
  - **Descrição:** `_send_response` e demais pontos de envio direto passam pelo `gateway.send_text`.
  - **Arquivos envolvidos:** `src/interfaces/http/app.py`
  - **Critério de conclusão:** Nenhum ponto do `app.py` instancia `WhatsAppService` diretamente para envio.
  - **Dependências:** T-004
  - **Estimativa:** Pequena

### Fase 3: Testes e Validação

- [ ] **T-007:** Testes unitários do adapter
  - **Descrição:** Criar `test_transport_gateway.py` cobrindo os formatos de payload, `@lid`,
    `extendedTextMessage`, `fromMe`, payload sem texto (→ `None`), e `get_gateway` por env.
  - **Arquivos envolvidos:** `tests/test_transport_gateway.py`
  - **Critério de conclusão:** Novos testes verdes; cobrem os casos de borda da spec §6.4.
  - **Dependências:** T-003, T-004
  - **Estimativa:** Média

- [ ] **T-008:** Rodar a suíte completa (preservação de comportamento)
  - **Descrição:** Garantir 488/488 (+ novos) verdes; em especial `test_main_webhook` e
    `test_messaging_*` sem alteração de expectativas.
  - **Arquivos envolvidos:** —
  - **Critério de conclusão:** `pytest -q` verde; nenhum teste existente modificado.
  - **Dependências:** T-005, T-006, T-007
  - **Estimativa:** Pequena

### Fase 4: Documentação e Finalização

- [ ] **T-009:** Atualizar status e índice
  - **Descrição:** Marcar critérios de aceitação, mudar status para 🟢 no `spec.md`, atualizar
    `implementações/README.md` e adicionar `.env.example` (`TRANSPORT_PROVIDER=evolution`).
  - **Arquivos envolvidos:** `implementações/014 - Gateway de Transporte/spec.md`,
    `implementações/README.md`, `.env.example`
  - **Critério de conclusão:** Índice e spec refletem a conclusão; commit na branch `refactor/nucleo-conversa`.
  - **Dependências:** T-008
  - **Estimativa:** Pequena

---

## Registro de Progresso

| Tarefa | Status | Data de Conclusão | Observações |
|--------|--------|-------------------|-------------|
| T-001  | ⬜ Pendente | — | Baseline 488/488 já confirmado |
| T-002  | ⬜ Pendente | — | — |
| T-003  | ⬜ Pendente | — | — |
| T-004  | ⬜ Pendente | — | — |
| T-005  | ⬜ Pendente | — | — |
| T-006  | ⬜ Pendente | — | — |
| T-007  | ⬜ Pendente | — | — |
| T-008  | ⬜ Pendente | — | — |
| T-009  | ⬜ Pendente | — | — |

---

> **📌 NOTA:** Atualize este documento conforme as tarefas forem concluídas.
