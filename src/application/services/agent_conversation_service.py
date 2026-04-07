"""Agente ReAct com LLM nativo para atendimento dental via WhatsApp.

Usa apenas langchain-core + langchain-openai, sem depender do pacote 'langchain'.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
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

_MAX_ITERATIONS = 10


def _wrap_tool(instance: Any) -> StructuredTool:
    """Converte uma tool local em StructuredTool do LangChain."""
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


# ── System prompt ────────────────────────────────────────────────────────────

def _build_procedure_rules_text(config: ConfigService) -> str:
    rules = config.get_procedure_rules()
    if not rules:
        return ""
    not_performed, private_only, card_required = [], [], []
    for rule in rules:
        label = str(rule.get("label", "")).strip()
        allowed = rule.get("allowed_plans", [])
        if rule.get("not_performed", False):
            not_performed.append(label)
        elif allowed and len(allowed) == 1 and str(allowed[0]).lower() == "particular":
            private_only.append(label)
        elif rule.get("requires_card_photo", False):
            card_required.append(f"{label} ({', '.join(str(p) for p in allowed)})")
    parts = []
    if not_performed:
        parts.append("NÃO realizamos:\n" + "\n".join(f"- {p}" for p in not_performed))
    if private_only:
        parts.append("Somente no particular:\n" + "\n".join(f"- {p}" for p in private_only))
    if card_required:
        parts.append("Exige foto da carteirinha:\n" + "\n".join(f"- {p}" for p in card_required))
    return "\n\n".join(parts)


def _build_plans_text(config: ConfigService) -> tuple[str, str]:
    direct, referral = [], []
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
    labels = {"manhã": "Manhã", "manha": "Manhã", "tarde": "Tarde", "noite": "Noite"}
    return "\n".join(
        f"- {labels.get(k, k.capitalize())}: {v.get('start', '?')}–{v.get('end', '?')}"
        for k, v in config.get_periods().items()
    )


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

    referral_section = (
        f"\n## Convênios com encaminhamento (NÃO atendemos diretamente)\n{referral_plans}"
        if referral_plans else ""
    )

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
{direct_plans}{referral_section}

## Regras de procedimentos
{procedure_rules}

## Regras de agendamento
- Slots de {slot_duration} minutos, {working_days}
- Mínimo: {min_days} dias úteis de antecedência; Máximo: {max_days} dias à frente
- Períodos:
{periods_text}

## Como usar as ferramentas
1. Comece buscando o paciente com `buscar_paciente`
2. Valide todo convênio com `verificar_convenio` antes de confirmar que atende
3. Para agendar sem data: `buscar_proximo_dia_disponivel`; com data: `buscar_horarios_disponiveis`
4. Ofereça no máximo 2 opções. Confirme com o paciente ANTES de `criar_agendamento`
5. Após criar/remarcar: chame `salvar_paciente` e `registrar_interacao`
6. Para cancelar: use `consultar_agendamento` para obter o event_id

## Regras importantes
- Nunca confirme convênio sem usar `verificar_convenio`
- Se convênio não encontrado: informe os planos aceitos e peça para verificar o nome
- Paciente menor de {min_age} anos: informe que atendemos a partir de {min_age} anos
- Planos de encaminhamento: informe que é atendido pela profissional parceira""".strip()

    if confirmation_context:
        prompt += f"\n\n{confirmation_context}"
    return prompt


def _build_confirmation_context(state: Any) -> str:
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
    return f"""## CONTEXTO: Confirmação de consulta pendente
Você enviou um lembrete ao paciente sobre a consulta de AMANHÃ:
- Data: {date_str.strip()} | Horário: {time_str.strip()} | ID: {event_id}

Interprete a mensagem com esse contexto:
- Confirmar (sim, vou, confirmo, etc.): responda que está confirmado. NÃO crie nem cancele nada.
- Remarcar: cancele com `cancelar_agendamento` e agende novo horário
- Cancelar: use `cancelar_agendamento` com o ID acima
- Cumprimento social (boa noite, etc.): responda com simpatia e pergunte se confirma a consulta""".strip()


# ── Conversor de histórico ───────────────────────────────────────────────────

def _convert_history(history_text: str | None) -> list:
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


# ── Serviço principal ────────────────────────────────────────────────────────

class AgentConversationService:
    """Agente ReAct com loop de tool-calling nativo — sem keywords, entende contexto natural."""

    def __init__(self) -> None:
        self.config = ConfigService()
        self._tools = _build_tools()
        self._tool_map = {t.name: t for t in self._tools}

        llm = ChatOpenAI(
            model=os.getenv("AGENT_OPENAI_MODEL", self.config.get_openai_model()),
            temperature=0,
        )
        self._llm = llm.bind_tools(self._tools)

    @staticmethod
    def enabled() -> bool:
        return os.getenv("CONVERSATION_ENGINE", "").strip().lower() == "agent"

    def _run_loop(self, messages: list) -> str:
        """Executa o loop ReAct: LLM → tool calls → resultado → LLM → ... → resposta final."""
        for iteration in range(_MAX_ITERATIONS):
            response: AIMessage = self._llm.invoke(messages)

            if not response.tool_calls:
                return str(response.content).strip()

            logger.debug(
                "Agente — iteração %d | tools: %s",
                iteration + 1,
                [tc["name"] for tc in response.tool_calls],
            )

            messages.append(response)
            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]
                tool = self._tool_map.get(tool_name)
                if tool is None:
                    result = f"Erro: ferramenta '{tool_name}' não encontrada."
                else:
                    try:
                        result = str(tool.invoke(tool_args))
                    except Exception as exc:
                        result = f"Erro ao executar '{tool_name}': {exc}"
                        logger.warning("Tool %s falhou: %s", tool_name, exc)

                messages.append(ToolMessage(content=result, tool_call_id=tool_call["id"]))

        logger.warning("Agente atingiu limite de %d iterações sem resposta final.", _MAX_ITERATIONS)
        return "Desculpe, tive um problema interno. A doutora será avisada em breve."

    def process_message(
        self,
        patient_phone: str,
        patient_message: str,
        patient_name: str = "",
        history_text: str | None = None,
        is_first_message: bool | None = None,
    ) -> str:
        state = ConversationStateService.get(patient_phone)

        confirmation_context = ""
        if state.stage == AppointmentConfirmationService.CONFIRMATION_STAGE:
            confirmation_context = _build_confirmation_context(state)

        system_prompt = _build_system_prompt(self.config, confirmation_context)

        messages: list = [SystemMessage(content=system_prompt)]
        messages.extend(_convert_history(history_text))
        messages.append(HumanMessage(content=patient_message))

        response = self._run_loop(messages)

        if not response:
            raise RuntimeError("Agente ReAct não produziu resposta.")

        if state.stage == AppointmentConfirmationService.CONFIRMATION_STAGE:
            ConversationStateService.clear(patient_phone)

        return response
