"""Testes para ConversationStateService: CO-01 (schema drift) e CO-02 (sanitizacao)."""

import json
import os
from pathlib import Path

import pytest

from src.application.services.conversation_state_service import (
    ConversationState,
    ConversationStateService,
)


class _DBMixin:
    def setup_method(self):
        self.db_path = Path("./data/test_conv_state_svc.db")
        os.environ["DATABASE_PATH"] = str(self.db_path)
        os.environ["OPENAI_API_KEY"] = "test-key"
        from src.infrastructure.persistence.connection import close_db, init_db
        close_db()
        init_db()

    def teardown_method(self):
        from src.infrastructure.persistence.connection import close_db
        close_db()
        self.db_path.unlink(missing_ok=True)


PHONE = "5511999990001"


class TestSchemaRobustness(_DBMixin):
    """CO-01: get() sobrevive a schema drift."""

    def _insert_raw_json(self, phone: str, payload: dict) -> None:
        from src.infrastructure.persistence.connection import get_db
        db = get_db()
        db.execute(
            "INSERT INTO conversation_state (phone, state_json) VALUES (?, ?)"
            " ON CONFLICT(phone) DO UPDATE SET state_json = excluded.state_json",
            (phone, json.dumps(payload)),
        )
        db.commit()

    def test_co01_campo_desconhecido_nao_quebra(self):
        """Campo extra no JSON não causa TypeError no ConversationState(**payload)."""
        payload = {"stage": "idle", "campo_novo_futuro": "valor", "outro_campo": 42}
        self._insert_raw_json(PHONE, payload)
        state = ConversationStateService.get(PHONE)
        assert isinstance(state, ConversationState)
        assert state.stage == "idle"

    def test_co01_campo_ausente_usa_default(self):
        """Campo ausente no JSON usa o valor default do dataclass."""
        payload = {"stage": "custom_stage"}  # sem offered_date
        self._insert_raw_json(PHONE, payload)
        state = ConversationStateService.get(PHONE)
        assert state.stage == "custom_stage"
        assert state.offered_date == ""

    def test_co01_payload_vazio_retorna_defaults(self):
        """JSON vazio retorna ConversationState com todos os defaults."""
        self._insert_raw_json(PHONE, {})
        state = ConversationStateService.get(PHONE)
        assert state.stage == "idle"
        assert state.offered_times == []


class TestFieldSanitization(_DBMixin):
    """CO-02: campos list/str com None são sanitizados após get()."""

    def _insert_raw_json(self, phone: str, payload: dict) -> None:
        from src.infrastructure.persistence.connection import get_db
        db = get_db()
        db.execute(
            "INSERT INTO conversation_state (phone, state_json) VALUES (?, ?)"
            " ON CONFLICT(phone) DO UPDATE SET state_json = excluded.state_json",
            (phone, json.dumps(payload)),
        )
        db.commit()

    def test_co02_offered_times_null_vira_lista_vazia(self):
        payload = {"stage": "idle", "offered_times": None}
        self._insert_raw_json(PHONE, payload)
        state = ConversationStateService.get(PHONE)
        assert state.offered_times == []

    def test_co02_rejected_slots_null_vira_lista_vazia(self):
        payload = {"stage": "idle", "rejected_slots": None}
        self._insert_raw_json(PHONE, payload)
        state = ConversationStateService.get(PHONE)
        assert state.rejected_slots == []

    def test_co02_excluded_dates_null_vira_lista_vazia(self):
        payload = {"stage": "idle", "excluded_dates": None}
        self._insert_raw_json(PHONE, payload)
        state = ConversationStateService.get(PHONE)
        assert state.excluded_dates == []

    def test_co02_str_fields_null_viram_string_vazia(self):
        payload = {"stage": "idle", "offered_date": None, "patient_name": None, "plan_name": None}
        self._insert_raw_json(PHONE, payload)
        state = ConversationStateService.get(PHONE)
        assert state.offered_date == ""
        assert state.patient_name == ""
        assert state.plan_name == ""


class TestRoundtrip(_DBMixin):
    """Roundtrip básico save → get."""

    def test_roundtrip_save_get_stage(self):
        ConversationStateService.save(PHONE, ConversationState(stage="custom_stage"))
        state = ConversationStateService.get(PHONE)
        assert state.stage == "custom_stage"

    def test_roundtrip_com_listas(self):
        s = ConversationState(
            stage="idle",
            offered_date="17/06/2026",
            offered_times=["09:00", "10:00"],
            rejected_slots=["08:00"],
        )
        ConversationStateService.save(PHONE, s)
        loaded = ConversationStateService.get(PHONE)
        assert loaded.offered_date == "17/06/2026"
        assert loaded.offered_times == ["09:00", "10:00"]
        assert loaded.rejected_slots == ["08:00"]
