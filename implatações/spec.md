# Spec: fluxo de marcacao, remarcacao e cancelamento

## Objetivo

Revisar e corrigir o fluxo da API responsavel por marcacoes, remarcacoes e cancelamentos de consultas no WhatsApp, com foco no problema relatado pelo cliente: ao remarcar, a nova data e criada no Google Calendar, mas a consulta antiga permanece na agenda.

## Problema observado

No fluxo atual de remarcacao, a API pode criar o novo evento antes de garantir que o evento antigo foi cancelado. Se o cancelamento do evento antigo falhar, o paciente pode receber uma confirmacao da nova consulta enquanto a agenda continua com dois horarios ocupados para o mesmo atendimento.

Ponto sensivel identificado:

- `src/interfaces/http/app.py` cria a nova consulta com `CalendarService.create_appointment_if_available`.
- Depois, se `state.intent == "reschedule"` e `state.reschedule_event_id` existir, chama `CalendarService.cancel_appointment`.
- Se `cancel_appointment` retornar `False`, a API dispara alerta interno com motivo `remarcacao_parcial`, mas ainda monta mensagem de confirmacao normal para o paciente e limpa o estado da conversa.

## Escopo

Incluido:

- Mapear o fluxo de marcacao simples.
- Mapear o fluxo de cancelamento simples.
- Mapear o fluxo de remarcacao iniciado por pedido direto do paciente.
- Mapear o fluxo de remarcacao iniciado pela confirmacao proativa da consulta.
- Garantir que remarcacao nao deixe evento antigo ativo quando a nova consulta for criada.
- Definir comportamento em caso de falha parcial.
- Adicionar testes automatizados para sucesso e falha do cancelamento antigo.

Fora de escopo:

- Mudancas no layout do painel administrativo.
- Mudancas de credenciais, deploy ou infraestrutura.
- Alteracao de regras de horarios disponiveis, duracao de consulta ou convenios.
- Refatoracao ampla do agente/LLM.

## Estado atual do fluxo

### Marcacao

1. Paciente pede para marcar consulta.
2. Sistema coleta dados minimos: nome, telefone, convenio/plano quando necessario, data ou periodo.
3. API busca horarios disponiveis no Google Calendar.
4. Horarios oferecidos ficam registrados em `ConversationState.offered_date` e `ConversationState.offered_times`.
5. Paciente escolhe um horario ofertado.
6. API confirma o slot e cria o evento com `create_appointment_if_available`.
7. API registra a interacao e limpa o estado.

Comportamento esperado: criar apenas um evento no Google Calendar, somente em horario previamente ofertado e ainda disponivel.

### Cancelamento

1. Paciente pede para cancelar.
2. API consulta proximas consultas pelo telefone com `find_appointments_by_phone`.
3. Se houver mais de uma consulta futura, o fluxo precisa identificar o `event_id` correto.
4. API pede confirmacao antes de cancelar.
5. Com confirmacao do paciente, chama `cancel_appointment(event_id)`.
6. API registra a interacao e limpa o estado.

Comportamento esperado: cancelar somente o evento correto e nao criar novo evento.

### Remarcacao

1. Paciente pede para remarcar ou responde a confirmacao proativa pedindo remarcacao.
2. API identifica a consulta original e salva `reschedule_event_id` no estado.
3. API busca e oferece novos horarios.
4. Paciente escolhe/confirma o novo horario.
5. API cria o novo evento.
6. API cancela o evento antigo usando `reschedule_event_id`.
7. API registra a interacao como `reschedule` e limpa o estado.

Comportamento esperado: ao final, deve existir somente a nova consulta ativa. A consulta antiga deve ser removida ou cancelada de forma verificavel.

## Requisito principal

A remarcacao deve ser tratada como uma operacao consistente de troca de horario.

Aceite minimo:

- Se a nova consulta for criada e a antiga for cancelada com sucesso, responder ao paciente confirmando a remarcacao.
- Se a nova consulta nao puder ser criada, manter a consulta antiga e informar que o novo horario ficou indisponivel.
- Se a nova consulta for criada mas a antiga nao puder ser cancelada, nao tratar como sucesso silencioso.
- Nesse caso de falha parcial, a API deve:
  - preservar informacao suficiente para intervencao manual;
  - alertar a doutora/admin;
  - informar ao paciente uma mensagem segura, sem afirmar que a remarcacao foi totalmente concluida;
  - evitar limpar o estado de forma que o evento antigo se perca.

