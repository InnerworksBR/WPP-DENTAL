# Relatorio tecnico de desenvolvimento - WPP-DENTAL

Data do relatorio: 25/05/2026  
Projeto: WPP-DENTAL  
Periodo identificado no Git: 04/04/2026 a 20/05/2026  
Autor dos commits: Innerworks

## 1. Resumo executivo

O WPP-DENTAL e um backend em Python para atendimento odontologico via WhatsApp. O sistema atua como uma assistente de agendamento para a Dra. Priscila, recebendo mensagens pela Evolution API, conduzindo fluxos de conversa com apoio de IA, consultando e alterando a agenda no Google Calendar, armazenando pacientes e historico em SQLite e enviando respostas automaticamente pelo WhatsApp.

O projeto evoluiu de uma base inicial para deploy em VPS/EasyPanel ate uma solucao mais completa, com arquitetura limpa, regras de negocio odontologicas, integracao com Google Calendar, controle de estado conversacional, confirmacao automatica de consultas, handoff manual, painel administrativo local e cobertura de testes para os principais fluxos.

Principais entregas:

- Backend FastAPI com endpoints de webhook, health check, reload de configuracao e painel administrativo.
- Integracao com Evolution API para recebimento e envio de mensagens WhatsApp.
- Integracao com Google Calendar para consultar disponibilidade, criar, remarcar e cancelar consultas.
- Persistencia local em SQLite com historico de conversas, pacientes, estados, mensagens processadas e confirmacoes.
- Motor conversacional baseado em IA e ferramentas deterministicas para reduzir risco em operacoes sensiveis.
- Regras de agenda com slots de 15 minutos, periodos do dia e antecedencia minima de 2 dias uteis.
- Regras de convenios, aliases, encaminhamentos e procedimentos configuraveis via YAML.
- Confirmacao automatica de consultas do dia seguinte.
- Handoff manual para impedir que o bot responda quando a doutora assume a conversa.
- Painel administrativo para acompanhar pacientes, conversas, erros, consultas futuras e bloqueios.
- Testes automatizados cobrindo webhook, calendario, configuracoes, regras de escopo, banco, admin e fluxos conversacionais.

## 2. Base tecnica do projeto

### 2.1 Stack utilizada

| Area | Tecnologia |
| --- | --- |
| Linguagem | Python 3.11+ |
| API HTTP | FastAPI |
| Servidor ASGI | Uvicorn |
| Banco de dados | SQLite |
| IA / LLM | OpenAI, LangChain e LangGraph |
| WhatsApp | Evolution API |
| Agenda | Google Calendar API |
| Configuracao | `.env` e arquivos YAML |
| Testes | pytest |
| Deploy | Docker, EasyPanel e VPS/systemd |

### 2.2 Estrutura principal

```text
WPP-DENTAL/
|-- config/
|   |-- messages.yaml
|   |-- plans.yaml
|   |-- procedure_rules.yaml
|   `-- settings.yaml
|-- deploy/
|   |-- start.sh
|   `-- wpp-dental.service
|-- docs/
|-- src/
|   |-- application/
|   |-- domain/
|   |-- infrastructure/
|   `-- interfaces/
|-- tests/
|-- Dockerfile
|-- README.md
|-- PRD.md
|-- SPEC.md
|-- pyproject.toml
`-- requirements.txt
```

### 2.3 Arquitetura

O projeto foi organizado seguindo uma abordagem inspirada em Clean Architecture:

| Camada | Responsabilidade |
| --- | --- |
| `interfaces` | Entradas e saidas externas, como HTTP, admin e tools do agente |
| `application` | Casos de uso, servicos de conversa, confirmacao e estado |
| `domain` | Entidades e regras de negocio independentes de infraestrutura |
| `infrastructure` | Banco, configuracao, logs e integracoes externas |

Essa separacao facilita testes, manutencao e evolucao, pois as regras de negocio ficam isoladas das APIs externas.

## 3. Funcionalidades implementadas

### 3.1 Atendimento via WhatsApp

Foi implementado o endpoint principal `POST /webhook/message`, responsavel por receber eventos da Evolution API, extrair a mensagem, identificar telefone/nome do contato, aplicar validacoes e enviar resposta ao paciente.

Recursos implementados:

