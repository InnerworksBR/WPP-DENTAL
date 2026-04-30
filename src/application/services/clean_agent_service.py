"""Motor de conversa limpo: LLM com function calling + ferramentas determinísticas.

Um único engine. Sem state machine de strings. Sem heurísticas de keyword.
O histórico da conversa é o estado. As ferramentas executam ações reais.
"""

from __future__ import annotations

import logging
import os
import re
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
_SLOT_DATE_RE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
_SLOT_TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_SLOT_TOOLS = {"buscar_horarios_disponiveis", "buscar_proximo_dia_disponivel"}


def _parse_offered_slots(result: str) -> tuple[str, list[str]] | None:
    date_m = _SLOT_DATE_RE.search(result)
    times = _SLOT_TIME_RE.findall(result)
    if not date_m or not times:
        return None
    return date_m.group(1), [f"{int(h):02d}:{m}" for h, m in times]


def _is_offered_slot(datetime_str: str, state: Any) -> bool:
    if not state.offered_date or not state.offered_times:
        return True
    try:
        dt = datetime.strptime(datetime_str, "%d/%m/%Y %H:%M")
        return (
            dt.strftime("%d/%m/%Y") == state.offered_date
            and dt.strftime("%H:%M") in state.offered_times
        )
    except ValueError:
        return True


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

def _build_system_prompt(config: ConfigService, patient_phone: str, greeting_template: str = "") -> str:
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

    _pt_weekdays = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
    now = datetime.now(SAO_PAULO_TZ)
    today = f"{now.strftime('%d/%m/%Y')} ({_pt_weekdays[now.weekday()]}) — {now.strftime('%H:%M')} (horário de Brasília)"

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
   - Se retornar **paciente encontrado**: use o nome cadastrado em todas as operações
   - Se retornar **paciente não encontrado**: pergunte imediatamente o nome completo do paciente antes de continuar qualquer outra etapa. Não use apelidos ou nomes abreviados — aguarde a resposta.
   - NUNCA use o nome de exibição do WhatsApp como nome do paciente. O único nome válido é o retornado por `buscar_paciente` ou o que o próprio paciente informar nesta conversa.
2. `verificar_convenio` SEMPRE antes de confirmar que atende um plano
3. Antes de buscar horários, pergunte: o paciente tem data específica em mente ou prefere o primeiro disponível? E qual período prefere (manhã ou tarde)?
4. Com data específica: `buscar_horarios_disponiveis`; sem data: `buscar_proximo_dia_disponivel`
5. Ofereça EXATAMENTE 2 opções de horário — nunca mais que 2
6. Quando o paciente escolher um dos horários oferecidos, chame `criar_agendamento` IMEDIATAMENTE — não peça confirmação adicional, a escolha já é a confirmação
7. Após criar/remarcar: `salvar_paciente` e `registrar_interacao`
8. Para cancelar: `consultar_agendamento` para obter o event_id

## Regras importantes
- Convênio de encaminhamento: informe que é atendido pela profissional parceira, encerre
- Se `criar_agendamento` retornar erro de indisponibilidade: avise o paciente e pergunte se quer ver outro dia — NÃO re-busque automaticamente
- Paciente menor de {min_age} anos: informe que atendemos a partir de {min_age} anos
- Não repita perguntas já respondidas no histórico
- ESTRITAMENTE PROIBIDO oferecer qualquer horário que não tenha sido retornado por uma ferramenta nesta conversa. Se o paciente pedir um horário não listado, informe a indisponibilidade e ofereça apenas as opções retornadas pela ferramenta.
- Após oferecer os horários disponíveis, aguarde a escolha do paciente. NÃO chame `buscar_horarios_disponiveis` nem `buscar_proximo_dia_disponivel` novamente a menos que o paciente peça explicitamente um dia diferente.
- NUNCA diga frases como "Um momento", "Aguarde", "Um segundo" ou "Vou processar". Se precisar usar uma ferramenta, chame-a imediatamente sem dar satisfação prévia. O paciente só deve ver o resultado final da operação.""".strip()

    if greeting_template:
        prompt += f"""

