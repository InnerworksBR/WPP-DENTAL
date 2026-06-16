"""Testes de regressão para Impl 005 — Cancelamento Seguro.

Cobre: CA-06 (CancelResult tipado), CA-01 (sem inferência por nome), CA-07 (event_id mismatch),
WE-01 (sucesso apenas com cancel real), CO-04 (não cancela em resposta ambígua).
"""

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.infrastructure.integrations.calendar_service import CancelResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event(event_id: str, summary: str, hour: int = 8) -> dict:
    return {
        "id": event_id,
        "summary": summary,
        "start": {"dateTime": datetime(2026, 4, 7, hour, 0).isoformat()},
    }


def _fake_service(events_list, delete_raises=None):
    """Cria um mock do service Google Calendar."""
    mock = MagicMock()
    events_api = MagicMock()
    mock.events.return_value = events_api
    list_call = MagicMock()
    events_api.list.return_value = list_call
    list_call.execute.return_value = {"items": events_list}

    delete_call = MagicMock()
    events_api.delete.return_value = delete_call
    if delete_raises:
        delete_call.execute.side_effect = delete_raises
    else:
        delete_call.execute.return_value = None
    return mock


# ---------------------------------------------------------------------------
# T-010 — Testes unitários de cancel_appointment (CA-06)
# ---------------------------------------------------------------------------


class TestCancelAppointmentResult:
    """Garante que CancelResult diferencia 2xx, 404/410 e erro real."""

    def test_success_returns_cancelled_true(self, monkeypatch):
        from src.infrastructure.integrations.calendar_service import CalendarService
        service = CalendarService()
        monkeypatch.setattr(service, "_get_service", lambda: _fake_service([]))
        result = service.cancel_appointment("evt-123")
        assert result.cancelled is True
        assert result.already_absent is False
        assert result.error is None

    def test_empty_event_id_returns_false_without_calling_api(self, monkeypatch):
        from src.infrastructure.integrations.calendar_service import CalendarService
        called = {"n": 0}

        def boom():
            called["n"] += 1
            return MagicMock()

        service = CalendarService()
        monkeypatch.setattr(service, "_get_service", boom)
        result = service.cancel_appointment("")
        assert result.cancelled is False
        assert result.error == "event_id ausente"
        assert called["n"] == 0

    def test_none_event_id_returns_false(self, monkeypatch):
        from src.infrastructure.integrations.calendar_service import CalendarService
        service = CalendarService()
        monkeypatch.setattr(service, "_get_service", lambda: _fake_service([]))
        result = service.cancel_appointment(None)
        assert result.cancelled is False
        assert result.error == "event_id ausente"

    def test_http_404_returns_already_absent(self, monkeypatch):
        from googleapiclient.errors import HttpError
        from src.infrastructure.integrations.calendar_service import CalendarService
        resp = MagicMock()
        resp.status = "404"
        exc = HttpError(resp=resp, content=b"Not Found")
        service = CalendarService()
        monkeypatch.setattr(service, "_get_service", lambda: _fake_service([], delete_raises=exc))
        result = service.cancel_appointment("evt-gone")
        assert result.cancelled is True
        assert result.already_absent is True
        assert result.error is None

    def test_http_410_returns_already_absent(self, monkeypatch):
        from googleapiclient.errors import HttpError
        from src.infrastructure.integrations.calendar_service import CalendarService
        resp = MagicMock()
        resp.status = "410"
        exc = HttpError(resp=resp, content=b"Gone")
        service = CalendarService()
        monkeypatch.setattr(service, "_get_service", lambda: _fake_service([], delete_raises=exc))
        result = service.cancel_appointment("evt-deleted")
        assert result.cancelled is True
        assert result.already_absent is True

    def test_network_error_returns_cancelled_false_with_error(self, monkeypatch, caplog):
        from src.infrastructure.integrations.calendar_service import CalendarService
        service = CalendarService()
        monkeypatch.setattr(service, "_get_service", lambda: _fake_service([], delete_raises=ConnectionError("timeout")))
        with caplog.at_level(logging.ERROR, logger="wpp-dental"):
            result = service.cancel_appointment("evt-abc")
        assert result.cancelled is False
        assert result.error is not None

    def test_http_401_returns_cancelled_false(self, monkeypatch):
        from googleapiclient.errors import HttpError
        from src.infrastructure.integrations.calendar_service import CalendarService
        resp = MagicMock()
        resp.status = "401"
        exc = HttpError(resp=resp, content=b"Unauthorized")
        service = CalendarService()
        monkeypatch.setattr(service, "_get_service", lambda: _fake_service([], delete_raises=exc))
        result = service.cancel_appointment("evt-abc")
        assert result.cancelled is False
        assert result.error is not None


