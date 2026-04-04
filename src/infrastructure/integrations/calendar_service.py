"""Servico de integracao com o Google Calendar."""

import os
import threading
import unicodedata
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from ...domain.policies.phone_service import build_phone_search_term, normalize_internal_phone
from ..config.config_service import ConfigService

SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")
_APPOINTMENT_CREATION_LOCK = threading.Lock()


class CalendarService:
    """Gerencia operacoes de leitura e escrita no Google Calendar."""

    SCOPES = ["https://www.googleapis.com/auth/calendar"]

    def __init__(self) -> None:
        self.config = ConfigService()
        self.calendar_id = self.config.get_calendar_id()
        self._service = None

    def _get_service(self):
        """Inicializa o servico do Google Calendar sob demanda."""
        if self._service is None:
            creds_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "./credentials/service-account.json")

            if os.path.exists(creds_file):
                credentials = Credentials.from_service_account_file(creds_file, scopes=self.SCOPES)
            else:
                client_email = os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL")
                private_key = os.getenv("GOOGLE_PRIVATE_KEY")

                if client_email and private_key:
                    creds_info = {
                        "type": "service_account",
                        "client_email": client_email,
                        "private_key": private_key.replace("\\n", "\n"),
                        "token_uri": "https://oauth2.googleapis.com/token",
                    }
                    credentials = Credentials.from_service_account_info(creds_info, scopes=self.SCOPES)
                else:
                    raise FileNotFoundError(f"Credenciais do Google nao encontradas: {creds_file}")

            self._service = build("calendar", "v3", credentials=credentials)
        return self._service

    @staticmethod
    def _normalize_datetime(value: datetime) -> datetime:
        """Normaliza datetimes para o timezone de Sao Paulo."""
        if value.tzinfo is None:
            return value.replace(tzinfo=SAO_PAULO_TZ)
        return value.astimezone(SAO_PAULO_TZ)

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        """Normaliza para o formato interno do sistema."""
        return normalize_internal_phone(phone)

    @staticmethod
    def _normalize_period(period: str) -> str:
        """Normaliza nomes de periodo para comparacoes robustas."""
        normalized = unicodedata.normalize("NFKD", period)
        return normalized.encode("ascii", "ignore").decode("ascii").lower().strip()

    def _iter_period_bounds(self) -> list[tuple[time, time]]:
        """Retorna os horarios configurados para atendimento."""
        bounds = []
        for period_data in self.config.get_periods().values():
            hour_start, minute_start = map(int, period_data["start"].split(":"))
            hour_end, minute_end = map(int, period_data["end"].split(":"))
            bounds.append((time(hour_start, minute_start), time(hour_end, minute_end)))
        return bounds

    def _is_within_business_hours(self, start_time: datetime, end_time: datetime) -> bool:
        """Valida se o intervalo solicitado cabe dentro de algum periodo configurado."""
        for period_start, period_end in self._iter_period_bounds():
            start_candidate = datetime.combine(start_time.date(), period_start).replace(
                tzinfo=SAO_PAULO_TZ
            )
            end_candidate = datetime.combine(start_time.date(), period_end).replace(
                tzinfo=SAO_PAULO_TZ
            )
            if start_time >= start_candidate and end_time <= end_candidate:
                return True
        return False

    def get_events(
        self,
        date: datetime,
        time_min: Optional[time] = None,
        time_max: Optional[time] = None,
    ) -> list[dict]:
        """Retorna todos os eventos de um determinado dia e periodo."""
        service = self._get_service()
        date_sp = self._normalize_datetime(date)
        start_of_day = datetime.combine(date_sp.date(), time_min or time(0, 0)).replace(tzinfo=SAO_PAULO_TZ)
        end_of_day = datetime.combine(date_sp.date(), time_max or time(23, 59, 59)).replace(tzinfo=SAO_PAULO_TZ)

        events_result = (
            service.events()
            .list(
                calendarId=self.calendar_id,
                timeMin=start_of_day.isoformat(),
                timeMax=end_of_day.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        return events_result.get("items", [])

    def get_available_slots(self, date: datetime, period: Optional[str] = None) -> list[dict]:
        """Retorna slots disponiveis em um dia e periodo."""
        slot_duration = self.config.get_slot_duration()
        configured_periods = self.config.get_periods()
        periods = {
            self._normalize_period(period_name): period_data
            for period_name, period_data in configured_periods.items()
        }

        normalized_period = self._normalize_period(period) if period else None

        if normalized_period and normalized_period in periods:
            period_data = periods[normalized_period]
            hour_start, minute_start = map(int, period_data["start"].split(":"))
            hour_end, minute_end = map(int, period_data["end"].split(":"))
            period_start = time(hour_start, minute_start)
            period_end = time(hour_end, minute_end)
        else:
            all_starts = []
            all_ends = []
            for period_data in periods.values():
                hour, minute = map(int, period_data["start"].split(":"))
                all_starts.append(time(hour, minute))
                hour, minute = map(int, period_data["end"].split(":"))
                all_ends.append(time(hour, minute))
            period_start = min(all_starts)
            period_end = max(all_ends)

        events = self.get_events(date, period_start, period_end)
        busy_intervals = []
        for event in events:
            start_str = event.get("start", {}).get("dateTime")
            end_str = event.get("end", {}).get("dateTime")

            if start_str and end_str:
                busy_intervals.append(
                    (
                        self._normalize_datetime(datetime.fromisoformat(start_str)),
                        self._normalize_datetime(datetime.fromisoformat(end_str)),
                    )
                )
            elif event.get("start", {}).get("date"):
                return []

        date_sp = self._normalize_datetime(date)
        available = []
        current = datetime.combine(date_sp.date(), period_start).replace(tzinfo=SAO_PAULO_TZ)
        end_limit = datetime.combine(date_sp.date(), period_end).replace(tzinfo=SAO_PAULO_TZ)
        now_sp = datetime.now(SAO_PAULO_TZ)

        while current + timedelta(minutes=slot_duration) <= end_limit:
            slot_end = current + timedelta(minutes=slot_duration)
            is_busy = False

            for busy_start, busy_end in busy_intervals:
                if current < busy_end and slot_end > busy_start:
                    is_busy = True
                    break

            if not is_busy and current > now_sp:
                available.append(
                    {
                        "start": current,
                        "end": slot_end,
                        "formatted": current.strftime("%d/%m/%Y as %H:%M"),
                    }
                )

            current += timedelta(minutes=slot_duration)

        return available

    def _slot_conflicts(self, start_time: datetime, end_time: datetime) -> bool:
        """Verifica se existe qualquer conflito de agenda no intervalo informado."""
        events = self.get_events(start_time, start_time.time(), end_time.time())

        for event in events:
            start_str = event.get("start", {}).get("dateTime")
            end_str = event.get("end", {}).get("dateTime")

            if start_str and end_str:
                event_start = self._normalize_datetime(datetime.fromisoformat(start_str))
                event_end = self._normalize_datetime(datetime.fromisoformat(end_str))
                if start_time < event_end and end_time > event_start:
                    return True
            elif event.get("start", {}).get("date"):
                return True

        return False

    def create_appointment(self, patient_name: str, patient_phone: str, start_time: datetime) -> dict:
        """Cria um evento de consulta no Google Calendar."""
        service = self._get_service()
        slot_duration = self.config.get_slot_duration()
        start_sp = self._normalize_datetime(start_time)
        end_sp = start_sp + timedelta(minutes=slot_duration)
        normalized_phone = self._normalize_phone(patient_phone)

        event_body = {
            "summary": f"{patient_name} - {normalized_phone}",
            "start": {
                "dateTime": start_sp.isoformat(),
                "timeZone": "America/Sao_Paulo",
            },
            "end": {
                "dateTime": end_sp.isoformat(),
                "timeZone": "America/Sao_Paulo",
            },
            "description": (
                "Agendamento automatico via WhatsApp\n"
                f"Paciente: {patient_name}\n"
                f"Telefone: {normalized_phone}"
            ),
        }

        return (
            service.events()
            .insert(calendarId=self.calendar_id, body=event_body)
            .execute()
        )

    def create_appointment_if_available(
        self,
        patient_name: str,
        patient_phone: str,
        start_time: datetime,
    ) -> dict:
        """Cria um evento somente se o slot ainda estiver livre."""
        slot_duration = self.config.get_slot_duration()
        start_sp = self._normalize_datetime(start_time)
        end_sp = start_sp + timedelta(minutes=slot_duration)
        now_sp = datetime.now(SAO_PAULO_TZ)

        if start_sp <= now_sp:
            raise ValueError("O horario informado ja esta no passado.")
        if start_sp.weekday() >= 5:
            raise ValueError("A clinica nao atende aos finais de semana.")
        if start_sp.minute % slot_duration != 0 or start_sp.second != 0:
            raise ValueError(
                f"O horario deve respeitar intervalos de {slot_duration} minutos."
            )
        if not self._is_within_business_hours(start_sp, end_sp):
            raise ValueError("O horario informado esta fora dos periodos de atendimento.")

        max_date = (now_sp + timedelta(days=self.config.get_max_days_ahead())).date()
        if start_sp.date() > max_date:
            raise ValueError(
                f"O agendamento so pode ser feito ate {self.config.get_max_days_ahead()} dias a frente."
            )

        with _APPOINTMENT_CREATION_LOCK:
            if self._slot_conflicts(start_sp, end_sp):
                raise ValueError(
                    f"O horario {start_sp.strftime('%d/%m/%Y %H:%M')} nao esta mais disponivel."
                )
            return self.create_appointment(patient_name, patient_phone, start_sp)

    def cancel_appointment(self, event_id: str) -> bool:
        """Cancela um evento do Google Calendar."""
        try:
            service = self._get_service()
            service.events().delete(calendarId=self.calendar_id, eventId=event_id).execute()
            return True
        except Exception:
            return False

    def find_appointment_by_patient(self, patient_name: str, patient_phone: str) -> Optional[dict]:
        """Busca a proxima consulta de um paciente pelo nome e telefone."""
        events = self.find_appointments_by_phone(patient_phone)
        patient_name_lower = patient_name.lower().strip()

        for event in events:
            summary = event.get("summary", "").lower()
            if patient_name_lower and patient_name_lower in summary:
                return event

        return events[0] if len(events) == 1 else None

    def find_appointments_by_phone(self, phone: str) -> list[dict]:
        """Busca todas as consultas futuras de um paciente pelo telefone."""
        service = self._get_service()
        now = datetime.now(SAO_PAULO_TZ).isoformat()
        phone_digits = self._normalize_phone(phone)
        search_term = build_phone_search_term(phone)

        events_result = (
            service.events()
            .list(
                calendarId=self.calendar_id,
                timeMin=now,
                maxResults=20,
                singleEvents=True,
                orderBy="startTime",
                q=search_term or phone,
            )
            .execute()
        )

        matched_events = []
        for event in events_result.get("items", []):
            summary_digits = self._normalize_phone(event.get("summary", ""))
            if not search_term:
                continue
            if (
                summary_digits == phone_digits
                or summary_digits.endswith(search_term)
                or phone_digits.endswith(summary_digits)
            ):
                matched_events.append(event)

        return matched_events
