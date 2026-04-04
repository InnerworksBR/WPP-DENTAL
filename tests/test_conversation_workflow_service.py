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