- Suporte a eventos de mensagem da Evolution API.
- Extracao de texto de mensagens simples e mensagens estendidas.
- Ignorar mensagens sem texto.
- Identificacao de mensagens enviadas pela propria instancia.
- Registro de historico da conversa.
- Resposta automatica via WhatsApp.
- Compatibilidade com diferentes formas de chave de webhook.
- Idempotencia por `message_id` para reduzir risco de processamento duplicado.

### 3.2 Cadastro e reconhecimento de pacientes

O sistema reconhece pacientes pelo telefone, salva nome e convenio/plano, e reutiliza essas informacoes em conversas futuras.

Foi implementado:

- Busca de paciente por telefone.
- Criacao e atualizacao de paciente.
- Registro de interacoes.
- Historico conversacional por telefone.
- Saudacao e continuidade de fluxo com base no estado anterior.

### 3.3 Agendamento de consultas

O fluxo de agendamento consulta o Google Calendar, respeita regras de horario e oferece opcoes ao paciente.

Regras implementadas:

- Slots de 15 minutos.
- Periodos: manha, tarde e noite.
- Sugestao de 2 horarios por oferta.
- Antecedencia minima de 2 dias uteis.
- Janela maxima de 30 dias.
- Atendimento de segunda a sexta-feira.
- Idade minima operacional configurada como 8 anos.
- Criacao de evento com nome e telefone do paciente.
- Validacao de conflito no calendario antes de criar consulta.
- Bloqueio de agendamento fora dos horarios previamente ofertados.

### 3.4 Remarcacao e cancelamento

Foram adicionados fluxos para localizar consulta futura do paciente, cancelar o evento anterior e criar novo evento quando necessario.

Evolucoes importantes:

- Correcao de problemas de remarcacao.
- Estado estruturado para guardar evento pendente.
- Confirmacao antes de alterar/cancelar consulta.
- Tratamento de conversas em andamento para evitar operacoes indevidas.

### 3.5 Consulta de proxima consulta

O sistema consegue consultar no Google Calendar as proximas consultas relacionadas ao telefone/nome do paciente e responder com data e horario.

### 3.6 Confirmacao automatica de consultas

Foi implementado um servico de confirmacao automatica para consultas do dia seguinte.

Funcionamento:

- Rotina interna diaria as 20h no timezone `America/Sao_Paulo`.
- Busca consultas do dia seguinte no Google Calendar.
- Deduplicacao por telefone.
- Registro de tentativa de confirmacao no SQLite.
- Envio de mensagem pelo WhatsApp.
- Estado `awaiting_appointment_confirmation` para tratar resposta do paciente.
- Suporte a respostas afirmativas, pedido de remarcacao e cancelamento.

### 3.7 Handoff manual

O handoff manual impede que o bot responda quando a doutora assume a conversa.

Foi implementado:

- Deteccao de mensagem `fromMe=true`.
- Diferenciacao entre eco de mensagem automatica e mensagem manual.
- Estado de handoff ativo.
- Janela de 30 minutos.
- Registro das mensagens do paciente durante o handoff sem resposta automatica.

### 3.8 Regras de convenios e procedimentos

Os convenios ficam em `config/plans.yaml` e incluem planos ativos, aliases, encaminhamentos e restricoes.

Planos configurados:

- OdontoPrev
- Bradesco Dental
- BB Dental
- Previan / Rede UNNA
- Unimed Odonto
- Sulamerica
- Amil Dental
- Uniodonto
- MetLife
- Caixa de Saude de Sao Vicente
- Caixa de Peculio de Sao Vicente
- Dentalpar
- Transmontano
- Particular

Encaminhamentos configurados:

- Caixa de Saude de Sao Vicente -> Dra. Tarcilia
- Caixa de Peculio de Sao Vicente -> Dra. Tarcilia

Tambem foram adicionadas regras em `config/procedure_rules.yaml` para assuntos como protese, coroa, faceta, ponte, ortodontia, canal em molar e extracao de siso.

### 3.9 Guardas de escopo

O sistema foi desenhado para nao responder perguntas clinicas, valores, diagnosticos ou orientacoes de tratamento.

Quando detecta assunto fora de escopo:

- Encaminha para a doutora.
- Envia mensagem segura ao paciente.
- Registra historico.
- Evita resposta automatica arriscada.

### 3.10 Painel administrativo local