## Hipoteses tecnicas

- `reschedule_event_id` e o identificador autoritativo do evento antigo.
- `cancel_appointment` retorna `False` quando o Google Calendar nao confirma a remocao.
- O Google Calendar e a fonte final da agenda.
- O banco SQLite guarda estado e historico, mas nao substitui a verificacao real no Calendar.

## Riscos

- Duplicidade de agenda: antigo e novo eventos ativos ao mesmo tempo.
- Paciente receber mensagem de sucesso quando a agenda ainda exige acao manual.
- Estado de conversa ser limpo apos falha parcial, perdendo `reschedule_event_id`.
- Falhas do Google Calendar serem mascaradas porque `cancel_appointment` captura excecoes e retorna apenas `False`.
- Mais de uma consulta futura para o mesmo telefone pode levar ao cancelamento do evento errado se o fluxo nao fixar o `event_id`.

## Criterios de aceite

- Remarcacao bem-sucedida chama criacao do novo evento e cancelamento do evento antigo correto.
- Quando o cancelamento antigo falha, o teste garante que a resposta nao e a mesma de sucesso total.
- Quando o cancelamento antigo falha, o estado ou registro operacional mantem o `reschedule_event_id` e o novo `event_id`.
- Cancelamento simples continua exigindo confirmacao e nao entra no fluxo de selecao de slot.
- Marcacao simples continua funcionando sem tentar cancelar evento antigo.
- Fluxo de confirmacao proativa preserva `reschedule_event_id` ao iniciar remarcacao.
- Cobertura automatizada contempla sucesso, falha parcial e ausencia de `reschedule_event_id`.

## Cenários de teste recomendados

### Marcacao simples

Dado um paciente com dados validos e horario ofertado, quando ele confirma o slot, entao a API cria um evento e nao chama cancelamento.

### Cancelamento simples

Dado um paciente com uma consulta futura, quando ele pede cancelamento e confirma, entao a API cancela o `event_id` correto e responde sucesso.

### Remarcacao com sucesso

Dado um paciente em estado `intent = reschedule` com `reschedule_event_id = evt-old`, quando ele confirma novo horario, entao a API cria `evt-new`, cancela `evt-old` e responde confirmacao de remarcacao.

### Remarcacao com falha ao cancelar antigo

Dado um paciente em estado `intent = reschedule` com `reschedule_event_id = evt-old`, quando o novo evento e criado mas `cancel_appointment(evt-old)` retorna `False`, entao a API nao deve responder como sucesso total e deve preservar/registrar a falha parcial.

### Remarcacao sem evento original

Dado um paciente que pede remarcacao mas a API nao consegue identificar a consulta original, quando ele escolhe novo horario, entao a API deve pedir confirmacao/identificacao da consulta antiga antes de criar novo evento como remarcacao.

## Observabilidade esperada

Registrar logs ou interacoes suficientes para responder:

- Qual telefone pediu remarcacao.
- Qual era o `event_id` antigo.
- Qual foi o `event_id` novo, se criado.
- Se o cancelamento antigo foi confirmado.
- Se houve alerta para intervencao manual.

## Decisao de consistencia

Estrategia escolhida: Opcao B, manter o novo evento, preservar estado e acionar intervencao manual imediata.

Motivo: evita apagar o novo horario escolhido pelo paciente sem revisao humana. Quando o novo evento for criado, mas o cancelamento do antigo retornar `False`, a API deve:

- manter `reschedule_event_id` e `reschedule_event_label`;
- salvar o novo evento em `ConversationState.metadata.partial_reschedule_new_event_id`;
- salvar o novo horario em `ConversationState.metadata.partial_reschedule_new_slot`;
- salvar o motivo em `ConversationState.metadata.partial_reschedule_reason = "remarcacao_parcial"`;
- alertar a doutora/admin com os dados do evento antigo e do evento novo;
- responder ao paciente sem afirmar que a remarcacao foi totalmente concluida;
- nao limpar o estado enquanto a intervencao manual estiver pendente.
