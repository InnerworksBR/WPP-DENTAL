"""Motor de conversa limpo: LLM com function calling + ferramentas determinísticas.

Um único engine. Sem state machine de strings. Sem heurísticas de keyword.
O histórico da conversa é o estado. As ferramentas executam ações reais.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI

from .appointment_confirmation_service import AppointmentConfirmationService
from .conversation_service import ConversationService
from .conversation_state_service import ConversationStateService
from .patient_service import PatientService
from ...infrastructure.config.config_service import ConfigService
from ...infrastructure.integrations.alert_service import AlertService
from ...infrastructure.integrations.calendar_service import CalendarService, SAO_PAULO_TZ
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

_MAX_ITERATIONS = 8


def _wrap(instance: Any) -> StructuredTool:
    return StructuredTool(
        name=instance.name,
        description=instance.description,
        func=instance._run,
        args_schema=instance.args_schema,
    )


def _build_tools() -> list[StructuredTool]:
    return [
        _wrap(GetAvailableSlotsTool()),
        _wrap(FindNextAvailableDayTool()),
        _wrap(CreateAppointmentTool()),
        _wrap(CancelAppointmentTool()),
        _wrap(FindAppointmentTool()),
        _wrap(CheckPlanTool()),
        _wrap(ListPlansTool()),
        _wrap(FindPatientTool()),
        _wrap(SavePatientTool()),
        _wrap(SaveInteractionTool()),
    ]


# ── System prompt ─────────────────────────────────────────────────────────────

def _build_system_prompt(config: ConfigService, patient_phone: str, confirmation_context: str = "") -> str:
    doctor_name = config.get_doctor_name()
    address = config.get_doctor_address()
    working_days = config.get_working_days()
    min_age = config.get_min_patient_age()
    min_days = config.get_min_business_days_ahead()
    max_days = config.get_max_days_ahead()
    slot_duration = config.get_slot_duration()

    # Planos
    direct_plans, referral_plans = [], []
    for plan in config.get_plans():
        name = str(plan.get("name", "")).strip()
        if not name:
            continue
        if plan.get("referral", False):
            referral_to = str(plan.get("referral_to", "profissional parceira")).strip()
            referral_plans.append(f"{name} → encaminhar para {referral_to}")
        else:
            direct_plans.append(name)
    direct_plans_text = "\n".join(f"- {p}" for p in direct_plans) or "(nenhum configurado)"
    referral_section = ""
    if referral_plans:
        referral_section = "\n\n## Convênios com encaminhamento (NÃO agendamos diretamente)\n" + "\n".join(f"- {p}" for p in referral_plans)

    # Regras de procedimentos
    proc_lines = []
    for rule in config.get_procedure_rules():
        label = str(rule.get("label", "")).strip()
        if not label:
            continue
        if rule.get("not_performed", False):
            proc_lines.append(f"- {label}: NÃO realizamos")
        elif rule.get("requires_card_photo", False):
            plans_str = ", ".join(str(p) for p in rule.get("allowed_plans", []))
            proc_lines.append(f"- {label}: exige foto da carteirinha ({plans_str})")
        else:
            plans_str = ", ".join(str(p) for p in rule.get("allowed_plans", []))
            if plans_str:
                proc_lines.append(f"- {label}: apenas pelos planos {plans_str}")
    proc_text = "\n".join(proc_lines) or "(sem regras especiais)"

    # Períodos
    period_lines = []
    labels = {"manhã": "Manhã", "manha": "Manhã", "tarde": "Tarde", "noite": "Noite"}
    for k, v in config.get_periods().items():
        period_lines.append(f"- {labels.get(k, k.capitalize())}: {v.get('start', '?')}–{v.get('end', '?')}")
    periods_text = "\n".join(period_lines)

    today = datetime.now(SAO_PAULO_TZ).strftime("%d/%m/%Y (%A)")

    prompt = f"""Você é a secretária virtual da {doctor_name}, atendendo via WhatsApp.
Seja acolhedora, simpática e objetiva. Responda em português brasileiro.
Mensagens curtas e diretas — como uma secretária humana faria.

## Hoje
{today}

## Sua função
Ajudar pacientes a: agendar, remarcar, cancelar e consultar consultas.
Fora disso: diga que vai encaminhar para a {doctor_name} e encerre.
NUNCA dê preços, diagnósticos ou orientações clínicas. Em caso de dúvida, encaminhe.

## Clínica
- Doutora: {doctor_name}
- Endereço: {address}
- Atendimento: {working_days}
- Idade mínima: {min_age} anos

## Telefone do paciente nesta conversa
{patient_phone} — já identificado pela API. NUNCA peça o número.

## Convênios aceitos (atendimento direto)
{direct_plans_text}{referral_section}

## Regras de procedimentos
{proc_text}

## Agendamento
- Slots de {slot_duration} min | Mínimo {min_days} dias úteis de antecedência | Máximo {max_days} dias
- Períodos:
{periods_text}