Foi criado um painel administrativo em `/admin`.

Recursos do painel:

- Tela administrativa local.
- Autenticacao por `ADMIN_API_KEY`, com fallback para chave do webhook.
- Resumo de metricas.
- Listagem de pacientes cadastrados.
- Busca/filtro de usuarios.
- Visualizacao de conversas e detalhes.
- Listagem de erros.
- Listagem de consultas futuras.
- Criacao, listagem e remocao de bloqueios de agenda.
- Tratamento de erros do Google Calendar em payload consistente.
- Melhoria de UI/UX e exibicao de usuarios cadastrados.

### 3.11 Deploy e operacao

O projeto foi preparado para deploy em VPS, Docker e EasyPanel.

Entregas:

- `Dockerfile`.
- `.dockerignore`.
- `deploy/start.sh`.
- `deploy/wpp-dental.service`.
- Health check em `/` e `/health`.
- Configuracao por variaveis de ambiente.
- Suporte a credenciais do Google Calendar via arquivo, JSON, base64 ou par email/chave privada.
- Recomendacao operacional de uma unica replica por uso de SQLite e scheduler interno.

### 3.12 Documentacao

Foram criados e atualizados documentos de apoio:

- `README.md`: visao geral, stack, execucao local, testes e deploy.
- `PRD.md`: requisitos de produto.
- `SPEC.md`: especificacao tecnica original.
- `docs/TECHNICAL_DOCUMENTATION.md`: documentacao tecnica detalhada da solucao.
- Este relatorio: resumo consolidado do que foi desenvolvido, historico, estimativa de tempo e custo.

## 4. Persistencia e dados

O banco padrao e SQLite em `data/dental.db`, com inicializacao automatica.

Tabelas/estruturas documentadas no projeto:

| Tabela | Finalidade |
| --- | --- |
| `patients` | Cadastro de pacientes por telefone |
| `interactions` | Registro de interacoes operacionais |
| `conversation_history` | Historico textual da conversa |
| `conversation_state` | Estado estruturado por telefone |
| `processed_messages` | Controle de idempotencia de webhooks |
| `appointment_confirmations` | Controle de confirmacoes automaticas |
| `outbound_messages` | Controle de mensagens enviadas para ignorar ecos |

## 5. Qualidade e testes

O projeto possui uma suite de testes automatizados com pytest.

Areas cobertas:

- Webhook principal.
- Regras de calendario.
- Criacao, cancelamento, remarcacao e consulta de agendamento.
- Confirmacao automatica.
- Configuracoes e leitura de YAML.
- Regras de escopo.
- Normalizacao de telefone.
- Banco de dados.
- Painel administrativo.
- Fluxos conversacionais simulados.

Arquivos de teste identificados no repositorio:

- `tests/test_admin.py`
- `tests/test_agent_scenarios.py`
- `tests/test_appointment_confirmation_service.py`
- `tests/test_appointment_offer_service.py`
- `tests/test_calendar_rules.py`
- `tests/test_calendar_tool.py`
- `tests/test_config.py`
- `tests/test_config_tool.py`
- `tests/test_conversation_context_validation.py`
- `tests/test_conversation_service.py`
- `tests/test_conversation_workflow_service.py`
- `tests/test_database.py`
- `tests/test_dental_crew_langgraph.py`
- `tests/test_langgraph_conversation_service.py`
- `tests/test_main_webhook.py`
- `tests/test_phone_normalization.py`
- `tests/test_scope_guard_service.py`

## 6. Historico de desenvolvimento pelo Git

Observacao importante: Git nao mede horas trabalhadas. Ele registra commits, datas e alteracoes. A estimativa de tempo deste relatorio usa o historico de commits como evidencia, mas nao substitui apontamento real de horas.

### 6.1 Numeros gerais do repositorio

| Metrica | Valor |
| --- | ---: |
| Commits identificados | 45 |
| Primeiro commit analisado | 04/04/2026 |
| Ultimo commit analisado | 20/05/2026 |
| Dias com commits | 11 |
| Arquivos versionados | 77 |
| Entradas de arquivo alteradas no historico | 260 |
| Linhas adicionadas no historico | 17.904 |
| Linhas removidas no historico | 3.905 |

### 6.2 Commits por dia