# ---------------------------------------------------------------------------
# T-011 — Testes da CancelAppointmentTool (CA-01, CA-07)
# ---------------------------------------------------------------------------


class TestCancelAppointmentToolRules:
    """Garante que a tool não cancela por nome e trata CA-07.

    Monkeypatch em calendar_tool.CalendarService (referência local do módulo tool).
    """

    def _patch(self, monkeypatch, events, cancel_result=None):
        from src.interfaces.tools import calendar_tool

        cr = cancel_result or CancelResult(cancelled=True, already_absent=False, error=None)

        class FakeCS:
            def find_appointments_by_phone(self, phone):
                return events
            def cancel_appointment(self, eid):
                return cr

        monkeypatch.setattr(calendar_tool, "CalendarService", FakeCS)

    def test_no_events_returns_not_found(self, monkeypatch):
        from src.interfaces.tools.calendar_tool import CancelAppointmentTool
        self._patch(monkeypatch, [])
        result = CancelAppointmentTool()._run("Ana", "11999999999")
        assert "nao encontrei" in result.lower()

    def test_single_event_cancels_without_event_id(self, monkeypatch):
        from src.interfaces.tools.calendar_tool import CancelAppointmentTool
        self._patch(monkeypatch, [_event("evt-1", "Ana Costa", 9)])
        result = CancelAppointmentTool()._run("Ana", "11999999999")
        assert "cancelada com sucesso" in result.lower()

    def test_multiple_events_without_event_id_returns_instruction(self, monkeypatch):
        """CA-01: >1 consulta sem event_id → instrução, sem inferência por nome."""
        from src.interfaces.tools.calendar_tool import CancelAppointmentTool
        self._patch(monkeypatch, [_event("evt-1", "Ana Costa", 8), _event("evt-2", "Ana Costa", 9)])
        result = CancelAppointmentTool()._run("Ana Costa", "11999999999")
        assert "mais de uma consulta" in result.lower()
        assert "event_id" in result.lower()

    def test_event_id_not_belonging_to_phone_returns_coherent_message(self, monkeypatch):
        """CA-07: event_id que não pertence ao telefone → mensagem coerente."""
        from src.interfaces.tools.calendar_tool import CancelAppointmentTool
        self._patch(monkeypatch, [_event("evt-1", "Ana Costa", 8), _event("evt-2", "Ana Costa", 9)])
        result = CancelAppointmentTool()._run("Ana Costa", "11999999999", event_id="evt-99")
        assert "nao encontrei essa consulta" in result.lower() or "consultar_agendamento" in result.lower()

    def test_correct_event_id_cancels_that_event(self, monkeypatch):
        from src.interfaces.tools.calendar_tool import CancelAppointmentTool
        self._patch(monkeypatch, [_event("evt-1", "Ana Costa", 8), _event("evt-2", "Ana Costa", 9)])
        result = CancelAppointmentTool()._run("Ana Costa", "11999999999", event_id="evt-2")
        assert "cancelada com sucesso" in result.lower()
        assert "09:00" in result

    def test_real_error_from_cancel_returns_error_message(self, monkeypatch):
        from src.interfaces.tools.calendar_tool import CancelAppointmentTool
        fail = CancelResult(cancelled=False, already_absent=False, error="connection refused")
        self._patch(monkeypatch, [_event("evt-1", "Ana", 8)], cancel_result=fail)
        result = CancelAppointmentTool()._run("Ana", "11999999999")
        assert "erro" in result.lower()


# ---------------------------------------------------------------------------
# T-012 — Testes de integração via TestClient (WE-01, CO-04)
# ---------------------------------------------------------------------------


