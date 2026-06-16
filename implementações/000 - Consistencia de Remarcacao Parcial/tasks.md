# Tasks: corrigir fluxo de marcacao, remarcacao e cancelamento

## 1. Diagnostico da causa raiz

1.1. [x] Localizar o fluxo deterministico que confirma horario ofertado.

- Arquivo: `src/interfaces/http/app.py`
- Funcao: `_handle_offered_slot_selection`
- Resultado esperado: confirmar onde a API cria novo evento quando o paciente responde afirmativamente ao horario.

1.2. [x] Localizar o ponto exato da remarcacao parcial.

- Arquivo: `src/interfaces/http/app.py`
- Trecho critico: depois de `create_appointment_if_available`, o codigo chama `cancel_appointment(state.reschedule_event_id)`.
- Resultado esperado: confirmar que a criacao do novo evento acontece antes da remocao garantida do evento antigo.

1.3. [x] Confirmar como o evento antigo e guardado no estado.

- Arquivo: `src/application/services/conversation_state_service.py`
- Campos: `reschedule_event_id`, `reschedule_event_label`, `pending_event_id`, `pending_event_label`, `metadata`
- Resultado esperado: saber qual campo deve ser tratado como fonte autoritativa para cancelar a consulta antiga.

1.4. [x] Confirmar como a confirmacao proativa inicia remarcacao.

- Arquivo: `src/application/services/appointment_confirmation_service.py`
- Funcao: `_build_confirmation_state`
- Resultado esperado: confirmar que a rotina de confirmacao salva `reschedule_event_id` com o `event_id` original.

1.5. [x] Confirmar comportamento de falha do cancelamento no Calendar.

- Arquivo: `src/infrastructure/integrations/calendar_service.py`
- Funcao: `cancel_appointment`
- Resultado esperado: confirmar que excecoes sao capturadas e a funcao retorna `False`, sem diferenciar o motivo.

1.6. [x] Mapear todos os caminhos que podem chamar criacao/cancelamento de agenda.

- Procurar por `create_appointment_if_available`.
- Procurar por `cancel_appointment`.
- Separar chamadas de marcacao simples, remarcacao e cancelamento simples.
- Resultado esperado: garantir que a correcao nao cubra apenas um caminho e deixe outro caminho duplicando eventos.
- Mapeamento:
  - `src/interfaces/http/app.py::_handle_offered_slot_selection` cria evento no caminho deterministico de confirmacao de horario ofertado e, quando `intent == "reschedule"`, cancela o evento antigo.
  - `src/interfaces/http/app.py::_handle_appointment_confirmation` cancela evento da confirmacao proativa quando o paciente responde cancelamento.
  - `src/interfaces/tools/calendar_tool.py::CreateAppointmentTool` cria evento via agente.
  - `src/interfaces/tools/calendar_tool.py::CancelAppointmentTool` cancela evento via agente usando `event_id` quando houver mais de uma consulta.

1.7. [x] Documentar o fluxo atual em uma sequencia real de mensagens.

- Exemplo minimo:
  - paciente tem consulta antiga `evt-old`;
  - paciente pede para remarcar;
  - API oferece novo horario;
  - paciente confirma;
  - API cria `evt-new`;
  - API tenta cancelar `evt-old`.
- Resultado esperado: ter um cenario concreto para reproduzir em teste automatizado.
- Sequencia documentada:
  - paciente tem consulta antiga `evt-old`;
  - paciente pede para remarcar e o estado fica com `intent="reschedule"` e `reschedule_event_id="evt-old"`;
  - API oferece novo horario e pede confirmacao;
  - paciente confirma;
  - API cria `evt-new`;
  - API tenta cancelar `evt-old`;
  - se o cancelamento falhar, a API preserva `evt-old` e `evt-new`, alerta admin e nao responde como sucesso total.

## 2. Reproducao automatizada antes da correcao

2.1. [x] Criar teste que reproduz remarcacao com sucesso.

- Arquivo sugerido: `tests/test_main_webhook.py`
- Estado inicial:
  - `ConversationState(intent="reschedule")`
  - `reschedule_event_id="evt-old"`
  - historico com horario ofertado
- Mocks:
  - `create_appointment_if_available` retorna `{"id": "evt-new"}`
  - `cancel_appointment("evt-old")` retorna `True`
- Validacoes:
  - API retorna sucesso;
  - criacao foi chamada uma vez;
  - cancelamento foi chamado uma vez com `evt-old`;
  - estado final fica limpo ou `idle`.