| Data | Commits | Principais entregas |
| --- | ---: | --- |
| 04/04/2026 | 1 | Preparacao inicial para deploy em VPS, estrutura principal, PRD/SPEC, API, banco, tools, testes e configuracoes |
| 06/04/2026 | 17 | Confirmacoes automaticas, deploy EasyPanel, credenciais Google, handoff, regras de planos, contexto conversacional, Rasa/LangGraph, fixes de webhook e calendario |
| 07/04/2026 | 7 | Troca de motor do agente, logs, rebuilds, correcao de fluxo e cenarios de teste |
| 08/04/2026 | 6 | Modo hibrido, confirmacao de agendamento, migracao para arquitetura limpa e correcoes |
| 09/04/2026 | 1 | Ajuste final da arquitetura limpa |
| 17/04/2026 | 1 | Correcoes gerais em conversa, estado, calendario e confirmacao |
| 30/04/2026 | 5 | Fluxo gerido por estados, adicao de Dentalpar/Transmontano e ajustes de saudacao |
| 05/05/2026 | 1 | Correcao de nome incorreto |
| 13/05/2026 | 1 | Correcoes de chat e calendario |
| 14/05/2026 | 4 | Painel administrativo local, chave de API, correcao de remarcacao, usuarios cadastrados e UI/UX |
| 20/05/2026 | 1 | Correcao de problemas de marcacao reportados em 20/05/2026 |

### 6.3 Linha do tempo por fases

#### Fase 1 - Base inicial e deploy (04/04/2026)

Commit principal:

- `9f0f3d8` - Prepare WPP-DENTAL for VPS deploy

Entregas:

- Estrutura inicial do projeto.
- API FastAPI.
- Camadas de dominio, aplicacao, infraestrutura e interfaces.
- Integracao inicial com WhatsApp, Google Calendar e SQLite.
- Configuracoes YAML.
- Documentos PRD e SPEC.
- Testes iniciais.
- Scripts de deploy.

Volume do commit: 61 arquivos alterados e 6.231 linhas adicionadas.

#### Fase 2 - Confirmacoes, EasyPanel, credenciais e regras (06/04/2026)

Commits principais:

- `d5a6c4e` - automatizacao de confirmacoes e preparo para EasyPanel.
- `1d14200` - health endpoint raiz.
- `647fd56`, `2c597fa`, `e71c63a` - melhorias no suporte a credenciais Google.
- `addfc55` - handoff manual com cooldown.
- `57681ed` - regras de planos e encaminhamentos.
- `d3fdefe` - normalizacao de telefone em webhook.
- `9d8c5ba`, `27e14e9`, `ddb538b`, `2dde67e` - melhorias de conversa contextual.
- `27fe0f2` e `d257523` - experimento Rasa e reversao.
- `388c3e6` - roteamento com LangGraph.

Entregas:

- Confirmacao automatica de consultas.
- Preparacao para deploy conteinerizado.
- Suporte a diferentes formatos de credenciais Google.
- Handoff manual.
- Regras de planos, encaminhamentos e contexto.
- Evolucao do motor conversacional.

#### Fase 3 - Motor do agente, logs e cenarios (07/04/2026)

Commits principais:

- `236d831` - troca de motor do agente.
- `83f5a8c` e `7ee45a3` - logs e requisitos.
- `537bef2`, `0343f7d`, `c72bf5c` - rebuilds e ajustes.
- `393bcf9` - correcao e cenarios de teste.

Entregas:

- Melhorias na orquestracao do agente.
- Logs para diagnostico.
- Ajustes de dependencias.
- Testes de cenarios conversacionais.

#### Fase 4 - Arquitetura limpa e simplificacao (08/04/2026 a 09/04/2026)

Commits principais:

- `f8c0877` - correcao em confirmacao de agendamentos.
- `a6b045b` - modo hibrido.
- `559c903` - solucao de Clean Architecture.
- `877a7e4`, `2910104`, `384bc2c`, `b163bfa` - fixes da arquitetura.

Entregas:

- Refatoracao grande removendo servicos antigos e concentrando fluxo em `CleanAgentService`.
- Reducao de complexidade.
- Ajustes de regras e imports.
- Consolidacao da arquitetura atual.

#### Fase 5 - Correcoes operacionais e estados (17/04/2026 a 05/05/2026)

Commits principais:

