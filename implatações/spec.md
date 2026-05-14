# Implantacao: Painel administrativo web

## Objetivo

Criar uma interface web simples para gerenciar a API do WPP-DENTAL sem depender de um frontend separado.

## Escopo

- Servir um painel em `/admin`.
- Exibir status geral da API e metricas do banco SQLite.
- Visualizar conversas recentes, mensagens e interacoes registradas.
- Visualizar proximas marcacoes do Google Calendar.
- Visualizar falhas de processamento e confirmacoes de consulta.
- Criar e remover bloqueios de dias no Google Calendar.

## Autenticacao

Os endpoints administrativos aceitam `ADMIN_API_KEY`.
Se ela nao existir, usam `WEBHOOK_API_KEY` ou `EVOLUTION_WEBHOOK_API_KEY`.
Em ambiente sem chave configurada, o painel fica aberto para facilitar desenvolvimento local.

## Bloqueio de agenda

O bloqueio de dia cria um evento de dia inteiro no Google Calendar com marcador privado `wpp_dental_day_block`.
Como a regra de slots ja considera evento de dia inteiro como agenda bloqueada, o agente deixa de oferecer horarios nessa data.

## Rotas criadas

- `GET /admin`
- `GET /admin/api/summary`
- `GET /admin/api/conversations`
- `GET /admin/api/conversations/{phone}`
- `GET /admin/api/errors`
- `GET /admin/api/appointments`
- `GET /admin/api/blocks`
- `POST /admin/api/blocks`
- `DELETE /admin/api/blocks/{event_id}`

## Fora de escopo nesta implantacao

- Login com usuario e senha.
- Edicao manual de mensagens.
- Reenvio automatico de webhooks com falha.
- Auditoria detalhada de acoes administrativas.