2.2. [x] Criar teste que reproduz falha parcial de remarcacao.

- Arquivo sugerido: `tests/test_main_webhook.py`
- Estado inicial igual ao item 2.1.
- Mocks:
  - `create_appointment_if_available` retorna `{"id": "evt-new"}`
  - `cancel_appointment("evt-old")` retorna `False`
- Validacoes obrigatorias:
  - API nao responde com a mesma mensagem de sucesso total;
  - alerta administrativo e disparado;
  - `evt-old` e `evt-new` ficam disponiveis para recuperacao manual;
  - estado nao e apagado antes de preservar os dados de recuperacao.

2.3. [x] Criar teste para remarcacao sem `reschedule_event_id`.

- Estado inicial:
  - `ConversationState(intent="reschedule")`
  - `reschedule_event_id=""`
  - horario novo ofertado e confirmado
- Validacoes:
  - API nao deve criar novo evento como remarcacao;
  - API deve pedir identificacao/confirmacao da consulta antiga;
  - estado deve continuar indicando fluxo de remarcacao.

2.4. [x] Criar teste para marcacao simples protegendo contra regressao.

- Estado inicial:
  - `ConversationState(intent="schedule")` ou estado sem intencao de remarcacao.
- Validacoes:
  - cria novo evento;
  - nao chama `cancel_appointment`;
  - resposta continua sendo confirmacao de agendamento.

2.5. [x] Criar teste para cancelamento simples protegendo contra regressao.

- Arquivos possiveis:
  - `tests/test_main_webhook.py`
  - `tests/test_conversation_workflow_service.py`
- Validacoes:
  - cancelamento exige confirmacao quando aplicavel;
  - usa o `event_id` correto;
  - nao cria novo evento;
  - limpa estado somente apos cancelamento confirmado.

## 3. Definicao da regra de consistencia da remarcacao

3.1. [x] Escolher a estrategia final para falha parcial.

- Opcao recomendada: manter novo evento, preservar estado e alertar humano.
- Motivo: evita apagar o novo horario escolhido pelo paciente sem revisao humana.
- Resultado esperado: decisao registrada no `spec.md` antes ou junto da correcao.

3.2. [x] Definir resposta ao paciente quando a remarcacao ficar parcial.

- A mensagem nao deve dizer que a remarcacao foi concluida.
- A mensagem deve dizer que o horario solicitado foi recebido, mas a equipe vai confirmar o ajuste da agenda.
- A mensagem nao deve expor detalhes tecnicos como `event_id`.
- Resultado esperado: paciente nao recebe uma falsa confirmacao.

3.3. [x] Definir payload minimo do alerta administrativo.

- Telefone do paciente.
- Nome do paciente.
- `event_id` antigo.
- Label/data do evento antigo, se houver.
- `event_id` novo.
- Data/hora nova.
- Ultima mensagem do paciente.
- Motivo: `remarcacao_parcial`.

3.4. [x] Definir onde preservar dados de recuperacao.

- Opcao minima: manter `ConversationState` com `reschedule_event_id` e adicionar metadados do novo evento.
- Opcao mais robusta: registrar uma interacao/erro operacional alem do estado.
- Resultado esperado: apos falha parcial, uma pessoa consegue saber exatamente o que cancelar ou confirmar.

## 4. Correcao no fluxo de remarcacao

4.1. [x] Capturar o retorno de `create_appointment_if_available`.

- Arquivo: `src/interfaces/http/app.py`
- Funcao: `_handle_offered_slot_selection`
- Mudanca esperada: guardar o evento retornado em uma variavel, por exemplo `new_event`.
- Resultado esperado: obter `new_event_id` para log, alerta e recuperacao.

4.2. [x] Antes de criar novo evento em remarcacao, validar que existe evento antigo.

- Condicao: `state.intent == "reschedule"`.
- Se `state.reschedule_event_id` estiver vazio:
  - nao criar o novo evento;
  - pedir ao paciente para confirmar qual consulta quer remarcar;
  - salvar estado apropriado.
- Resultado esperado: remarcacao nunca vira uma marcacao simples por falta de `event_id`.

4.3. [x] Separar o caminho de marcacao simples do caminho de remarcacao.

- Marcacao simples:
  - cria novo evento;
  - registra `schedule`;
  - responde confirmacao de agendamento;
  - limpa estado.