- `27dd066` - fixes gerais.
- `cb0c657` e `c9a8ed4` - fluxo de conversa gerido por estados.
- `c5e5e9d` - adicao Dentalpar e Transmontano.
- `aa1177b` e `f80910f` - mudancas de saudacao.
- `9184b96` - correcao de nome errado.

Entregas:

- Melhor controle de estado conversacional.
- Ajustes de calendario.
- Inclusao de novos convenios.
- Melhorias de mensagens e saudacao.

#### Fase 6 - Painel administrativo e correcoes de remarcacao (13/05/2026 a 14/05/2026)

Commits principais:

- `08e1c49` - correcao de chat e calendario.
- `6b13da1` - painel administrativo local.
- `6bcf039` - correcao de chave de API quebrada.
- `32e7f86` - solucao de problema de remarcacao.
- `21b003e` - mostrar usuarios cadastrados e melhoria de UI/UX.

Entregas:

- Painel administrativo completo em `/admin`.
- Endpoints e testes do admin.
- Protecao por chave de API.
- Listagem de usuarios cadastrados.
- Correcoes importantes de remarcacao.
- Melhorias de UI/UX.

#### Fase 7 - Correcoes reportadas de marcacao (20/05/2026)

Commit principal:

- `23444bf` - correcao de problemas de marcacao reportados em 20/05/2026.

Entregas:

- Ajustes em `CleanAgentService`.
- Ajustes em `ConversationStateService`.
- Regras de oferta de horario.
- Ajustes no webhook.
- Ajustes em `calendar_tool`.
- Novos testes para servico de oferta, regras de calendario e webhook.

Volume do commit: 8 arquivos alterados, 824 insercoes e 11 remocoes.

## 7. Estimativa de tempo gasto

### 7.1 Metodo usado

Como o Git nao registra tempo real de trabalho, foi usada uma estimativa tecnica considerando:

- Quantidade de commits.
- Distribuicao dos commits no tempo.
- Tamanho dos commits.
- Complexidade das entregas.
- Volume de integracoes externas.
- Quantidade de regras de negocio.
- Quantidade de testes e documentacao.
- Necessidade de investigacao/correcao de bugs apos uso real.

### 7.2 Tempo minimo observavel pelo Git

Em dias com varios commits, e possivel observar janelas de trabalho aproximadas entre o primeiro e o ultimo commit do dia:

| Data | Janela aproximada visivel nos commits |
| --- | ---: |
| 06/04/2026 | 10:14 a 17:06, cerca de 6h52 |
| 07/04/2026 | 09:52 a 17:20, cerca de 7h28 |
| 08/04/2026 | 10:51 a 14:52, cerca de 4h01 |
| 30/04/2026 | 14:24 a 15:05, cerca de 0h41 |
| 14/05/2026 | 15:40 a 17:06, cerca de 1h26 |

Somente essas janelas somam aproximadamente 20h28. Esse numero e um piso minimo, pois nao considera:

- Tempo antes do primeiro commit de cada dia.
- Tempo depois do ultimo commit de cada dia.
- Dias com apenas um commit.
- Levantamento de requisitos.
- Testes manuais com WhatsApp, Evolution API e Google Calendar.
- Debug de credenciais e deploy.
- Analise de bugs reportados.
- Documentacao e revisao.

### 7.3 Estimativa realista por fase

| Fase | Estimativa |
| --- | ---: |
| Requisitos, desenho inicial e base do projeto | 12h a 18h |
| Backend FastAPI, SQLite, Evolution API e Google Calendar | 18h a 26h |
| Fluxos conversacionais, IA, estado e guardas de escopo | 18h a 28h |
| Regras de agenda, convenios, procedimentos e encaminhamentos | 8h a 14h |
| Confirmacao automatica e handoff manual | 8h a 12h |
| Deploy Docker/EasyPanel/VPS e credenciais Google | 6h a 10h |
| Painel administrativo local | 10h a 16h |
| Testes automatizados e cenarios simulados | 10h a 18h |
| Correcoes, ajustes de producao e refinamentos | 12h a 20h |
| Documentacao tecnica | 4h a 8h |

Estimativa total realista: 106h a 170h.

Para fins de cobranca/valoracao, uma faixa equilibrada seria considerar 130h como ponto medio.

