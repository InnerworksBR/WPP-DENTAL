"""Agente ReAct com LLM nativo para atendimento dental via WhatsApp."""

from __future__ import annotations

import logging
import os
from typing import Any

from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI

from .appointment_confirmation_service import AppointmentConfirmationService
from .conversation_state_service import ConversationStateService
from ...infrastructure.config.config_service import ConfigService
from ...interfaces.tools.calendar_tool import (
    CancelAppointmentTool,
    CreateAppointmentTool,
    FindAppointmentTool,
    FindNextAvailableDayTool,
    GetAvailableSlotsTool,
)
from ...interfaces.tools.config_tool import CheckPlanTool, ListPlansTool
from ...interfaces.tools.patient_tool import FindPatientTool, SaveInteractionTool, SavePatientTool

logger = logging.getLogger(__name__)


def _wrap_tool(instance: Any) -> StructuredTool:
    """Converte uma tool local (com _run, name, description, args_schema) em StructuredTool do LangChain."""
    return StructuredTool(
        name=instance.name,
        description=instance.description,
        func=instance._run,
        args_schema=instance.args_schema,
    )


def _build_tools() -> list[StructuredTool]:
    return [
        _wrap_tool(GetAvailableSlotsTool()),
        _wrap_tool(FindNextAvailableDayTool()),
        _wrap_tool(CreateAppointmentTool()),
        _wrap_tool(CancelAppointmentTool()),
        _wrap_tool(FindAppointmentTool()),
        _wrap_tool(CheckPlanTool()),
        _wrap_tool(ListPlansTool()),
        _wrap_tool(FindPatientTool()),
        _wrap_tool(SavePatientTool()),
        _wrap_tool(SaveInteractionTool()),
    ]


def _build_procedure_rules_text(config: ConfigService) -> str:
    """Formata as regras de procedimentos para o system prompt."""
    rules = config.get_procedure_rules()
    if not rules:
        return ""

    not_performed = []
    private_only = []
    card_required = []

    for rule in rules:
        label = str(rule.get("label", "")).strip()
        allowed = rule.get("allowed_plans", [])
        if rule.get("not_performed", False):
            not_performed.append(label)
        elif allowed and len(allowed) == 1 and str(allowed[0]).lower() == "particular":
            private_only.append(label)
        elif rule.get("requires_card_photo", False):
            plans_str = ", ".join(str(p) for p in allowed)
            card_required.append(f"{label} ({plans_str})")

    parts = []
    if not_performed:
        parts.append("NÃO realizamos:\n" + "\n".join(f"- {p}" for p in not_performed))
    if private_only:
        parts.append("Somente no particular (sem convênio):\n" + "\n".join(f"- {p}" for p in private_only))
    if card_required:
        parts.append(
            "Exige foto da carteirinha (apenas pelos planos indicados):\n"
            + "\n".join(f"- {p}" for p in card_required)
        )
    return "\n\n".join(parts)


def _build_plans_text(config: ConfigService) -> tuple[str, str]:
    """Retorna (planos_diretos, planos_encaminhamento) formatados."""
    direct = []
    referral = []
    for plan in config.get_plans():
        name = str(plan.get("name", "")).strip()
        if not name:
            continue
        if plan.get("referral", False):
            to = str(plan.get("referral_to", "profissional parceira")).strip()
            referral.append(f"- {name} → encaminhar para {to}")
        else:
            direct.append(f"- {name}")
    return "\n".join(direct), "\n".join(referral)


def _build_periods_text(config: ConfigService) -> str:
    periods = config.get_periods()
    lines = []
    period_labels = {"manhã": "Manhã", "manha": "Manhã", "tarde": "Tarde", "noite": "Noite"}
    for key, val in periods.items():
        label = period_labels.get(key, key.capitalize())
        lines.append(f"- {label}: {val.get('start', '?')}–{val.get('end', '?')}")
    return "\n".join(lines)


def _build_system_prompt(config: ConfigService, confirmation_context: str = "") -> str:
    doctor_name = config.get_doctor_name()
    address = config.get_doctor_address()
    min_age = config.get_min_patient_age()
    working_days = config.get_working_days()
    min_days = config.get_min_business_days_ahead()
    max_days = config.get_max_days_ahead()
    slot_duration = config.get_slot_duration()
    direct_plans, referral_plans = _build_plans_text(config)
    procedure_rules = _build_procedure_rules_text(config)
    periods_text = _build_periods_text(config)

    prompt = f"""Você é a secretária virtual da {doctor_name}, atendendo pacientes pelo WhatsApp.
Seu nome é Melody. Seja acolhedora, simpática e objetiva — como uma secretária humana.
Responda SEMPRE em português brasileiro. Mensagens curtas e diretas.

## Sua função
Ajudar pacientes a: agendar, remarcar, cancelar e consultar consultas.
Você NÃO dá conselhos clínicos, NÃO informa preços, NÃO faz diagnósticos.
Para sintomas, dores ou urgências: diga que vai encaminhar para a doutora.
Para dúvidas sobre valores: diga que a {doctor_name} entrará em contato.

## Informações da clínica
- Doutora: {doctor_name}
- Endereço: {address}
- Atendimento: {working_days}
- Idade mínima de atendimento: {min_age} anos

## Convênios aceitos (atendimento direto)
{direct_plans}
{"" if not referral_plans else chr(10) + "## Convênios com encaminhamento (NÃO atendemos diretamente)" + chr(10) + referral_plans}

## Regras de procedimentos
{procedure_rules}

## Regras de agendamento
- Slots de {slot_duration} minutos, {working_days}
- Mínimo: {min_days} dias úteis de antecedência
- Máximo: {max_days} dias à frente
- Períodos disponíveis:
{periods_text}

## Como usar as ferramentas
1. Comece buscando o paciente com `buscar_paciente` para saber se já é conhecido
2. Quando o paciente informar um convênio, SEMPRE valide com `verificar_convenio` antes de confirmar
3. Para agendar sem data específica: use `buscar_proximo_dia_disponivel`
4. Para agendar com data específica: use `buscar_horarios_disponiveis`
5. Ofereça no máximo 2 opções de horário. Confirme com o paciente ANTES de criar
6. Só chame `criar_agendamento` depois que o paciente confirmar data e hora
7. Após criar ou remarcar: chame `salvar_paciente` e `registrar_interacao`
8. Para cancelar: busque com `consultar_agendamento` para obter o event_id

## Regras importantes
- Nunca confirme um convênio sem usar `verificar_convenio`
- Se o convênio não for encontrado, informe os planos aceitos e peça para verificar o nome
- Se o paciente for menor de {min_age} anos: informe que a clínica atende a partir de {min_age} anos
- Para planos de encaminhamento: informe que esse convênio é atendido pela profissional parceira
- Não repita perguntas que já foram respondidas no histórico
""".strip()

    if confirmation_context:
        prompt += f"\n\n{confirmation_context}"

    return prompt


