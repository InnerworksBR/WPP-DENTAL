"""Tool CrewAI para operacoes com o Google Calendar."""

from datetime import datetime, timedelta
from typing import Optional, Type

from pydantic import BaseModel, Field

from ...domain.policies.phone_service import normalize_internal_phone
from ...infrastructure.config.config_service import ConfigService
from ...infrastructure.integrations.calendar_service import CalendarService, SAO_PAULO_TZ


class GetAvailableSlotsInput(BaseModel):
    """Input para buscar horarios disponiveis."""

    date: str = Field(..., description="Data no formato DD/MM/YYYY")
    period: Optional[str] = Field(
        None,
        description="Periodo do dia: 'manha', 'tarde' ou 'noite'. Se nao informado, retorna o dia inteiro.",
    )


class GetAvailableSlotsTool:
    """Busca horarios disponiveis no Google Calendar da doutora."""

    name: str = "buscar_horarios_disponiveis"
    description: str = (
        "Busca horarios disponiveis para agendamento no Google Calendar. "
        "Retorna slots de 15 minutos que estao livres. "
        "A data deve estar no formato DD/MM/YYYY. "
        "O periodo pode ser 'manha', 'tarde' ou 'noite'."
    )
    args_schema: Type[BaseModel] = GetAvailableSlotsInput

    def _run(self, date: str, period: Optional[str] = None) -> str:
        try:
            dt = datetime.strptime(date, "%d/%m/%Y")
        except ValueError:
            return "Erro: Data invalida. Use o formato DD/MM/YYYY."

        if dt.weekday() >= 5:
            day_name = "sabado" if dt.weekday() == 5 else "domingo"
            return f"Erro: {date} e {day_name}. A clinica nao atende aos finais de semana."

        service = CalendarService()
        slots = service.get_available_slots(dt, period)

        if not slots:
            suffix = f" no periodo da {period}" if period else ""
            return (
                f"Nao encontrei horarios disponiveis em {date}{suffix} 😕\n"
                "Esse dia pode ja estar preenchido ou bloqueado."
            )

        config = ConfigService()
        selected = slots[:config.get_suggestions_count()]

        result = f"Encontrei estes horarios disponiveis em {date} 😊"
        if period:
            result += f" ({period})"
        result += ":\n"

        for index, slot in enumerate(selected, 1):
            result += f"  {index}. {slot['formatted']}\n"

        return result


class FindNextAvailableDayInput(BaseModel):
    """Input para buscar o proximo dia util disponivel."""

    period: Optional[str] = Field(
        None,
        description="Periodo do dia: 'manha', 'tarde' ou 'noite'. Se nao informado, retorna o dia inteiro.",
    )
    min_business_days: int = Field(
        2,
        description="Minimo de dias uteis a partir de hoje para comecar a buscar.",
    )


class FindNextAvailableDayTool:
    """Busca o proximo dia util com horarios disponiveis."""

    name: str = "buscar_proximo_dia_disponivel"
    description: str = (
        "Busca o proximo dia util que tenha horarios disponiveis. "
        "Comeca a busca respeitando a janela minima configurada para encaixes. "
        "Retorna a quantidade configurada de opcoes de horarios no periodo solicitado."
    )
    args_schema: Type[BaseModel] = FindNextAvailableDayInput

    def _run(self, period: Optional[str] = None, min_business_days: int = 2) -> str:
        try:
            config = ConfigService()
            service = CalendarService()
            target = datetime.now(SAO_PAULO_TZ)
            min_business_days = max(min_business_days, config.get_min_business_days_ahead())
            suggestions_count = config.get_suggestions_count()
            max_days_ahead = config.get_max_days_ahead()

            if period:
                period = period.lower().strip()

            business_days_counted = 0
            while business_days_counted < min_business_days:
                target += timedelta(days=1)
                if target.weekday() < 5:
                    business_days_counted += 1

            for _ in range(max_days_ahead):
                while target.weekday() >= 5:
                    target += timedelta(days=1)

                try:
                    slots = service.get_available_slots(target, period)
                except Exception:
                    target += timedelta(days=1)
                    continue

                if slots:
                    selected = slots[:suggestions_count]
                    day_str = target.strftime("%d/%m/%Y")
                    weekday_names = ["segunda", "terça", "quarta", "quinta", "sexta"]
                    result = (
                        f"Encontrei o proximo dia com horarios disponiveis 😊\n"
                        f"{day_str} ({weekday_names[target.weekday()]})"
                    )
                    if period:
                        result += f" - periodo da {period}"
                    result += ":\n"
                    for index, slot in enumerate(selected, 1):
                        result += f"  {index}. {slot['formatted']}\n"
                    return result

                target += timedelta(days=1)

            return (
                "Nao encontrei horarios disponiveis "
                f"nos proximos {max_days_ahead} dias."
            )
        except Exception as exc:
            return f"Erro ao buscar horarios: {exc}"


