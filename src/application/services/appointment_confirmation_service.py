"""Rotina de confirmacao proativa de consultas do dia seguinte."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import unicodedata
from datetime import datetime, time, timedelta, timezone
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

    def __init__(self) -> None:
        self.calendar = CalendarService()
        self.whatsapp = WhatsAppService()
        self.config = ConfigService()

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
        if status in ("failed", "processing"):
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
        date_str = start_sp.strftime("%d/%m/%Y")
        time_str = start_sp.strftime("%H:%M")
        message = self.config.get_message(
            "appointment_confirmation.day_before",
            patient_name=first_name or "voce",
            date=date_str,
            time=time_str,
            doctor_name=self.config.get_doctor_name(),
        ).strip()
        # Só usa mensagem do config se contiver a data — descarta fallback genérico de erro
        if message and date_str in message:
            return message
        prefix = f"{first_name}, " if first_name else ""
        return (
            f"{prefix}passando para confirmar sua consulta de amanha.\n\n"
            f"Data: {date_str}\n"
            f"Horario: {time_str}\n\n"
            "Voce consegue comparecer? Se precisar remarcar, me avise por aqui."
        )

    def _select_unique_appointments(self, appointments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # CO-06: deduplicar por (phone, event_id) — mesmo paciente pode ter N consultas
        unique_by_phone_event: dict[tuple, dict[str, Any]] = {}
        for appointment in appointments:
            phone = str(appointment.get("patient_phone", "")).strip()
            event_id = str(appointment.get("event_id", "")).strip()
            if not phone or not event_id:
                continue
            key = (phone, event_id)
            if key not in unique_by_phone_event:
                unique_by_phone_event[key] = appointment
        return sorted(unique_by_phone_event.values(), key=lambda item: item["start_time"])

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

            try:
                current_state = ConversationStateService.get(phone)
                if current_state.stage != "idle":
                    # Só pula se o estado for recente (< 2h) — estado antigo é considerado expirado
                    updated_at = ConversationStateService.get_updated_at(phone)
                    state_is_recent = (
                        updated_at is not None
                        and (datetime.utcnow() - updated_at).total_seconds() < 7200
                    )
                    if state_is_recent:
                        logger.info(
                            "[confirmacao] %s | pulado (stage=%s, atualizado há %.0f min)",
                            phone,
                            current_state.stage,
                            (datetime.utcnow() - updated_at).total_seconds() / 60,
                        )
                        stats["skipped_busy"] += 1
                        continue
                    # CO-07: nao apagar conversa em andamento; apenas pular
                    logger.info(
                        "[confirmacao] %s | estado expirado (stage=%s) — pulando para nao interromper conversa",
                        phone, current_state.stage,
                    )
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
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "[confirmacao] excecao ao processar %s (event=%s): %s",
                    phone, event_id, exc, exc_info=True,
                )
                try:
                    self._mark_reminder_failed(event_id=event_id, appointment_start=start_time)
                except Exception:
                    pass
                stats["failed"] += 1

        return stats

    async def run_catchup_if_missed(
        self,
        now: datetime | None = None,
    ) -> dict[str, int] | None:
        """CO-04: executa confirmacoes se o disparo das 20h foi perdido por restart/queda.

        Retorna stats se executou, None se nao foi necessario.
        """
        now_tz = self._normalize_datetime(now or datetime.now(SAO_PAULO_TZ))
        if now_tz.hour < self.REMINDER_HOUR:
            return None
        target_date = (now_tz + timedelta(days=1)).date()
        db = get_db()
        row = db.execute(
            "SELECT COUNT(*) AS cnt FROM appointment_confirmations "
            "WHERE appointment_start LIKE ? AND status IN ('sent', 'processing')",
            (f"{target_date.isoformat()}%",),
        ).fetchone()
        sent_count = int(row["cnt"]) if row else 0
        if sent_count > 0:
            logger.info(
                "[confirmacao] catch-up: %d lembrete(s) ja enviado(s)/em andamento para %s — nenhuma acao",
                sent_count,
                target_date,
            )
            return None
        logger.info(
            "[confirmacao] catch-up: nenhum lembrete para %s apos as %02dh — enviando agora",
            target_date,
            self.REMINDER_HOUR,
        )
        return await self.send_next_day_confirmations(reference_time=now_tz)
