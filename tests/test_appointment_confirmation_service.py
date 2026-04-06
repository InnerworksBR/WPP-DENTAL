"""Testes da rotina automatica de confirmacao de consultas."""

import asyncio
import os
from datetime import datetime
from pathlib import Path

from src.infrastructure.integrations.calendar_service import SAO_PAULO_TZ


class TestAppointmentConfirmationService:
    """Valida o envio automatico e o rastreio das confirmacoes."""

    def setup_method(self):
        self.db_path = Path("./data/test_confirmation_job.db")
        os.environ["DATABASE_PATH"] = str(self.db_path)

        from src.infrastructure.persistence.connection import close_db

        close_db()

    def teardown_method(self):
        from src.infrastructure.persistence.connection import close_db

        close_db()
        self.db_path.unlink(missing_ok=True)

    def test_send_next_day_confirmations_persists_state_and_avoids_duplicates(self, monkeypatch):
        from src.application.services.appointment_confirmation_service import AppointmentConfirmationService
        from src.application.services.conversation_service import ConversationService
        from src.application.services.conversation_state_service import ConversationStateService
        from src.application.services.patient_service import PatientService
        from src.infrastructure.persistence.connection import get_db, init_db

        init_db()
        PatientService.upsert("5511999999999", "Maria Silva", "Amil Dental")

        tomorrow_appointment = {
            "event_id": "evt-1",
            "patient_name": "Maria Silva",
            "patient_phone": "11999999999",
            "start_time": datetime(2026, 4, 7, 8, 0, tzinfo=SAO_PAULO_TZ),
            "end_time": datetime(2026, 4, 7, 8, 15, tzinfo=SAO_PAULO_TZ),
        }
        sent_messages: list[tuple[str, str]] = []

        async def fake_send_message(self, phone, message):
            sent_messages.append((phone, message))
            return True

        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.find_patient_appointments_for_date",
            lambda self, date: [tomorrow_appointment],
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )

        service = AppointmentConfirmationService()
        first_run = asyncio.run(
            service.send_next_day_confirmations(
                reference_time=datetime(2026, 4, 6, 20, 0, tzinfo=SAO_PAULO_TZ)
            )
        )
        second_run = asyncio.run(
            service.send_next_day_confirmations(
                reference_time=datetime(2026, 4, 6, 20, 5, tzinfo=SAO_PAULO_TZ)
            )
        )

        state = ConversationStateService.get("5511999999999")
        history = ConversationService.get_history("5511999999999")
        confirmation = get_db().execute(
            "SELECT status, phone FROM appointment_confirmations WHERE event_id = ?",
            ("evt-1",),
        ).fetchone()

        assert first_run["sent"] == 1
        assert second_run["skipped_duplicates"] + second_run["skipped_busy"] == 1
        assert len(sent_messages) == 1
        assert "consulta de amanha" in sent_messages[0][1].lower()
        assert state.stage == AppointmentConfirmationService.CONFIRMATION_STAGE
        assert state.plan_name == "Amil Dental"
        assert state.pending_event_id == "evt-1"
        assert history[-1]["role"] == "assistant"
        assert "Voce consegue comparecer?" in history[-1]["content"]
        assert confirmation is not None
        assert confirmation["status"] == "sent"
        assert confirmation["phone"] == "5511999999999"

    def test_send_next_day_confirmations_skips_patient_with_active_flow(self, monkeypatch):
        from src.application.services.appointment_confirmation_service import AppointmentConfirmationService
        from src.application.services.conversation_state_service import ConversationState, ConversationStateService
        from src.infrastructure.persistence.connection import get_db, init_db

        init_db()
        ConversationStateService.save(
            "5511999999999",
            ConversationState(
                stage="awaiting_cancel_confirmation",
                patient_name="Maria Silva",
                pending_event_id="evt-old",
                pending_event_label="07/04/2026 as 08:00",
            ),
        )

        tomorrow_appointment = {
            "event_id": "evt-1",
            "patient_name": "Maria Silva",
            "patient_phone": "11999999999",
            "start_time": datetime(2026, 4, 7, 8, 0, tzinfo=SAO_PAULO_TZ),
            "end_time": datetime(2026, 4, 7, 8, 15, tzinfo=SAO_PAULO_TZ),
        }

        async def fake_send_message(self, phone, message):
            return True

        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.find_patient_appointments_for_date",
            lambda self, date: [tomorrow_appointment],
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )

        service = AppointmentConfirmationService()
        result = asyncio.run(
            service.send_next_day_confirmations(
                reference_time=datetime(2026, 4, 6, 20, 0, tzinfo=SAO_PAULO_TZ)
            )
        )

        row = get_db().execute(
            "SELECT COUNT(*) AS total FROM appointment_confirmations"
        ).fetchone()

        assert result["sent"] == 0
        assert result["skipped_busy"] == 1
        assert row["total"] == 0
