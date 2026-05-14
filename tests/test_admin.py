"""Testes do painel administrativo."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.infrastructure.integrations.calendar_service import CalendarService, SAO_PAULO_TZ
from src.infrastructure.persistence.connection import close_db, get_db, init_db
from src.interfaces.http import admin


@pytest.fixture()
def admin_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = tmp_path / "admin.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    monkeypatch.delenv("WEBHOOK_API_KEY", raising=False)
    monkeypatch.delenv("EVOLUTION_WEBHOOK_API_KEY", raising=False)
    close_db()
    init_db()

    app = FastAPI()
    app.include_router(admin.router)
    with TestClient(app) as client:
        yield client

    close_db()


def _seed_admin_data() -> None:
    db = get_db()
    db.execute(
        "INSERT INTO patients (phone, name, plan) VALUES (?, ?, ?)",
        ("11999999999", "Maria Silva", "Amil Dental"),
    )
    patient_id = db.execute(
        "SELECT id FROM patients WHERE phone = ?",
        ("11999999999",),
    ).fetchone()["id"]
    db.execute(
        "INSERT INTO interactions (patient_id, type, summary) VALUES (?, ?, ?)",
        (patient_id, "schedule", "Agendamento confirmado"),
    )
    db.execute(
        "INSERT INTO conversation_history (phone, role, content) VALUES (?, ?, ?)",
        ("11999999999", "patient", "Oi"),
    )
    db.execute(
        "INSERT INTO conversation_history (phone, role, content) VALUES (?, ?, ?)",
        ("11999999999", "assistant", "Como posso ajudar?"),
    )
    db.execute(
        "INSERT INTO conversation_state (phone, state_json) VALUES (?, ?)",
        ("11999999999", '{"stage": "idle"}'),
    )
    db.execute(
        "INSERT INTO processed_messages (message_id, phone, status, last_error) "
        "VALUES (?, ?, ?, ?)",
        ("msg-fail", "11999999999", "failed", "timeout"),
    )
    db.execute(
        "INSERT INTO appointment_confirmations "
        "(event_id, phone, patient_name, appointment_start, status, response_text) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("evt-1", "11999999999", "Maria Silva", "2026-05-20T09:00:00-03:00", "sent", ""),
    )
    db.commit()


def test_admin_page_is_served(admin_client: TestClient):
    response = admin_client.get("/admin")

    assert response.status_code == 200
    assert "WPP-DENTAL Admin" in response.text


def test_admin_api_is_open_when_no_key_is_configured(admin_client: TestClient):
    response = admin_client.get("/admin/api/summary")

    assert response.status_code == 200
    assert response.json()["service"] == "wpp-dental"


def test_admin_api_rejects_invalid_key(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ADMIN_API_KEY", "admin-secret")

    response = admin_client.get("/admin/api/summary", headers={"x-admin-key": "wrong"})

    assert response.status_code == 401


def test_admin_api_accepts_admin_key(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ADMIN_API_KEY", "admin-secret")

    response = admin_client.get("/admin/api/summary", headers={"x-admin-key": "admin-secret"})

    assert response.status_code == 200


def test_admin_api_accepts_webhook_key_fallback(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    monkeypatch.setenv("WEBHOOK_API_KEY", "webhook-secret")

    response = admin_client.get("/admin/api/summary", headers={"x-api-key": "webhook-secret"})

    assert response.status_code == 200


def test_summary_reads_sqlite_metrics(admin_client: TestClient):
    _seed_admin_data()

    response = admin_client.get("/admin/api/summary")

    assert response.status_code == 200
    metrics = response.json()["metrics"]
    assert metrics["patients"] == 1
    assert metrics["conversations"] == 1
    assert metrics["messages_24h"] == 2
    assert metrics["active_states"] == 1
    assert metrics["failed_messages_7d"] == 1
    assert metrics["pending_confirmations"] == 1


def test_conversations_and_detail_return_messages_and_interactions(admin_client: TestClient):
    _seed_admin_data()

    list_response = admin_client.get("/admin/api/conversations?limit=500")
    detail_response = admin_client.get("/admin/api/conversations/11999999999?limit=500")

    assert list_response.status_code == 200
    assert len(list_response.json()["items"]) == 1
    item = list_response.json()["items"][0]
    assert item["phone"] == "11999999999"
    assert item["patient_name"] == "Maria Silva"
    assert item["last_role"] == "assistant"
    assert item["last_content"] == "Como posso ajudar?"

    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["patient"]["name"] == "Maria Silva"
    assert [message["role"] for message in detail["messages"]] == ["patient", "assistant"]
    assert detail["interactions"][0]["summary"] == "Agendamento confirmado"


def test_errors_return_failed_messages_and_confirmations(admin_client: TestClient):
    _seed_admin_data()

    response = admin_client.get("/admin/api/errors?limit=500")

    assert response.status_code == 200
    data = response.json()
    assert data["processed_messages"][0]["message_id"] == "msg-fail"
    assert data["processed_messages"][0]["last_error"] == "timeout"
    assert data["appointment_confirmations"][0]["event_id"] == "evt-1"


def test_appointments_ignore_day_blocks(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    class FakeCalendarService(CalendarService):
        def __init__(self) -> None:
            pass

        def list_events_between(self, start_date: datetime, end_date: datetime):
            return [
                {
                    "id": "block-1",
                    "summary": "[WPP-DENTAL] Bloqueio de agenda",
                    "start": {"date": "2026-05-20"},
                    "end": {"date": "2026-05-21"},
                    "extendedProperties": {
                        "private": {"wpp_dental_type": CalendarService.DAY_BLOCK_MARKER}
                    },
                },
                {
                    "id": "appt-1",
                    "summary": "Maria Silva - 11999999999",
                    "description": "Paciente: Maria Silva\nTelefone: 11999999999",
                    "start": {"dateTime": "2026-05-20T09:00:00-03:00"},
                    "end": {"dateTime": "2026-05-20T09:15:00-03:00"},
                },
            ]

    monkeypatch.setattr(admin, "CalendarService", FakeCalendarService)

    response = admin_client.get("/admin/api/appointments?days=999")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert len(data["items"]) == 1
    assert data["items"][0]["event_id"] == "appt-1"
    assert data["items"][0]["patient_phone"] == "11999999999"


def test_calendar_errors_return_consistent_payload(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    class BrokenCalendarService:
        def list_events_between(self, start_date: datetime, end_date: datetime):
            raise RuntimeError("calendar unavailable")

    monkeypatch.setattr(admin, "CalendarService", BrokenCalendarService)

    response = admin_client.get("/admin/api/appointments")

    assert response.status_code == 200
    assert response.json() == {"ok": False, "error": "calendar unavailable", "items": []}


def test_blocks_can_be_listed_created_and_deleted(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    calls: dict[str, object] = {}

    class FakeCalendarService:
        def list_day_blocks(self, start_date: datetime, end_date: datetime):
            calls["list_range"] = (start_date, end_date)
            return [
                {
                    "event_id": "block-1",
                    "summary": "[WPP-DENTAL] Bloqueio de agenda",
                    "description": "Curso",
                    "start_date": "2026-05-20",
                    "end_date": "2026-05-21",
                }
            ]

        def create_day_block(self, block_date: datetime, reason: str = ""):
            calls["created"] = (block_date, reason)
            return {"id": "block-2"}

        def delete_day_block(self, event_id: str):
            calls["deleted"] = event_id
            return True

    monkeypatch.setattr(admin, "CalendarService", FakeCalendarService)

    list_response = admin_client.get("/admin/api/blocks?days=999")
    create_response = admin_client.post(
        "/admin/api/blocks",
        json={"date": "2026-05-20", "reason": "Curso"},
    )
    delete_response = admin_client.delete("/admin/api/blocks/block-2")

    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["event_id"] == "block-1"

    assert create_response.status_code == 200
    assert create_response.json()["ok"] is True
    assert create_response.json()["event_id"] == "block-2"
    created_date, reason = calls["created"]
    assert created_date.tzinfo == SAO_PAULO_TZ
    assert created_date.strftime("%Y-%m-%d") == "2026-05-20"
    assert reason == "Curso"

    assert delete_response.status_code == 200
    assert delete_response.json() == {"ok": True}
    assert calls["deleted"] == "block-2"


def test_invalid_block_date_returns_422(admin_client: TestClient):
    response = admin_client.post("/admin/api/blocks", json={"date": "20/05/2026"})

    assert response.status_code == 422
    assert response.json()["detail"] == "Data invalida. Use YYYY-MM-DD."


def test_main_app_includes_admin_router_once():
    import src.main as main

    admin_get_routes = [
        route
        for route in main.app.routes
        if getattr(route, "path", "") == "/admin" and "GET" in getattr(route, "methods", set())
    ]

    assert len(admin_get_routes) == 1
