# Rasa CALM Migration

Esta pasta contem a base da migracao conversacional do WPP-DENTAL para Rasa CALM.

## O que entra no Rasa

- perguntas informativas sobre convenio
- perguntas informativas sobre endereco
- perguntas operacionais sobre procedimentos com regra fixa
- encaminhamento objetivo para Dra. Tarcilia
- rephrase das respostas para soar mais natural

## O que continua no backend atual

- webhook da Evolution
- handoff manual
- Google Calendar
- banco SQLite
- agendamento, remarcacao, cancelamento e consulta de agenda via workflow legado

## Variaveis necessarias

- `OPENAI_API_KEY`
- `RASA_PRO_LICENSE`
- `RASA_OPENAI_MODEL`

## Instalar

1. Instale o Rasa Pro conforme a documentacao oficial e exporte `RASA_PRO_LICENSE`.
2. Use esta pasta como projeto do assistente CALM.
3. Aponte o backend atual para o Rasa com `CONVERSATION_ENGINE=rasa` e `RASA_ASSISTANT_URL=http://host-do-rasa:5005`.

## Treinar e subir

Exemplo de fluxo:

```bash
rasa train --config rasa_assistant/config.yml --domain rasa_assistant/domain.yml --data rasa_assistant/flows.yml
rasa run --enable-api --credentials rasa_assistant/credentials.yml --endpoints rasa_assistant/endpoints.yml
```

## Observacao

O fluxo de agendamento ainda usa `action_handle_legacy_turn` como fallback controlado. Isso reduz risco na migracao enquanto os fluxos 100% CALM sao amadurecidos.