def _build_confirmation_context(state: Any) -> str:
    """Constrói o bloco de contexto para o stage de confirmação de consulta."""
    label = state.pending_event_label or state.reschedule_event_label
    if not label or " as " not in label:
        return ""

    date_str, time_str = label.split(" as ", 1)
    event_id = (
        state.metadata.get(AppointmentConfirmationService.METADATA_EVENT_ID_KEY)
        or state.pending_event_id
        or state.reschedule_event_id
        or ""
    )

    return f"""## CONTEXTO ATUAL: Confirmação de consulta pendente
Você enviou um lembrete ao paciente sobre a consulta de AMANHÃ:
- Data: {date_str.strip()}
- Horário: {time_str.strip()}
- ID do evento: {event_id}

O paciente está respondendo a esse lembrete agora. Interprete a mensagem com esse contexto:
- Se confirmar (sim, confirmo, vou comparecer, etc.): responda que a consulta está confirmada. NÃO crie nem cancele nada.
- Se quiser remarcar: use `consultar_agendamento`, cancele o evento atual com `cancelar_agendamento` e agende um novo horário
- Se quiser cancelar: confirme o cancelamento e use `cancelar_agendamento` com o ID acima
- Se for apenas um cumprimento ou mensagem social (boa noite, etc.): responda com simpatia e pergunte se confirma a consulta de amanhã""".strip()


def _convert_history(history_text: str | None) -> list:
    """Converte o histórico 'PACIENTE:/ASSISTENTE:' em mensagens LangChain."""
    if not history_text or not history_text.strip():
        return []

    messages = []
    for line in history_text.strip().splitlines():
        line = line.strip()
        if line.startswith("PACIENTE:"):
            content = line[len("PACIENTE:"):].strip()
            if content:
                messages.append(HumanMessage(content=content))
        elif line.startswith("ASSISTENTE:"):
            content = line[len("ASSISTENTE:"):].strip()
            if content:
                messages.append(AIMessage(content=content))
    return messages


class AgentConversationService:
    """Agente ReAct com LLM nativo — sem keywords, entende contexto natural."""

    def __init__(self) -> None:
        self.config = ConfigService()
        self._tools = _build_tools()
        self._llm = ChatOpenAI(
            model=os.getenv("AGENT_OPENAI_MODEL", self.config.get_openai_model()),
            temperature=0,
        )
        self._prompt_template = ChatPromptTemplate.from_messages([
            ("system", "{system_prompt}"),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ])
        self._agent = create_openai_tools_agent(
            llm=self._llm,
            tools=self._tools,
            prompt=self._prompt_template,
        )
        self._executor = AgentExecutor(
            agent=self._agent,
            tools=self._tools,
            max_iterations=10,
            handle_parsing_errors=True,
            verbose=bool(os.getenv("AGENT_VERBOSE", "")),
        )

    @staticmethod
    def enabled() -> bool:
        return os.getenv("CONVERSATION_ENGINE", "").strip().lower() == "agent"

    def process_message(
        self,
        patient_phone: str,
        patient_message: str,
        patient_name: str = "",
        history_text: str | None = None,
        is_first_message: bool | None = None,
    ) -> str:
        state = ConversationStateService.get(patient_phone)

        # Contexto adicional quando estamos no stage de confirmação do dia anterior
        confirmation_context = ""
        if state.stage == AppointmentConfirmationService.CONFIRMATION_STAGE:
            confirmation_context = _build_confirmation_context(state)

        system_prompt = _build_system_prompt(self.config, confirmation_context)
        chat_history = _convert_history(history_text)

        try:
            result = self._executor.invoke({
                "system_prompt": system_prompt,
                "input": patient_message,
                "chat_history": chat_history,
            })
            response = str(result.get("output", "")).strip()
        except Exception as exc:
            logger.error("Erro no agente ReAct para %s: %s", patient_phone, exc, exc_info=True)
            raise

        if not response:
            raise RuntimeError("Agente ReAct não produziu resposta.")

        # Limpa o stage de confirmação após o agente ter respondido
        if state.stage == AppointmentConfirmationService.CONFIRMATION_STAGE:
            ConversationStateService.clear(patient_phone)

        return response
