"""Testes da normalizacao interna de telefone."""

import os
from datetime import datetime
from pathlib import Path

from src.infrastructure.persistence.connection import close_db, get_db, init_db
from src.infrastructure.integrations.calendar_service import CalendarService
from src.interfaces.tools.patient_tool import SavePatientTool


class _FakeCalendarInsertApi:
    def __init__(self, captured: dict):
        self.captured = captured

    def insert(self, calendarId, body):
        self.captured["calendar_id"] = calendarId
        self.captured["body"] = body
        return self

    def execute(self):
        return {"id": "evt-1"}


class _FakeCalendarListApi:
    def __init__(self, items: list[dict], captured: dict):
        self.items = items
        self.captured = captured

    def list(self, **kwargs):
        self.captured["query"] = kwargs
        return self

    def execute(self):
        return {"items": self.items}


class _FakeCalendarServiceApi:
    def __init__(self, events_api):
        self._events_api = events_api

    def events(self):
        return self._events_api


class TestPhoneNormalization:
    """Garante que a aplicacao usa DDD + numero internamente."""

    def setup_method(self):
        self.db_path = Path("./data/test_phone_normalization.db")
        os.environ["DATABASE_PATH"] = str(self.db_path)
        close_db()
        init_db()

    def teardown_method(self):
        close_db()
        self.db_path.unlink(missing_ok=True)
        self.db_path.with_suffix(".db-wal").unlink(missing_ok=True)
        self.db_path.with_suffix(".db-shm").unlink(missing_ok=True)

    def test_save_patient_tool_stores_phone_without_country_code(self):
        tool = SavePatientTool()

        message = tool._run("5513991743380", "Cristian")

        row = get_db().execute("SELECT phone FROM patients WHERE name = ?", ("Cristian",)).fetchone()

        assert "cadastrado com sucesso" in message
        assert row is not None
        assert row["phone"] == "13991743380"

    def test_init_db_normalizes_legacy_patient_phone(self):
        db = get_db()
        db.execute(
            "INSERT INTO patients (phone, name, plan) VALUES (?, ?, ?)",
            ("5513991743380", "Cristian", None),
        )
        db.commit()

        close_db()
        init_db()

        row = get_db().execute("SELECT phone FROM patients WHERE name = ?", ("Cristian",)).fetchone()

        assert row is not None
        assert row["phone"] == "13991743380"

    def test_create_appointment_stores_internal_phone_in_calendar(self, monkeypatch):
        service = CalendarService()
        captured: dict = {}
        fake_service = _FakeCalendarServiceApi(_FakeCalendarInsertApi(captured))

        monkeypatch.setattr(service, "_get_service", lambda: fake_service)

        service.create_appointment("Cristian", "5513991743380", datetime(2026, 4, 7, 8, 0))

        assert captured["body"]["summary"] == "Cristian - 13991743380"
        assert "Telefone: 13991743380" in captured["body"]["description"]

    def test_find_appointments_by_phone_matches_legacy_and_new_events(self, monkeypatch):
        service = CalendarService()
        captured: dict = {}
        fake_items = [
            {"id": "evt-old", "summary": "Cristian - 5513991743380"},
            {"id": "evt-new", "summary": "Cristian - 13991743380"},
            {"id": "evt-other", "summary": "Outra pessoa - 11988887777"},
        ]
        fake_service = _FakeCalendarServiceApi(_FakeCalendarListApi(fake_items, captured))

        monkeypatch.setattr(service, "_get_service", lambda: fake_service)

        events = service.find_appointments_by_phone("5513991743380")

        assert captured["query"]["q"] == "13991743380"
        assert [event["id"] for event in events] == ["evt-old", "evt-new"]
