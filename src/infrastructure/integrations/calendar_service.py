"""Servico de integracao com o Google Calendar."""

import base64
import binascii
import json
import os
import threading
import unicodedata
from datetime import datetime, time, timedelta
from typing import Any, Optional
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
    DEFAULT_CREDENTIALS_PATH = "./credentials/service-account.json"
    CONTAINER_CREDENTIALS_PATH = "/app/credentials/service-account.json"

    def __init__(self) -> None:
        self.config = ConfigService()
        self.calendar_id = self.config.get_calendar_id()
        self._service = None

    @classmethod
    def _build_credentials_candidates(cls) -> tuple[str, list[str]]:
        """Monta os caminhos candidatos para as credenciais do Google."""
        configured_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
        candidates = []

        if configured_path:
            candidates.append(configured_path)

        for fallback_path in (cls.CONTAINER_CREDENTIALS_PATH, cls.DEFAULT_CREDENTIALS_PATH):
            if fallback_path not in candidates:
                candidates.append(fallback_path)

        reported_path = configured_path or cls.DEFAULT_CREDENTIALS_PATH
        return reported_path, candidates

    @classmethod
    def _resolve_credentials_file(cls) -> str | None:
        """Resolve o primeiro arquivo de credenciais existente no ambiente."""
        _, candidates = cls._build_credentials_candidates()
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        return None

    @staticmethod
    def _strip_wrapping_quotes(value: str) -> str:
        """Remove aspas externas quando o painel salva o valor como string literal."""
        cleaned = (value or "").strip()
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
            return cleaned[1:-1].strip()
        return cleaned

    @classmethod
    def _parse_service_account_json(cls, raw_value: str, source_name: str) -> dict[str, Any]:
        """Interpreta um JSON de service account e valida o tipo basico do payload."""
        cleaned = cls._strip_wrapping_quotes(raw_value)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Conteudo invalido em {source_name}. "
                "Informe o JSON completo da service account ou um base64 valido desse JSON."
            ) from exc

        if not isinstance(parsed, dict):
            raise ValueError(
                f"Conteudo invalido em {source_name}. O payload precisa ser um objeto JSON."
            )
        return parsed

    @classmethod
    def _load_credentials_from_json_env(cls) -> dict[str, Any] | None:
        """Carrega credenciais completas a partir de JSON ou base64 no ambiente."""
        raw_json = cls._strip_wrapping_quotes(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", ""))
        raw_json_base64 = cls._strip_wrapping_quotes(
            os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_BASE64", "")
        )

        if raw_json_base64:
            if raw_json_base64.startswith("{"):
                return cls._parse_service_account_json(
                    raw_json_base64,
                    "GOOGLE_SERVICE_ACCOUNT_JSON_BASE64",
                )

            try:
                decoded_json = base64.b64decode(raw_json_base64, validate=True).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError):
                return cls._parse_service_account_json(
                    raw_json_base64,
                    "GOOGLE_SERVICE_ACCOUNT_JSON_BASE64",
                )

            return cls._parse_service_account_json(
                decoded_json,
                "GOOGLE_SERVICE_ACCOUNT_JSON_BASE64",
            )

        if raw_json:
            return cls._parse_service_account_json(raw_json, "GOOGLE_SERVICE_ACCOUNT_JSON")

        return None

    @staticmethod
    def _load_credentials_from_minimal_env() -> dict[str, Any] | None:
        """Carrega credenciais a partir de email e chave privada no ambiente."""
        client_email = os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL")
        private_key = os.getenv("GOOGLE_PRIVATE_KEY")

        if not client_email or not private_key:
            return None

        return {
            "type": "service_account",
            "client_email": client_email,
            "private_key": private_key.replace("\\n", "\n"),
            "token_uri": "https://oauth2.googleapis.com/token",
        }

    def _get_service(self):
        """Inicializa o servico do Google Calendar sob demanda."""
        if self._service is None:
            reported_path, candidates = self._build_credentials_candidates()
            creds_file = self._resolve_credentials_file()

            if creds_file:
                credentials = Credentials.from_service_account_file(creds_file, scopes=self.SCOPES)
            else:
                creds_info = self._load_credentials_from_json_env()
                if creds_info is None:
                    creds_info = self._load_credentials_from_minimal_env()

                if creds_info is not None:
                    credentials = Credentials.from_service_account_info(creds_info, scopes=self.SCOPES)
                else:
                    checked_paths = ", ".join(candidates)
                    raise FileNotFoundError(
                        "Credenciais do Google nao encontradas. "
                        f"GOOGLE_SERVICE_ACCOUNT_FILE atual: {reported_path}. "
                        f"Caminhos verificados: {checked_paths}. "
                        "Monte o arquivo no container ou configure "
                        "GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_SERVICE_ACCOUNT_JSON_BASE64, "
                        "ou GOOGLE_SERVICE_ACCOUNT_EMAIL e GOOGLE_PRIVATE_KEY."
                    )

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

    @staticmethod
    def _extract_description_field(description: str, field_name: str) -> str:
        """Extrai um campo simples do texto padrao salvo na descricao do evento."""
        expected_prefix = f"{field_name}:".lower()
        for raw_line in (description or "").splitlines():
            line = raw_line.strip()
            if line.lower().startswith(expected_prefix):
                return line.split(":", 1)[1].strip()
        return ""

    @classmethod
    def _extract_patient_phone_from_event(cls, event: dict[str, Any]) -> str:
        """Tenta localizar o telefone do paciente no resumo ou descricao do evento."""
        description = str(event.get("description", "") or "")
        summary = str(event.get("summary", "") or "")

        candidates = [
            cls._extract_description_field(description, "Telefone"),
            summary.split(" - ", 1)[1].strip() if " - " in summary else "",
            summary,
        ]
        for candidate in candidates:
            normalized = cls._normalize_phone(candidate)
            if len(normalized) >= 10:
                return normalized[-11:]
        return ""

    @classmethod
    def _extract_patient_name_from_event(cls, event: dict[str, Any]) -> str:
        """Resolve o nome do paciente salvo no evento do Google Calendar."""
        description = str(event.get("description", "") or "")
        summary = str(event.get("summary", "") or "")

        description_name = cls._extract_description_field(description, "Paciente")
        if description_name:
            return description_name

        if " - " in summary:
            return summary.split(" - ", 1)[0].strip()
        return summary.strip()

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

        all_items: list[dict] = []
        page_token: str | None = None
        while True:
            events_result = (
                service.events()
                .list(
                    calendarId=self.calendar_id,
                    timeMin=start_of_day.isoformat(),
                    timeMax=end_of_day.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    pageToken=page_token,
                )
                .execute()
            )
            all_items.extend(events_result.get("items", []))
            page_token = events_result.get("nextPageToken")
            if not page_token:
                break
        return all_items

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
        clean_name = (patient_name or "").strip()
        if not clean_name or clean_name.lower() == "paciente" or clean_name.replace("+", "").isdigit():
            raise ValueError("Informe o nome completo do paciente antes de agendar.")

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

    def find_patient_appointments_for_date(self, date: datetime) -> list[dict[str, Any]]:
        """Lista consultas de pacientes em uma data, com nome e telefone normalizados."""
        date_sp = self._normalize_datetime(date)
        appointments = []

        for event in self.get_events(date_sp):
            if str(event.get("status", "")).lower() == "cancelled":
                continue

            start_str = event.get("start", {}).get("dateTime")
            end_str = event.get("end", {}).get("dateTime")
            if not start_str or not end_str:
                continue

            start_time = self._normalize_datetime(datetime.fromisoformat(start_str))
            end_time = self._normalize_datetime(datetime.fromisoformat(end_str))
            if start_time.date() != date_sp.date():
                continue

            patient_phone = self._extract_patient_phone_from_event(event)
            if not patient_phone:
                continue

            appointments.append(
                {
                    "event_id": str(event.get("id", "")).strip(),
                    "patient_name": self._extract_patient_name_from_event(event),
                    "patient_phone": patient_phone,
                    "start_time": start_time,
                    "end_time": end_time,
                    "raw_event": event,
                }
            )

        appointments.sort(key=lambda item: item["start_time"])
        return appointments

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
