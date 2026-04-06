"""Rotina de confirmacao proativa de consultas do dia seguinte."""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from datetime import datetime, time, timedelta
from typing import Any

from .conversation_service import ConversationService
from .conversation_state_service import ConversationState, ConversationStateService
from .patient_service import PatientService
from ...infrastructure.config.config_service import ConfigService
from ...infrastructure.integrations.calendar_service import CalendarService, SAO_PAULO_TZ
from ...infrastructure.integrations.whatsapp_service import WhatsAppService
from ...infrastructure.persistence.connection import get_db

logger = logging.getLogger(__name__)


class AppointmentConfirmationService:
    """Dispara e rastreia confirmacoes do dia anterior a consulta."""

    REMINDER_TYPE_DAY_BEFORE = "day_before"
    CONFIRMATION_STAGE = "awaiting_appointment_confirmation"
    REMINDER_HOUR = 20
    METADATA_TYPE_KEY = "appointment_confirmation_type"
    METADATA_EVENT_ID_KEY = "appointment_confirmation_event_id"
    METADATA_START_KEY = "appointment_confirmation_start"
    _YES_TOKENS = (
        "sim",
        "confirmo",
        "confirmada",
        "confirmado",
        "vou",
        "compareco",
        "consigo comparecer",
        "estarei",
        "ok",
        "okay",
        "fechado",
    )
    _NO_TOKENS = (
        "nao",
        "nao vou",
        "nao consigo",
        "nao poderei",
        "nao posso",
        "preciso remarcar",
        "quero remarcar",
        "remarcar",
        "reagendar",
        "trocar horario",
        "mudar horario",
        "outro horario",
        "outra data",
    )
    _CANCEL_TOKENS = (
        "cancelar",
        "desmarcar",
        "cancelar consulta",
        "cancelar minha consulta",
    )

    def __init__(self) -> None:
        self.calendar = CalendarService()
        self.whatsapp = WhatsAppService()
        self.config = ConfigService()

    @staticmethod
    def _normalize(text: str) -> str:
        normalized = unicodedata.normalize("NFKD", text or "")
        normalized = normalized.encode("ascii", "ignore").decode("ascii").lower()
        return re.sub(r"\s+", " ", normalized).strip()

    @staticmethod
    def _normalize_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=SAO_PAULO_TZ)
        return value.astimezone(SAO_PAULO_TZ)

    @staticmethod
    def build_conversation_phone(phone: str) -> str:
        """Padroniza o telefone no formato usado pelo webhook do WhatsApp."""
        digits = "".join(char for char in (phone or "") if char.isdigit())
        if digits and not digits.startswith("55"):
            digits = f"55{digits}"
        return digits

    @classmethod
    def scheduler_enabled(cls) -> bool:
        """Permite desligar a rotina por variavel de ambiente."""
        raw_value = os.getenv("ENABLE_APPOINTMENT_CONFIRMATION_SCHEDULER", "1")
        return raw_value.strip().lower() not in {"0", "false", "no", "off"}

    @classmethod
    def get_next_run_datetime(cls, reference_time: datetime | None = None) -> datetime:
        """Calcula o proximo disparo diario das 20:00 em Sao Paulo."""
        now = cls._normalize_datetime(reference_time or datetime.now(SAO_PAULO_TZ))
        next_run = now.replace(hour=cls.REMINDER_HOUR, minute=0, second=0, microsecond=0)
        if now >= next_run:
            next_run += timedelta(days=1)
        return next_run

    @classmethod
    def wants_cancellation(cls, patient_message: str) -> bool:
        """Identifica quando o paciente quer cancelar em vez de remarcar."""
        normalized = cls._normalize(patient_message)
        return any(token in normalized for token in cls._CANCEL_TOKENS)

    @classmethod
    def is_affirmative_response(cls, patient_message: str) -> bool:
        """Indica se o paciente confirmou que vai comparecer."""
        normalized = cls._normalize(patient_message)
        if not normalized:
            return False
        if cls.wants_cancellation(normalized) or any(token in normalized for token in cls._NO_TOKENS):
            return False
        return any(token in normalized for token in cls._YES_TOKENS)

    @classmethod
    def needs_reschedule_response(cls, patient_message: str) -> bool:
        """Identifica respostas negativas ou pedidos diretos de remarcacao."""
        normalized = cls._normalize(patient_message)
        if not normalized or cls.wants_cancellation(normalized):
            return False
        return any(token in normalized for token in cls._NO_TOKENS)

    @classmethod
    def clear_confirmation_metadata(cls, state: ConversationState) -> None:
        """Remove metadados temporarios da confirmacao proativa."""
        for key in (
            cls.METADATA_TYPE_KEY,
            cls.METADATA_EVENT_ID_KEY,
            cls.METADATA_START_KEY,
        ):
            state.metadata.pop(key, None)

    @classmethod
    def build_event_label(cls, start_time: datetime) -> str:
        """Formata o label padrao de data/hora da consulta."""
        start_sp = cls._normalize_datetime(start_time)
        return f"{start_sp.strftime('%d/%m/%Y')} as {start_sp.strftime('%H:%M')}"

    @classmethod
    def serialize_appointment_start(cls, start_time: datetime | str) -> str:
        """Serializa a data/hora da consulta no formato persistido."""
        if isinstance(start_time, str):
            return start_time.strip()
        return cls._normalize_datetime(start_time).replace(microsecond=0).isoformat()

    @classmethod
    def _try_claim_reminder_send(
        cls,
        *,
        event_id: str,
        phone: str,
        patient_name: str,
        appointment_start: datetime,
        reminder_type: str = REMINDER_TYPE_DAY_BEFORE,
    ) -> bool:
        db = get_db()
        serialized_start = cls.serialize_appointment_start(appointment_start)
        cursor = db.execute(
            "INSERT OR IGNORE INTO appointment_confirmations "
            "(event_id, phone, patient_name, reminder_type, appointment_start, status, sent_at) "
            "VALUES (?, ?, ?, ?, ?, 'processing', CURRENT_TIMESTAMP)",
            (event_id, phone, patient_name, reminder_type, serialized_start),
        )
        if cursor.rowcount == 1:
            db.commit()
            return True

        row = db.execute(
            "SELECT status FROM appointment_confirmations "
            "WHERE event_id = ? AND reminder_type = ? AND appointment_start = ?",
            (event_id, reminder_type, serialized_start),
        ).fetchone()
        status = str(row["status"] or "").lower() if row else ""
        if status == "failed":
            cursor = db.execute(
                "UPDATE appointment_confirmations "
                "SET phone = ?, patient_name = ?, status = 'processing', response_text = NULL, "
                "responded_at = NULL, sent_at = CURRENT_TIMESTAMP "
                "WHERE event_id = ? AND reminder_type = ? AND appointment_start = ?",
                (phone, patient_name, event_id, reminder_type, serialized_start),
            )
            db.commit()
            return cursor.rowcount == 1

        return False

    @classmethod
    def _mark_reminder_sent(
        cls,
        *,
        event_id: str,
        phone: str,
        patient_name: str,
        appointment_start: datetime,
        reminder_type: str = REMINDER_TYPE_DAY_BEFORE,
    ) -> None:
        db = get_db()
        db.execute(
            "UPDATE appointment_confirmations "
            "SET phone = ?, patient_name = ?, status = 'sent', sent_at = CURRENT_TIMESTAMP "
            "WHERE event_id = ? AND reminder_type = ? AND appointment_start = ?",
            (
                phone,
                patient_name,
                event_id,
                reminder_type,
                cls.serialize_appointment_start(appointment_start),
            ),
        )
        db.commit()

    @classmethod
    def _mark_reminder_failed(
        cls,
        *,
        event_id: str,
        appointment_start: datetime,
        reminder_type: str = REMINDER_TYPE_DAY_BEFORE,
    ) -> None:
        db = get_db()
        db.execute(
            "UPDATE appointment_confirmations "
            "SET status = 'failed', sent_at = CURRENT_TIMESTAMP "
            "WHERE event_id = ? AND reminder_type = ? AND appointment_start = ?",
            (event_id, reminder_type, cls.serialize_appointment_start(appointment_start)),
        )
        db.commit()

    @classmethod
    def mark_patient_response(
        cls,
        *,
        event_id: str,
        appointment_start: datetime | str,
        status: str,
        response_text: str,
        reminder_type: str = REMINDER_TYPE_DAY_BEFORE,
    ) -> None:
        """Atualiza o status da confirmacao apos resposta do paciente."""
        if not event_id:
            return

        serialized_start = cls.serialize_appointment_start(appointment_start)
        if not serialized_start:
            return

        db = get_db()
        db.execute(
            "UPDATE appointment_confirmations "
            "SET status = ?, response_text = ?, responded_at = CURRENT_TIMESTAMP "
            "WHERE event_id = ? AND reminder_type = ? AND appointment_start = ?",
            (status, (response_text or "").strip()[:500], event_id, reminder_type, serialized_start),
        )
        db.commit()

    def _build_day_before_message(self, patient_name: str, start_time: datetime) -> str:
        first_name = (patient_name or "").strip().split()[0] if patient_name else ""
        start_sp = self._normalize_datetime(start_time)
        message = self.config.get_message(
            "appointment_confirmation.day_before",
            patient_name=first_name or "voce",
            date=start_sp.strftime("%d/%m/%Y"),
            time=start_sp.strftime("%H:%M"),
            doctor_name=self.config.get_doctor_name(),
        ).strip()
        if message:
            return message
        prefix = f"{first_name}, " if first_name else ""
        return (
            f"{prefix}passando para confirmar sua consulta de amanha.\n\n"
            f"Data: {start_sp.strftime('%d/%m/%Y')}\n"
            f"Horario: {start_sp.strftime('%H:%M')}\n\n"
            "Voce consegue comparecer? Se precisar remarcar, me avise por aqui."
        )

    def _select_unique_appointments(self, appointments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unique_by_phone: dict[str, dict[str, Any]] = {}
        for appointment in appointments:
            phone = str(appointment.get("patient_phone", "")).strip()
            if not phone:
                continue
            current = unique_by_phone.get(phone)
            if current is None or appointment["start_time"] < current["start_time"]:
                unique_by_phone[phone] = appointment
        return sorted(unique_by_phone.values(), key=lambda item: item["start_time"])

    def _build_confirmation_state(
        self,
        *,
        patient_name: str,
        plan_name: str,
        event_id: str,
        start_time: datetime,
    ) -> ConversationState:
        label = self.build_event_label(start_time)
        return ConversationState(
            stage=self.CONFIRMATION_STAGE,
            patient_name=patient_name,
            plan_name=plan_name,
            pending_event_id=event_id,
            pending_event_label=label,
            reschedule_event_id=event_id,
            reschedule_event_label=label,
            metadata={
                self.METADATA_TYPE_KEY: self.REMINDER_TYPE_DAY_BEFORE,
                self.METADATA_EVENT_ID_KEY: event_id,
                self.METADATA_START_KEY: self.serialize_appointment_start(start_time),
            },
        )

    async def send_next_day_confirmations(
        self,
        reference_time: datetime | None = None,
    ) -> dict[str, int]:
        """Envia confirmacoes para pacientes com consulta no dia seguinte."""
        now = self._normalize_datetime(reference_time or datetime.now(SAO_PAULO_TZ))
        target_date = (now + timedelta(days=1)).date()
        appointments = self.calendar.find_patient_appointments_for_date(
            datetime.combine(target_date, time(0, 0), tzinfo=SAO_PAULO_TZ)
        )

        stats = {
            "candidates": 0,
            "sent": 0,
            "skipped_duplicates": 0,
            "skipped_busy": 0,
            "failed": 0,
        }

        for appointment in self._select_unique_appointments(appointments):
            stats["candidates"] += 1
            phone = self.build_conversation_phone(str(appointment.get("patient_phone", "")).strip())
            event_id = str(appointment.get("event_id", "")).strip()
            start_time = appointment.get("start_time")
            if not phone or not event_id or not isinstance(start_time, datetime):
                continue

            current_state = ConversationStateService.get(phone)
            if current_state.stage != "idle":
                stats["skipped_busy"] += 1
                continue

            patient = PatientService.find_by_phone(phone) or {}
            patient_name = str(
                patient.get("name")
                or appointment.get("patient_name")
                or phone
            ).strip()
            plan_name = str(patient.get("plan") or "").strip()

            claimed = self._try_claim_reminder_send(
                event_id=event_id,
                phone=phone,
                patient_name=patient_name,
                appointment_start=start_time,
            )
            if not claimed:
                stats["skipped_duplicates"] += 1
                continue

            message = self._build_day_before_message(patient_name, start_time)
            delivered = await self.whatsapp.send_message(phone, message)
            if not delivered:
                self._mark_reminder_failed(
                    event_id=event_id,
                    appointment_start=start_time,
                )
                stats["failed"] += 1
                continue

            ConversationService.add_message(phone, "assistant", message)
            ConversationStateService.save(
                phone,
                self._build_confirmation_state(
                    patient_name=patient_name,
                    plan_name=plan_name,
                    event_id=event_id,
                    start_time=start_time,
                ),
            )
            self._mark_reminder_sent(
                event_id=event_id,
                phone=phone,
                patient_name=patient_name,
                appointment_start=start_time,
            )
            stats["sent"] += 1

        return stats
