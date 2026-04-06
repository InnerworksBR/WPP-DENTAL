"""Testes das regras de agenda e sugestao de horarios."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.infrastructure.integrations.calendar_service import CalendarService, SAO_PAULO_TZ
from src.interfaces.tools.calendar_tool import FindNextAvailableDayTool


class TestCalendarRules:
    """Valida regras deterministicas do calendario."""

    def test_find_next_available_day_respects_configured_suggestions(self, monkeypatch):
        tool = FindNextAvailableDayTool()

        monkeypatch.setattr(
            "src.interfaces.tools.calendar_tool.ConfigService.get_min_business_days_ahead",
            lambda self: 2,
        )
        monkeypatch.setattr(
            "src.interfaces.tools.calendar_tool.ConfigService.get_suggestions_count",
            lambda self: 3,
        )
        monkeypatch.setattr(
            "src.interfaces.tools.calendar_tool.ConfigService.get_max_days_ahead",
            lambda self: 10,
        )
        monkeypatch.setattr(
            "src.interfaces.tools.calendar_tool.CalendarService.get_available_slots",
            lambda self, target, period=None: [
                {"formatted": "10/04/2026 as 09:00"},
                {"formatted": "10/04/2026 as 09:15"},
                {"formatted": "10/04/2026 as 09:30"},
                {"formatted": "10/04/2026 as 09:45"},
            ],
        )

        result = tool._run(period="manha", min_business_days=0)

        assert "1. 10/04/2026 as 09:00" in result
        assert "2. 10/04/2026 as 09:15" in result
        assert "3. 10/04/2026 as 09:30" in result
        assert "4. 10/04/2026 as 09:45" not in result

    def test_create_appointment_rejects_weekend(self, monkeypatch):
        service = CalendarService()
        future = datetime.now(SAO_PAULO_TZ) + timedelta(days=1)
        days_until_saturday = (5 - future.weekday()) % 7
        if days_until_saturday == 0:
            days_until_saturday = 7
        saturday = future + timedelta(days=days_until_saturday)
        saturday = saturday.replace(hour=9, minute=0, second=0, microsecond=0)

        monkeypatch.setattr(service, "_slot_conflicts", lambda start, end: False)
        monkeypatch.setattr(service, "create_appointment", lambda *args, **kwargs: {"id": "evt-1"})

        with pytest.raises(ValueError, match="finais de semana"):
            service.create_appointment_if_available("Maria", "5511999999999", saturday)

    def test_create_appointment_rejects_time_outside_business_hours(self, monkeypatch):
        service = CalendarService()
        future = datetime.now(SAO_PAULO_TZ) + timedelta(days=3)
        invalid_time = future.replace(hour=6, minute=0, second=0, microsecond=0)

        monkeypatch.setattr(service, "_slot_conflicts", lambda start, end: False)
        monkeypatch.setattr(service, "create_appointment", lambda *args, **kwargs: {"id": "evt-1"})

        with pytest.raises(ValueError, match="fora dos periodos de atendimento"):
            service.create_appointment_if_available("Maria", "5511999999999", invalid_time)

    def test_find_patient_appointments_for_date_extracts_name_and_phone(self, monkeypatch):
        service = CalendarService()

        monkeypatch.setattr(
            service,
            "get_events",
            lambda date, time_min=None, time_max=None: [
                {
                    "id": "evt-1",
                    "summary": "Maria Silva - 11999999999",
                    "description": (
                        "Agendamento automatico via WhatsApp\n"
                        "Paciente: Maria Silva\n"
                        "Telefone: 11999999999"
                    ),
                    "start": {"dateTime": "2026-04-07T08:00:00-03:00"},
                    "end": {"dateTime": "2026-04-07T08:15:00-03:00"},
                },
                {
                    "id": "evt-2",
                    "summary": "Bloqueio interno",
                    "start": {"dateTime": "2026-04-07T09:00:00-03:00"},
                    "end": {"dateTime": "2026-04-07T09:15:00-03:00"},
                },
            ],
        )

        appointments = service.find_patient_appointments_for_date(
            datetime(2026, 4, 7, 0, 0, tzinfo=SAO_PAULO_TZ)
        )

        assert len(appointments) == 1
        assert appointments[0]["event_id"] == "evt-1"
        assert appointments[0]["patient_name"] == "Maria Silva"
        assert appointments[0]["patient_phone"] == "11999999999"

    def test_get_service_uses_container_credentials_path_when_relative_file_is_missing(
        self, monkeypatch
    ):
        service = CalendarService()
        captured = {}

        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "./credentials/service-account.json")
        monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_EMAIL", raising=False)
        monkeypatch.delenv("GOOGLE_PRIVATE_KEY", raising=False)

        def fake_exists(path):
            return path == "/app/credentials/service-account.json"

        def fake_from_service_account_file(path, scopes):
            captured["path"] = path
            captured["scopes"] = scopes
            return object()

        def fake_build(api_name, version, credentials):
            captured["api_name"] = api_name
            captured["version"] = version
            captured["credentials"] = credentials
            return SimpleNamespace()

        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.os.path.exists",
            fake_exists,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.Credentials.from_service_account_file",
            fake_from_service_account_file,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.build",
            fake_build,
        )

        result = service._get_service()

        assert isinstance(result, SimpleNamespace)
        assert captured["path"] == "/app/credentials/service-account.json"
        assert captured["api_name"] == "calendar"
        assert captured["version"] == "v3"

    def test_get_service_error_lists_checked_paths_when_credentials_are_missing(self, monkeypatch):
        service = CalendarService()

        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "./credentials/service-account.json")
        monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_EMAIL", raising=False)
        monkeypatch.delenv("GOOGLE_PRIVATE_KEY", raising=False)
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.os.path.exists",
            lambda path: False,
        )

        with pytest.raises(FileNotFoundError) as exc_info:
            service._get_service()

        message = str(exc_info.value)
        assert "GOOGLE_SERVICE_ACCOUNT_FILE atual: ./credentials/service-account.json" in message
        assert "/app/credentials/service-account.json" in message
        assert "GOOGLE_SERVICE_ACCOUNT_EMAIL e GOOGLE_PRIVATE_KEY" in message