## 8. Estimativa de custo do tempo de desenvolvimento

### 8.1 Premissas

A estimativa abaixo considera apenas tempo de desenvolvimento tecnico. Nao inclui:

- Custos de servidores.
- Custos da Evolution API.
- Custos da OpenAI.
- Custos do Google Cloud.
- Suporte mensal.
- Treinamento operacional.
- Manutencao futura.

Como valor de referencia, foram considerados tres cenarios de valor/hora:

- R$ 80/h: valor conservador.
- R$ 120/h: valor intermediario para desenvolvimento backend/IA com integracoes.
- R$ 150/h: valor mais proximo de trabalho especializado com IA, automacao, deploy e integrações externas.

### 8.2 Cenarios de custo

| Horas estimadas | R$ 80/h | R$ 120/h | R$ 150/h |
| ---: | ---: | ---: | ---: |
| 106h | R$ 8.480,00 | R$ 12.720,00 | R$ 15.900,00 |
| 130h | R$ 10.400,00 | R$ 15.600,00 | R$ 19.500,00 |
| 170h | R$ 13.600,00 | R$ 20.400,00 | R$ 25.500,00 |

Valor sugerido para representar o desenvolvimento realizado:

```text
130h x R$ 120/h = R$ 15.600,00
```

Faixa recomendada para apresentacao comercial:

```text
R$ 12.720,00 a R$ 20.400,00
```

Essa faixa considera o trabalho como projeto customizado, com backend, IA conversacional, integracoes externas, deploy, regras de negocio e testes.

## 9. Valor tecnico entregue

O desenvolvimento nao se limitou a uma automacao simples de mensagens. O projeto entregou uma solucao operacional com varios pontos de complexidade:

- Integracao com WhatsApp por API externa.
- Integracao com Google Calendar com leitura, escrita e bloqueios.
- Persistencia de dados e estados de conversa.
- Controle de idempotencia para webhooks duplicados.
- Regras de negocio odontologicas configuraveis.
- Agente de IA limitado por ferramentas e guardas de seguranca.
- Fluxos criticos de agendamento, remarcacao e cancelamento.
- Painel administrativo.
- Deploy conteinerizado e instrucoes para VPS.
- Testes automatizados.
- Documentacao tecnica.

Esses itens justificam tratar o projeto como um sistema sob medida, nao apenas como um chatbot.

## 10. Riscos e limitacoes atuais

Pontos ja documentados no projeto:

- SQLite exige cuidado com multiplas replicas; recomendacao atual e usar uma unica instancia.
- Scheduler interno pode duplicar confirmacoes se houver mais de uma replica ativa.
- Webhook principal aceita chamadas sem chave valida em alguns cenarios de compatibilidade, embora registre alerta.
- Audio, imagem e documentos nao sao interpretados.
- A disponibilidade depende da Google Calendar API.
- A operacao depende da Evolution API estar conectada ao WhatsApp.
- O modelo de IA depende de `OPENAI_API_KEY` valida.

## 11. Recomendacoes futuras

Possiveis proximas evolucoes:

- Migrar de SQLite para PostgreSQL caso o uso cresca ou haja mais de uma instancia.
- Separar scheduler em processo/worker dedicado.
- Fechar o webhook para exigir chave obrigatoria em producao.
- Adicionar observabilidade com dashboard de logs e metricas.
- Melhorar painel administrativo com login completo.
- Adicionar suporte a audio com transcricao.
- Adicionar rotina de backup do banco.
- Criar pipeline CI/CD com execucao automatica de testes.
- Criar relatorios mensais de agendamentos, cancelamentos e remarcacoes.
- Adicionar controle financeiro ou exportacao para planilha, se fizer sentido para a clinica.

## 12. Conclusao

O projeto WPP-DENTAL evoluiu para uma solucao tecnica completa para atendimento e agendamento odontologico via WhatsApp. Foram implementadas integracoes externas, persistencia, regras de negocio, fluxos conversacionais, confirmacoes automaticas, painel administrativo, deploy e testes.

Com base no historico do Git, no volume de codigo e na complexidade das entregas, a estimativa realista de desenvolvimento fica entre 106h e 170h, com ponto medio recomendado de 130h. Usando R$ 120/h como referencia, o custo estimado do tempo de desenvolvimento e de aproximadamente R$ 15.600,00.