## Como usar as ferramentas
1. `buscar_paciente` com o telefone acima logo no início — nunca peça o número
2. `verificar_convenio` SEMPRE antes de confirmar que atende um plano
3. Para agendar sem data: `buscar_proximo_dia_disponivel`; com data: `buscar_horarios_disponiveis`
4. Ofereça no máximo 2 opções. Só chame `criar_agendamento` após o paciente confirmar
5. Após criar/remarcar: `salvar_paciente` e `registrar_interacao`
6. Para cancelar: `consultar_agendamento` para obter o event_id

## Regras importantes
- Convênio de encaminhamento: informe que é atendido pela profissional parceira, encerre
- Se `criar_agendamento` retornar erro de indisponibilidade: avise o paciente, não re-busque automaticamente
- Paciente menor de {min_age} anos: informe que atendemos a partir de {min_age} anos
- Não repita perguntas já respondidas no histórico""".strip()

    if confirmation_context:
        prompt += f"\n\n{confirmation_context}"

    return prompt


def _build_confirmation_context(state: Any) -> str:
    """Injeta contexto determinístico quando o paciente responde ao lembrete de cron."""
    label = str(state.pending_event_label or state.reschedule_event_label or "").strip()
    if not label or " as " not in label:
        return ""

    date_str, time_str = label.split(" as ", 1)
    event_id = (
        state.metadata.get(AppointmentConfirmationService.METADATA_EVENT_ID_KEY, "")
        or state.pending_event_id
        or state.reschedule_event_id
        or ""
    )

    return f"""## CONTEXTO ATIVO — Confirmação de consulta
O paciente está respondendo ao lembrete da consulta de AMANHÃ:
Data: {date_str.strip()} | Horário: {time_str.strip()} | ID: {event_id}

AÇÃO OBRIGATÓRIA — escolha exatamente uma:
- Paciente CONFIRMA (sim, vou, ok, estarei lá...): responda que está confirmado. NÃO chame nenhuma ferramenta.
- Paciente CANCELA (não, não posso, cancelar...): chame `cancelar_agendamento` com o ID acima. Após cancelar, pergunte se quer reagendar outro dia.
- Paciente quer REMARCAR (remarcar, reagendar, outro horário...): chame `cancelar_agendamento` com o ID acima, depois busque novos horários.
- Outra mensagem: responda com simpatia e pergunte se confirma, cancela ou quer remarcar.""".strip()


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


# ── Serviço principal ─────────────────────────────────────────────────────────

class CleanAgentService:
    """Motor único de conversa: LLM + tools determinísticas. Sem keywords, sem state machine."""

    def __init__(self) -> None:
        self.config = ConfigService()
        self._tools = _build_tools()
        self._tool_map = {t.name: t for t in self._tools}
        llm = ChatOpenAI(
            model=os.getenv("OPENAI_MODEL", self.config.get_openai_model()),
            temperature=0,
        )
        self._llm = llm.bind_tools(self._tools)

    def _run_loop(self, messages: list) -> str:
        for iteration in range(_MAX_ITERATIONS):
            response: AIMessage = self._llm.invoke(messages)

            if not response.tool_calls:
                return str(response.content).strip()

            logger.debug(
                "[clean_agent] iteração %d | tools: %s",
                iteration + 1,
                [tc["name"] for tc in response.tool_calls],
            )

            messages.append(response)
            for call in response.tool_calls:
                tool = self._tool_map.get(call["name"])
                if tool is None:
                    result = f"Erro: ferramenta '{call['name']}' não encontrada."
                else:
                    try:
                        result = str(tool.invoke(call["args"]))
                    except Exception as exc:
                        result = f"Erro em '{call['name']}': {exc}"
                        logger.warning("[clean_agent] tool %s falhou: %s", call["name"], exc)
                messages.append(ToolMessage(content=result, tool_call_id=call["id"]))

        logger.warning("[clean_agent] limite de %d iterações atingido.", _MAX_ITERATIONS)
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

        # Injeta contexto de confirmação de cron quando aplicável
        confirmation_context = ""
        if state.stage == AppointmentConfirmationService.CONFIRMATION_STAGE:
            confirmation_context = _build_confirmation_context(state)
            logger.info("[clean_agent] %s | stage=confirmation → contexto injetado", patient_phone)

        system_prompt = _build_system_prompt(self.config, patient_phone, confirmation_context)

        messages: list = [SystemMessage(content=system_prompt)]
        messages.extend(_convert_history(history_text))
        messages.append(HumanMessage(content=patient_message))

        response = self._run_loop(messages)

        if not response:
            raise RuntimeError("CleanAgent não produziu resposta.")

        # Limpa estado de confirmação após o agente ter respondido
        if state.stage == AppointmentConfirmationService.CONFIRMATION_STAGE:
            ConversationStateService.clear(patient_phone)
            logger.info("[clean_agent] %s | estado de confirmação limpo", patient_phone)

        return response