- Remarcacao:
  - cria novo evento;
  - tenta cancelar evento antigo;
  - so responde sucesso total se o cancelamento antigo confirmar `True`.

4.4. [x] Tratar sucesso completo da remarcacao.

- Condicao:
  - `new_event_id` existe;
  - `cancel_appointment(state.reschedule_event_id)` retorna `True`.
- Acoes:
  - registrar interacao como `reschedule`;
  - responder confirmacao de remarcacao;
  - limpar estado.
- Resultado esperado: ao final, apenas o novo evento fica ativo.

4.5. [x] Tratar falha parcial da remarcacao.

- Condicao:
  - novo evento foi criado;
  - cancelamento do antigo retornou `False`.
- Acoes:
  - enviar alerta administrativo;
  - preservar `reschedule_event_id`;
  - salvar `new_event_id` em `state.metadata`;
  - salvar data/hora nova em `state.metadata`;
  - responder ao paciente com mensagem segura;
  - nao limpar o estado antes da preservacao.
- Resultado esperado: duplicidade nao fica silenciosa.

4.6. [x] Tratar falha de criacao do novo evento.

- Condicao:
  - `create_appointment_if_available` levanta `ValueError`.
- Acoes:
  - nao cancelar evento antigo;
  - informar que o novo horario ficou indisponivel;
  - manter fluxo de remarcacao para nova escolha.
- Resultado esperado: consulta antiga continua preservada.

4.7. [x] Melhorar observabilidade do cancelamento.

- Arquivo: `src/infrastructure/integrations/calendar_service.py`
- Funcao: `cancel_appointment`
- Mudanca esperada:
  - logar excecao com `event_id`;
  - manter retorno booleano se for o contrato atual.
- Resultado esperado: falhas reais do Google Calendar deixam rastro nos logs.

## 5. Protecoes contra regressao do agente/LLM

5.1. [x] Revisar instrucoes em `src/application/services/clean_agent_service.py`.

- Procurar regras de remarcar, cancelar e consultar agenda.
- Resultado esperado: garantir que o agente consulte o agendamento antigo antes de remarcar quando nao houver contexto.
- Resultado: prompt reforcado para consultar agenda antes de cancelar/remarcar, pedir escolha quando houver mais de uma consulta e nunca criar agendamento de remarcacao sem identificar a consulta antiga.

5.2. [x] Garantir que `consultar_agendamento` seja usado para obter `event_id`.

- Arquivo: `src/interfaces/tools/calendar_tool.py`
- Resultado esperado: quando houver mais de uma consulta futura, o fluxo nao cancela por inferencia fraca.
- Resultado: descricoes das tools reforcadas e teste garante que `cancelar_agendamento` recusa cancelamento ambiguo sem `event_id`.

5.3. [x] Garantir que cancelamento simples nao seja confundido com confirmacao de slot.

- Verificar estado `awaiting_cancel_confirmation`.
- Resultado esperado: resposta "sim" para cancelamento nao deve criar agendamento.
- Resultado: teste do webhook confirma que `awaiting_cancel_confirmation` nao chama `create_appointment_if_available`.

5.4. [x] Garantir que remarcacao iniciada pela confirmacao proativa preserve contexto.

- Arquivos:
  - `src/interfaces/http/app.py`
  - `src/application/services/appointment_confirmation_service.py`
- Resultado esperado: pedido como "quero remarcar" deve manter `reschedule_event_id` ate a conclusao.
- Resultado: teste do webhook confirma que `CONFIRMATION_STAGE` salva `reschedule_event_id` e `reschedule_event_label` ao receber pedido de remarcacao.

## 6. Execucao dos testes

6.1. [x] Rodar testes focados do webhook.

- Comando: `pytest tests/test_main_webhook.py`
- Resultado esperado: todos os testes de marcacao/remarcacao/cancelamento passam.
- Resultado: `.venv\Scripts\python.exe -m pytest tests/test_main_webhook.py` passou com 21 testes.

6.2. [ ] Rodar testes do fluxo conversacional.

- Comando: `pytest tests/test_conversation_workflow_service.py`
- Resultado esperado: fluxos de confirmacao proativa, cancelamento e remarcacao continuam validos.
- Observacao: `tests/test_conversation_workflow_service.py` depende de modulo ausente (`conversation_workflow_service`) ja identificado na suite completa. Para a secao 5, foi executado o teste focado de confirmacao proativa:
  - `.venv\Scripts\python.exe -m pytest tests/test_appointment_confirmation_service.py` passou com 2 testes.