class CreateAppointmentInput(BaseModel):
    """Input para criar agendamento."""

    patient_name: str = Field(..., description="Nome completo do paciente")
    patient_phone: str = Field(..., description="Telefone do paciente")
    datetime_str: str = Field(..., description="Data e horario no formato DD/MM/YYYY HH:MM")


class CreateAppointmentTool:
    """Cria um agendamento no Google Calendar."""

    name: str = "criar_agendamento"
    description: str = (
        "Cria uma consulta no Google Calendar da doutora. "
        "Use quando o paciente escolher um dos horarios oferecidos — a escolha ja e a confirmacao, nao pergunte novamente. "
        "A data e o horario devem estar no formato DD/MM/YYYY HH:MM."
    )
    args_schema: Type[BaseModel] = CreateAppointmentInput

    def _run(self, patient_name: str, patient_phone: str, datetime_str: str) -> str:
        try:
            dt = datetime.strptime(datetime_str, "%d/%m/%Y %H:%M")
        except ValueError:
            return "Erro: Data/horario invalido. Use o formato DD/MM/YYYY HH:MM."

        service = CalendarService()
        try:
            event = service.create_appointment_if_available(patient_name, patient_phone, dt)
        except ValueError as exc:
            return f"Erro: {exc}"

        return (
            "Perfeito! 😊\n"
            "Consulta agendada com sucesso.\n"
            f"ID do evento: {event.get('id', 'N/A')}\n"
            f"Data: {dt.strftime('%d/%m/%Y')}\n"
            f"Horario: {dt.strftime('%H:%M')}\n"
            f"Paciente: {patient_name} - {normalize_internal_phone(patient_phone)}"
        )


class CancelAppointmentInput(BaseModel):
    """Input para cancelar agendamento."""

    patient_name: str = Field(..., description="Nome do paciente")
    patient_phone: str = Field(..., description="Telefone do paciente")
    event_id: Optional[str] = Field(
        None,
        description="ID exato do evento a cancelar. Prefira informar apos consultar_agendamento.",
    )


class CancelAppointmentTool:
    """Cancela um agendamento no Google Calendar."""

    name: str = "cancelar_agendamento"
    description: str = (
        "Cancela uma consulta existente do paciente no Google Calendar. "
        "Quando houver mais de uma consulta futura, use o event_id correto retornado por consultar_agendamento."
    )
    args_schema: Type[BaseModel] = CancelAppointmentInput

    def _run(
        self,
        patient_name: str,
        patient_phone: str,
        event_id: Optional[str] = None,
    ) -> str:
        service = CalendarService()
        events = service.find_appointments_by_phone(patient_phone)

        if not events:
            return "Nao encontrei nenhuma consulta futura para este paciente."

        event = None
        if event_id:
            event = next((item for item in events if item.get("id") == event_id), None)
            if event is None:
                return "Erro: nao encontrei esse ID de consulta para este telefone."
        else:
            patient_name_lower = patient_name.lower().strip()
            named_events = [
                item for item in events
                if patient_name_lower and patient_name_lower in item.get("summary", "").lower()
            ]
            if len(named_events) == 1:
                event = named_events[0]
            elif len(events) == 1:
                event = events[0]
            else:
                return (
                    "Erro: existe mais de uma consulta futura para este telefone. "
                    "Use consultar_agendamento e cancele informando o event_id correto."
                )

        current_event_id = event.get("id")
        start_str = event.get("start", {}).get("dateTime", "")

        if start_str:
            dt = datetime.fromisoformat(start_str)
            date_str = dt.strftime("%d/%m/%Y")
            time_str = dt.strftime("%H:%M")
        else:
            date_str = "N/A"
            time_str = "N/A"

        success = service.cancel_appointment(current_event_id)
        if success:
            return (
                "Prontinho! 😊\n"
                "Consulta cancelada com sucesso.\n"
                f"Data: {date_str}\n"
                f"Horario: {time_str}"
            )
        return "Erro ao cancelar a consulta. Tente novamente."


class FindAppointmentInput(BaseModel):
    """Input para consultar agendamento."""

    patient_phone: str = Field(..., description="Telefone do paciente")


class FindAppointmentTool:
    """Consulta as proximas consultas de um paciente."""

    name: str = "consultar_agendamento"
    description: str = (
        "Busca consultas futuras de um paciente pelo telefone. "
        "Inclui o ID do evento para cancelamento e remarcacao seguros."
    )
    args_schema: Type[BaseModel] = FindAppointmentInput

    def _run(self, patient_phone: str) -> str:
        service = CalendarService()
        events = service.find_appointments_by_phone(patient_phone)

        if not events:
            return "Nao encontrei nenhuma consulta futura para este telefone."

        result = "Encontrei estas consultas futuras 😊:\n"
        for event in events:
            start_str = event.get("start", {}).get("dateTime", "")
            if start_str:
                dt = datetime.fromisoformat(start_str)
                result += (
                    f"  - ID: {event.get('id', 'N/A')} | "
                    f"{dt.strftime('%d/%m/%Y as %H:%M')}\n"
                )

        return result
