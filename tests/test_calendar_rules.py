"""Testes das regras de agenda e sugestao de horarios."""

from datetime import datetime, timedelta

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