6.3. [x] Rodar testes de regras de agenda.

- Comando: `pytest tests/test_calendar_rules.py`
- Resultado esperado: regras de disponibilidade, horario valido e conflitos continuam intactas.
- Resultado: `.venv\Scripts\python.exe -m pytest tests/test_calendar_rules.py` passou com 15 testes.

6.4. [ ] Rodar suite completa quando os testes focados passarem.

- Comando: `pytest`
- Resultado esperado: nenhuma regressao fora do fluxo de agenda.
- Resultado: `.venv\Scripts\python.exe -m pytest` executou, mas falhou por modulos ausentes fora das tarefas 1-4: `agent_conversation_service`, `conversation_workflow_service`, `dental_crew` e `langgraph_conversation_service`. Os testes do webhook e das regras de agenda passaram dentro da suite.
- Teste adicional da secao 5:
  - `.venv\Scripts\python.exe -m pytest tests/test_calendar_tool.py` passou com 2 testes.

## 7. Validacao manual em ambiente controlado

7.1. [ ] Preparar paciente de teste.

- Criar uma consulta futura no Google Calendar de homologacao.
- Confirmar que o telefone do paciente aparece na descricao/resumo do evento.
- Anotar `event_id`, data e horario antigo.

7.2. [ ] Validar remarcacao com sucesso.

- Pelo WhatsApp/API, pedir remarcacao.
- Escolher novo horario ofertado.
- Confirmar resposta da API.
- Conferir no Google Calendar:
  - evento antigo removido;
  - evento novo criado;
  - nao existem dois eventos ativos para o mesmo paciente.

7.3. [ ] Validar marcacao simples.

- Iniciar conversa sem `intent=reschedule`.
- Marcar consulta em horario ofertado.
- Conferir que nenhum evento antigo foi cancelado.

7.4. [ ] Validar cancelamento simples.

- Pedir cancelamento de uma consulta futura.
- Confirmar cancelamento.
- Conferir que o evento correto foi removido.
- Conferir que nenhum novo evento foi criado.

7.5. [ ] Validar falha parcial, se possivel simular.

- Forcar `cancel_appointment` a retornar `False` em ambiente controlado.
- Confirmar que:
  - alerta administrativo e enviado;
  - paciente nao recebe sucesso total;
  - dados de recuperacao ficam registrados;
  - operador consegue decidir qual evento manter/cancelar.

## 8. Atualizacao final da documentacao

8.1. [ ] Atualizar `implatações/spec.md` com a estrategia escolhida.

- Registrar se a decisao final foi manter novo evento com intervencao manual, rollback do novo evento ou outra abordagem.

8.2. [ ] Atualizar este `task.md` marcando tarefas concluidas.

- Marcar apenas tarefas realmente executadas.
- Manter pendencias explicitas para validacao real, se nao forem feitas no mesmo ciclo.

8.3. [ ] Registrar comandos de teste executados e resultado.

- Exemplo:
  - `pytest tests/test_main_webhook.py` passou;
  - `pytest tests/test_conversation_workflow_service.py` passou;
  - `pytest tests/test_calendar_rules.py` passou.
- Registrado nos itens 6.1, 6.3 e 6.4.
- Secao 5:
  - `.venv\Scripts\python.exe -m pytest tests/test_main_webhook.py` passou com 22 testes;
  - `.venv\Scripts\python.exe -m pytest tests/test_appointment_confirmation_service.py` passou com 2 testes;
  - `.venv\Scripts\python.exe -m pytest tests/test_calendar_tool.py` passou com 2 testes.

## 9. Criterios de conclusao

9.1. [ ] Remarcacao com sucesso deixa apenas o novo evento ativo.

9.2. [ ] Falha ao cancelar evento antigo nao gera sucesso silencioso.

9.3. [ ] Estado ou registro operacional preserva `event_id` antigo e novo em falha parcial.

9.4. [x] Marcacao simples nao chama cancelamento.

9.5. [x] Cancelamento simples nao cria novo evento.

9.6. [x] Confirmacao proativa de consulta consegue iniciar remarcacao sem perder `event_id` original.

9.7. [x] Testes automatizados cobrem sucesso, falha parcial e ausencia de `reschedule_event_id`.

9.8. [ ] Validacao manual confirma que o Google Calendar nao fica duplicado apos remarcacao.
