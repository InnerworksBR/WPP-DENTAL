"""Testes do novo motor deterministico de conversa."""

import os
from datetime import datetime
from pathlib import Path

from src.infrastructure.integrations.calendar_service import SAO_PAULO_TZ


class TestConversationWorkflowService:
    """Garante fluxos principais do atendimento sem depender de CrewAI."""

    def setup_method(self):
        self.db_path = Path("./data/test_workflow.db")
        os.environ["DATABASE_PATH"] = str(self.db_path)

        from src.infrastructure.persistence.connection import close_db

        close_db()

    def teardown_method(self):
        from src.infrastructure.persistence.connection import close_db

        close_db()
        self.db_path.unlink(missing_ok=True)

    def test_new_patient_first_message_asks_for_name(self):
        from src.infrastructure.persistence.connection import init_db
        from src.application.services.conversation_state_service import ConversationStateService
        from src.application.services.conversation_workflow_service import ConversationWorkflowService

        init_db()
        workflow = ConversationWorkflowService()

        response = workflow.process_message(
            patient_phone="5511999999999",
            patient_message="Oi",
            patient_name="",
            is_first_message=True,
        )

        state = ConversationStateService.get("5511999999999")

        assert "nome completo" in response.lower()
        assert state.stage == "awaiting_name"

    def test_returning_patient_prompt_mentions_consulta_not_agenda(self):
        from src.infrastructure.persistence.connection import init_db
        from src.application.services.conversation_workflow_service import ConversationWorkflowService
        from src.application.services.patient_service import PatientService

        init_db()
        PatientService.upsert("5511999999999", "Cristian Silva", "Amil Dental")
        workflow = ConversationWorkflowService()

        response = workflow.process_message(
            patient_phone="5511999999999",
            patient_message="Oi",
            patient_name="Cristian",
            is_first_message=True,
        )

        normalized = response.lower()
        assert "consulta" in normalized
        assert "agenda" not in normalized

    def test_returning_patient_with_saved_plan_goes_straight_to_period_and_offer(self, monkeypatch):
        from src.infrastructure.persistence.connection import init_db
        from src.application.services.conversation_workflow_service import ConversationWorkflowService
        from src.application.services.patient_service import PatientService

        init_db()
        PatientService.upsert("5511999999999", "Maria Silva", "Amil Dental")
        workflow = ConversationWorkflowService()

        monkeypatch.setattr(
            workflow,
            "_find_next_available_slots",
            lambda period: [
                {
                    "start": datetime(2026, 4, 8, 8, 0, tzinfo=SAO_PAULO_TZ),
                    "formatted": "08/04/2026 as 08:00",
                },
                {
                    "start": datetime(2026, 4, 8, 8, 15, tzinfo=SAO_PAULO_TZ),
                    "formatted": "08/04/2026 as 08:15",
                },
            ],
        )

        ask_period = workflow.process_message(
            patient_phone="5511999999999",
            patient_message="Quero agendar uma consulta",
            patient_name="Maria",
            is_first_message=False,
        )
        offer = workflow.process_message(
            patient_phone="5511999999999",
            patient_message="De manha",
            patient_name="Maria",
            is_first_message=False,
        )

        assert "qual periodo" in ask_period.lower()
        assert "08/04/2026" in offer
        assert "08:00" in offer
        assert "08:15" in offer

    def test_cancel_flow_requires_confirmation_and_cancels_event(self, monkeypatch):
        from src.infrastructure.persistence.connection import init_db
        from src.application.services.conversation_state_service import ConversationStateService
        from src.application.services.conversation_workflow_service import ConversationWorkflowService
        from src.application.services.patient_service import PatientService

        init_db()
        PatientService.upsert("5511999999999", "Maria Silva", "Amil Dental")
        workflow = ConversationWorkflowService()

        monkeypatch.setattr(
            workflow.calendar,
            "find_appointments_by_phone",
            lambda phone: [
                {
                    "id": "evt-1",
                    "start": {"dateTime": "2026-04-09T08:00:00-03:00"},
                    "summary": "Maria Silva - 11999999999",
                }
            ],
        )
        monkeypatch.setattr(workflow.calendar, "cancel_appointment", lambda event_id: True)

        first = workflow.process_message(
            patient_phone="5511999999999",
            patient_message="Quero cancelar minha consulta",
            patient_name="Maria",
            is_first_message=False,
        )
        second = workflow.process_message(
            patient_phone="5511999999999",
            patient_message="Sim",
            patient_name="Maria",
            is_first_message=False,
        )

        state = ConversationStateService.get("5511999999999")

        assert "deseja realmente cancelar" in first.lower()
        assert "cancelada com sucesso" in second.lower()
        assert state.stage == "idle"

    def test_day_before_confirmation_yes_keeps_appointment_confirmed(self):
        from src.infrastructure.persistence.connection import get_db, init_db
        from src.application.services.appointment_confirmation_service import AppointmentConfirmationService
        from src.application.services.conversation_state_service import ConversationState, ConversationStateService
        from src.application.services.conversation_workflow_service import ConversationWorkflowService
        from src.application.services.patient_service import PatientService

        init_db()
        PatientService.upsert("5511999999999", "Maria Silva", "Amil Dental")
        db = get_db()
        db.execute(
            "INSERT INTO appointment_confirmations "
            "(event_id, phone, patient_name, reminder_type, appointment_start, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "evt-1",
                "5511999999999",
                "Maria Silva",
                AppointmentConfirmationService.REMINDER_TYPE_DAY_BEFORE,
                "2026-04-07T08:00:00-03:00",
                "sent",
            ),
        )
        db.commit()
        ConversationStateService.save(
            "5511999999999",
            ConversationState(
                stage=AppointmentConfirmationService.CONFIRMATION_STAGE,
                patient_name="Maria Silva",
                plan_name="Amil Dental",
                pending_event_id="evt-1",
                pending_event_label="07/04/2026 as 08:00",
                reschedule_event_id="evt-1",
                reschedule_event_label="07/04/2026 as 08:00",
                metadata={
                    AppointmentConfirmationService.METADATA_EVENT_ID_KEY: "evt-1",
                    AppointmentConfirmationService.METADATA_START_KEY: "2026-04-07T08:00:00-03:00",
                },
            ),
        )

        workflow = ConversationWorkflowService()
        response = workflow.process_message(
            patient_phone="5511999999999",
            patient_message="Sim, vou sim",
            patient_name="Maria",
            is_first_message=False,
        )

        state = ConversationStateService.get("5511999999999")
        confirmation = get_db().execute(
            "SELECT status, response_text FROM appointment_confirmations WHERE event_id = ?",
            ("evt-1",),
        ).fetchone()

        assert "continua confirmada" in response.lower()
        assert state.stage == "idle"
        assert confirmation["status"] == "confirmed"
        assert "vou sim" in confirmation["response_text"].lower()

    def test_day_before_confirmation_negative_response_starts_reschedule_flow(self):
        from src.infrastructure.persistence.connection import get_db, init_db
        from src.application.services.appointment_confirmation_service import AppointmentConfirmationService
        from src.application.services.conversation_state_service import ConversationState, ConversationStateService
        from src.application.services.conversation_workflow_service import ConversationWorkflowService
        from src.application.services.patient_service import PatientService

        init_db()
        PatientService.upsert("5511999999999", "Maria Silva", "Amil Dental")
        db = get_db()
        db.execute(
            "INSERT INTO appointment_confirmations "
            "(event_id, phone, patient_name, reminder_type, appointment_start, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "evt-1",
                "5511999999999",
                "Maria Silva",
                AppointmentConfirmationService.REMINDER_TYPE_DAY_BEFORE,
                "2026-04-07T08:00:00-03:00",
                "sent",
            ),
        )
        db.commit()
        ConversationStateService.save(
            "5511999999999",
            ConversationState(
                stage=AppointmentConfirmationService.CONFIRMATION_STAGE,
                patient_name="Maria Silva",
                plan_name="Amil Dental",
                pending_event_id="evt-1",
                pending_event_label="07/04/2026 as 08:00",
                reschedule_event_id="evt-1",
                reschedule_event_label="07/04/2026 as 08:00",
                metadata={
                    AppointmentConfirmationService.METADATA_EVENT_ID_KEY: "evt-1",
                    AppointmentConfirmationService.METADATA_START_KEY: "2026-04-07T08:00:00-03:00",
                },
            ),
        )

        workflow = ConversationWorkflowService()
        response = workflow.process_message(
            patient_phone="5511999999999",
            patient_message="Nao vou conseguir, preciso remarcar",
            patient_name="Maria",
            is_first_message=False,
        )

        state = ConversationStateService.get("5511999999999")
        confirmation = get_db().execute(
            "SELECT status FROM appointment_confirmations WHERE event_id = ?",
            ("evt-1",),
        ).fetchone()

        assert "vamos remarcar" in response.lower()
        assert "qual periodo" in response.lower()
        assert state.intent == "reschedule"
        assert state.reschedule_event_id == "evt-1"
        assert state.stage == "awaiting_period"
        assert confirmation["status"] == "reschedule_requested"

    def test_address_question_returns_clinic_address_without_entering_schedule_loop(self):
        from src.infrastructure.persistence.connection import init_db
        from src.application.services.conversation_workflow_service import ConversationWorkflowService
        from src.application.services.patient_service import PatientService

        init_db()
        PatientService.upsert("5511999999999", "Cristian", "Amil Dental")
        workflow = ConversationWorkflowService()

        response = workflow.process_message(
            patient_phone="5511999999999",
            patient_message="Tenho consulta agendada mas nao lembro o endereco",
            patient_name="Cristian",
            is_first_message=False,
        )

        normalized = response.lower()
        assert "benjamin constant" in normalized
        assert "qual periodo" not in normalized
        assert "sala 1114" in normalized

    def test_address_question_clears_stale_schedule_state(self):
        from src.infrastructure.persistence.connection import init_db
        from src.application.services.conversation_state_service import ConversationState, ConversationStateService
        from src.application.services.conversation_workflow_service import ConversationWorkflowService
        from src.application.services.patient_service import PatientService

        init_db()
        PatientService.upsert("5511999999999", "Cristian", "Amil Dental")
        ConversationStateService.save(
            "5511999999999",
            ConversationState(
                stage="awaiting_period",
                intent="schedule",
                patient_name="Cristian",
                plan_name="Amil Dental",
            ),
        )

        workflow = ConversationWorkflowService()
        response = workflow.process_message(
            patient_phone="5511999999999",
            patient_message="Ja marquei, quero so saber o endereco",
            patient_name="Cristian",
            is_first_message=False,
        )
        state = ConversationStateService.get("5511999999999")

        assert "benjamin constant" in response.lower()
        assert state.stage == "idle"
        assert state.intent == ""