## Saudação Obrigatória (Início da Conversa)
Se esta for a primeira interação com o paciente agora (histórico vazio ou reiniciado), você DEVE iniciar sua resposta com exatamente este texto:
{greeting_template}
Adapte o restante da resposta para fluir naturalmente após essa saudação se o paciente já tiver feito uma pergunta específica."""

    return prompt.strip()


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

    def _run_loop(self, messages: list, patient_phone: str) -> str:
        seen_calls: set[tuple] = set()
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
                call_sig = (call["name"], str(sorted(call["args"].items())))
                if call_sig in seen_calls:
                    logger.warning("[clean_agent] loop detectado: %s repetido com mesmos args", call["name"])
                    return "Desculpe, tive uma dificuldade interna. Por favor, tente novamente ou aguarde contato da clínica."
                seen_calls.add(call_sig)

                # Validação: criar_agendamento só executa com slot previamente ofertado
                if call["name"] == "criar_agendamento":
                    state = ConversationStateService.get(patient_phone)
                    datetime_str = call["args"].get("datetime_str", "")
                    if not _is_offered_slot(datetime_str, state):
                        logger.warning(
                            "[clean_agent] %s | tentativa de agendar horário não ofertado: %s (oferta: %s %s)",
                            patient_phone, datetime_str, state.offered_date, state.offered_times,
                        )
                        result = (
                            "Erro interno: o horário solicitado não estava entre os ofertados ao paciente. "
                            "Use apenas os horários que foram apresentados e aguarde a escolha do paciente."
                        )
                        messages.append(ToolMessage(content=result, tool_call_id=call["id"]))
                        continue

                tool = self._tool_map.get(call["name"])
                if tool is None:
                    result = f"Erro: ferramenta '{call['name']}' não encontrada."
                else:
                    try:
                        result = str(tool.invoke(call["args"]))
                    except Exception as exc:
                        result = f"Erro em '{call['name']}': {exc}"
                        logger.warning("[clean_agent] tool %s falhou: %s", call["name"], exc)

                # Rastreamento: armazena slots ofertados para validação futura
                if call["name"] in _SLOT_TOOLS:
                    parsed = _parse_offered_slots(result)
                    if parsed:
                        state = ConversationStateService.get(patient_phone)
                        state.offered_date, state.offered_times = parsed
                        ConversationStateService.save(patient_phone, state)
                        logger.debug(
                            "[clean_agent] %s | slots ofertados salvos: %s %s",
                            patient_phone, state.offered_date, state.offered_times,
                        )

                # Limpa oferta após agendamento confirmado
                if call["name"] == "criar_agendamento" and "agendada com sucesso" in result:
                    state = ConversationStateService.get(patient_phone)
                    state.offered_date = ""
                    state.offered_times = []
                    ConversationStateService.save(patient_phone, state)

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
        
        greeting_template = ""
        if is_first_message or not history_text or "Nenhum historico" in history_text:
            patient = PatientService.find_by_phone(patient_phone)
            
            # Melhora a resolução do nome para evitar "Paciente" genérico
            name_to_use = ""
            if patient and patient.get("name") and patient["name"].lower() != "paciente":
                name_to_use = patient["name"]
            else:
                # Se não tem nome no banco ou é genérico, usa o nome do WhatsApp (pushName)
                name_to_use = patient_name or "Paciente"

            if patient:
                greeting_template = self.config.get_message("greeting.returning_patient", patient_name=name_to_use)
            else:
                greeting_template = self.config.get_message("greeting.new_patient", doctor_name=self.config.get_doctor_name())

        system_prompt = _build_system_prompt(self.config, patient_phone, greeting_template)

        messages: list = [SystemMessage(content=system_prompt)]
        messages.extend(_convert_history(history_text))
        messages.append(HumanMessage(content=patient_message))

        response = self._run_loop(messages, patient_phone)

        if not response:
            raise RuntimeError("CleanAgent não produziu resposta.")

        return response