class _DBMixin:
    def setup_method(self):
        from src.infrastructure.persistence.connection import close_db, init_db
        self.db_path = Path("./data/test_cancel_safe.db")
        os.environ["DATABASE_PATH"] = str(self.db_path)
        close_db()
        init_db()

    def teardown_method(self):
        from src.infrastructure.persistence.connection import close_db
        close_db()
        self.db_path.unlink(missing_ok=True)
        self.db_path.with_suffix(".db-wal").unlink(missing_ok=True)
        self.db_path.with_suffix(".db-shm").unlink(missing_ok=True)


def _build_payload(msg_id: str = "msg-1") -> dict:
    return {
        "event": "messages.upsert",
        "data": {
            "key": {"id": msg_id, "remoteJid": "5511999999999@s.whatsapp.net", "fromMe": False},
            "message": {"conversation": ""},
            "messageType": "conversation",
            "pushName": "Ana",
        },
    }


class TestHandleAppointmentConfirmationCancel(_DBMixin):
    """Testa _handle_appointment_confirmation nos caminhos de cancelamento via webhook."""

    def _build_confirmation_state(self, event_id="evt-abc"):
        from src.application.services.appointment_confirmation_service import AppointmentConfirmationService
        from src.application.services.conversation_state_service import ConversationState, ConversationStateService
        state = ConversationState(
            stage=AppointmentConfirmationService.CONFIRMATION_STAGE,
            pending_event_id=event_id,
            pending_event_label="07/04/2026 as 08:00",
        )
        ConversationStateService.save("5511999999999", state)
        return state

    def test_explicit_cancel_with_success_clears_state(self, monkeypatch):
        """CA-001: event_id válido + delete 2xx → sucesso + estado limpo."""
        import src.main as main
        from src.infrastructure.persistence.connection import init_db
        from src.application.services.conversation_state_service import ConversationStateService
        from fastapi.testclient import TestClient

        self._build_confirmation_state("evt-abc")

        async def fake_send(self, phone, message):
            return True

        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.cancel_appointment",
            lambda self, eid: CancelResult(cancelled=True, already_absent=False, error=None),
        )

        payload = _build_payload("cancel-ok")
        payload["data"]["message"]["conversation"] = "cancelar"

        with TestClient(main.app) as client:
            response = client.post("/webhook/message", json=payload, headers={"apikey": "test-secret"})

        assert response.status_code == 200
        assert response.json()["status"] == "appointment_cancelled"
        state = ConversationStateService.get("5511999999999")
        assert state.stage == "idle"

    def test_empty_event_id_sends_neutral_message_and_alerts(self, monkeypatch):
        """CA-002: event_id ausente → mensagem neutra + alerta à doutora."""
        import src.main as main
        from src.application.services.appointment_confirmation_service import AppointmentConfirmationService
        from src.application.services.conversation_state_service import ConversationState, ConversationStateService
        from fastapi.testclient import TestClient

        state = ConversationState(
            stage=AppointmentConfirmationService.CONFIRMATION_STAGE,
            pending_event_id="",
        )
        ConversationStateService.save("5511999999999", state)

        alerts = []

        async def fake_send(self, phone, message):
            return True

        def fake_send_alert(self, **kwargs):
            alerts.append(kwargs)

        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.alert_service.AlertService.send_alert",
            fake_send_alert,
        )

        payload = _build_payload("cancel-no-id")
        payload["data"]["message"]["conversation"] = "quero cancelar"

        with TestClient(main.app) as client:
            response = client.post("/webhook/message", json=payload, headers={"apikey": "test-secret"})

        assert response.status_code == 200
        assert response.json()["status"] == "cancel_failed_no_event_id"
        assert len(alerts) == 1

    def test_real_error_does_not_clear_state_and_alerts(self, monkeypatch):
        """CA-003: erro real no Calendar → mensagem neutra + alerta + estado preservado."""
        import src.main as main
        from src.application.services.conversation_state_service import ConversationStateService
        from fastapi.testclient import TestClient

        self._build_confirmation_state("evt-abc")

        alerts = []

        async def fake_send(self, phone, message):
            return True

        def fake_send_alert(self, **kwargs):
            alerts.append(kwargs)

        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.alert_service.AlertService.send_alert",
            fake_send_alert,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.cancel_appointment",
            lambda self, eid: CancelResult(cancelled=False, already_absent=False, error="timeout"),
        )

        payload = _build_payload("cancel-fail")
        payload["data"]["message"]["conversation"] = "cancelar"

        with TestClient(main.app) as client:
            response = client.post("/webhook/message", json=payload, headers={"apikey": "test-secret"})

        assert response.status_code == 200
        assert response.json()["status"] == "cancel_failed"
        state = ConversationStateService.get("5511999999999")
        assert state.pending_event_id == "evt-abc"
        assert len(alerts) == 1

    def test_idempotent_404_treated_as_success(self, monkeypatch):
        """CA-008: 404/410 → cancelled=True, already_absent=True → sucesso idempotente."""
        import src.main as main
        from fastapi.testclient import TestClient

        self._build_confirmation_state("evt-gone")

        async def fake_send(self, phone, message):
            return True

        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.cancel_appointment",
            lambda self, eid: CancelResult(cancelled=True, already_absent=True, error=None),
        )

        payload = _build_payload("cancel-404")
        payload["data"]["message"]["conversation"] = "desmarcar"

        with TestClient(main.app) as client:
            response = client.post("/webhook/message", json=payload, headers={"apikey": "test-secret"})

        assert response.status_code == 200
        assert response.json()["status"] == "appointment_cancelled"

    def test_ambiguous_nao_requests_confirmation(self, monkeypatch):
        """CA-005: 'nao' isolado → pede confirmação, não cancela."""
        import src.main as main
        from src.application.services.conversation_state_service import ConversationStateService
        from fastapi.testclient import TestClient

        self._build_confirmation_state("evt-abc")

        cancel_called = {"n": 0}

        async def fake_send(self, phone, message):
            return True

        def fake_cancel(self, eid):
            cancel_called["n"] += 1
            return CancelResult(cancelled=True, already_absent=False, error=None)

        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.cancel_appointment",
            fake_cancel,
        )

        payload = _build_payload("cancel-ambiguous")
        payload["data"]["message"]["conversation"] = "nao"

        with TestClient(main.app) as client:
            response = client.post("/webhook/message", json=payload, headers={"apikey": "test-secret"})

        assert response.status_code == 200
        assert response.json()["status"] == "cancel_confirmation_requested"
        assert cancel_called["n"] == 0
        state = ConversationStateService.get("5511999999999")
        assert state.stage == "awaiting_cancel_confirmation"

    def test_nao_sei_does_not_cancel(self, monkeypatch):
        """CA-005: 'nao sei' → pede confirmação, não cancela."""
        import src.main as main
        from fastapi.testclient import TestClient

        self._build_confirmation_state("evt-abc")
        cancel_called = {"n": 0}

        async def fake_send(self, phone, message):
            return True

        def fake_cancel(self, eid):
            cancel_called["n"] += 1
            return CancelResult(cancelled=True, already_absent=False, error=None)

        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.cancel_appointment",
            fake_cancel,
        )

        payload = _build_payload("cancel-nao-sei")
        payload["data"]["message"]["conversation"] = "nao sei ainda"

        with TestClient(main.app) as client:
            response = client.post("/webhook/message", json=payload, headers={"apikey": "test-secret"})

        assert response.status_code == 200
        assert response.json()["status"] == "cancel_confirmation_requested"
        assert cancel_called["n"] == 0

    def test_explicit_cancel_confirmed_from_awaiting_state(self, monkeypatch):
        """CA-006: estado awaiting_cancel_confirmation + 'sim' → cancela."""
        import src.main as main
        from src.application.services.conversation_state_service import ConversationState, ConversationStateService
        from fastapi.testclient import TestClient

        state = ConversationState(
            stage="awaiting_cancel_confirmation",
            pending_event_id="evt-abc",
            pending_event_label="07/04/2026 as 08:00",
        )
        ConversationStateService.save("5511999999999", state)

        async def fake_send(self, phone, message):
            return True

        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.cancel_appointment",
            lambda self, eid: CancelResult(cancelled=True, already_absent=False, error=None),
        )

        payload = _build_payload("cancel-confirm-sim")
        payload["data"]["message"]["conversation"] = "sim"

        with TestClient(main.app) as client:
            response = client.post("/webhook/message", json=payload, headers={"apikey": "test-secret"})

        assert response.status_code == 200
        assert response.json()["status"] == "appointment_cancelled"
