"""Testes para _reset_to_idle (CO-08) e HandoffService.activate() (HO-01)."""

import os
from pathlib import Path

import pytest


class _DBMixin:
    def setup_method(self):
        self.db_path = Path("./data/test_reset_handoff.db")
        os.environ["DATABASE_PATH"] = str(self.db_path)
        os.environ["OPENAI_API_KEY"] = "test-key"
        from src.infrastructure.persistence.connection import close_db, init_db
        close_db()
        init_db()

    def teardown_method(self):
        from src.infrastructure.persistence.connection import close_db
        close_db()
        self.db_path.unlink(missing_ok=True)


PHONE = "5511999990002"


class TestResetToIdle:
    """CO-08: _reset_to_idle limpa todos os campos do estado."""

    def test_co08_reset_zera_offered_date_e_times(self):
        from src.application.services.conversation_state_service import ConversationState
        from src.interfaces.http.app import _reset_to_idle

        state = ConversationState(
            stage="awaiting_plan_for_slot_confirmation",
            offered_date="17/06/2026",
            offered_times=["09:00", "10:00"],
            patient_name="Maria",
            plan_name="Particular",
            pending_slot_date="17/06/2026",
            pending_slot_time="09:00",
        )
        result = _reset_to_idle(state)
        assert result.stage == "idle"
        assert result.offered_date == ""
        assert result.offered_times == []
        assert result.patient_name == ""
        assert result.plan_name == ""
        assert result.pending_slot_date == ""
        assert result.pending_slot_time == ""

    def test_co08_reset_retorna_mesmo_objeto(self):
        from src.application.services.conversation_state_service import ConversationState
        from src.interfaces.http.app import _reset_to_idle

        state = ConversationState(stage="custom")
        result = _reset_to_idle(state)
        assert result is state  # modifica in-place e retorna

    def test_co08_reset_zera_rejected_slots(self):
        from src.application.services.conversation_state_service import ConversationState
        from src.interfaces.http.app import _reset_to_idle

        state = ConversationState(
            stage="idle",
            rejected_slots=["08:00", "09:00"],
            excluded_dates=["18/06/2026"],
        )
        _reset_to_idle(state)
        assert state.rejected_slots == []
        assert state.excluded_dates == []


class TestHandoffPreservesContext(_DBMixin):
    """HO-01: HandoffService.activate() preserva contexto de agenda."""

    def test_ho01_handoff_preserva_offered_date(self):
        from src.application.services.conversation_state_service import (
            ConversationState,
            ConversationStateService,
        )
        from src.application.services.handoff_service import HandoffService

        state = ConversationState(
            stage="idle",
            offered_date="18/06/2026",
            offered_times=["10:00", "11:00"],
            patient_name="Maria",
        )
        ConversationStateService.save(PHONE, state)

        HandoffService.activate(PHONE)

        loaded = ConversationStateService.get(PHONE)
        assert loaded.stage == HandoffService.STAGE
        assert loaded.offered_date == "18/06/2026"
        assert loaded.offered_times == ["10:00", "11:00"]

    def test_ho01_handoff_preserva_pending_slot(self):
        from src.application.services.conversation_state_service import (
            ConversationState,
            ConversationStateService,
        )
        from src.application.services.handoff_service import HandoffService

        state = ConversationState(
            stage="idle",
            pending_slot_date="19/06/2026",
            pending_slot_time="14:00",
            plan_name="Unimed",
        )
        ConversationStateService.save(PHONE, state)

        HandoffService.activate(PHONE)

        loaded = ConversationStateService.get(PHONE)
        assert loaded.stage == HandoffService.STAGE
        assert loaded.pending_slot_date == "19/06/2026"
        assert loaded.pending_slot_time == "14:00"
        assert loaded.plan_name == "Unimed"

    def test_ho01_handoff_seta_metadata_correto(self):
        from src.application.services.conversation_state_service import (
            ConversationState,
            ConversationStateService,
        )
        from src.application.services.handoff_service import HandoffService

        ConversationStateService.save(PHONE, ConversationState())
        HandoffService.activate(PHONE)

        loaded = ConversationStateService.get(PHONE)
        assert HandoffService.METADATA_UNTIL_KEY in loaded.metadata
        assert loaded.metadata[HandoffService.METADATA_UNTIL_KEY]  # não vazio
