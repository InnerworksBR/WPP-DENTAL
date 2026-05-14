"""Testes das ferramentas de agenda expostas ao agente."""

from datetime import datetime


def _event(event_id: str, summary: str, hour: int) -> dict:
    return {
        "id": event_id,
        "summary": summary,
        "start": {"dateTime": datetime(2026, 4, 7, hour, 0).isoformat()},
    }


class TestCancelAppointmentTool:
    def test_refuses_ambiguous_cancel_without_event_id(self, monkeypatch):
        from src.interfaces.tools import calendar_tool
        from src.interfaces.tools.calendar_tool import CancelAppointmentTool

        calls = {"cancel": 0}

        class FakeCalendarService:
            def find_appointments_by_phone(self, patient_phone):
                return [
                    _event("evt-1", "Consulta Maria Silva", 8),
                    _event("evt-2", "Consulta Maria Silva", 9),
                ]

            def cancel_appointment(self, event_id):
                calls["cancel"] += 1
                return True

        monkeypatch.setattr(calendar_tool, "CalendarService", FakeCalendarService)

        result = CancelAppointmentTool()._run(
            patient_name="Maria Silva",
            patient_phone="5511999999999",
        )

        assert "mais de uma consulta futura" in result
        assert "consultar_agendamento" in result
        assert calls["cancel"] == 0

    def test_cancels_only_matching_event_id(self, monkeypatch):
        from src.interfaces.tools import calendar_tool
        from src.interfaces.tools.calendar_tool import CancelAppointmentTool

        calls = {"cancelled": []}

        class FakeCalendarService:
            def find_appointments_by_phone(self, patient_phone):
                return [
                    _event("evt-1", "Consulta Maria Silva", 8),
                    _event("evt-2", "Consulta Maria Silva", 9),
                ]

            def cancel_appointment(self, event_id):
                calls["cancelled"].append(event_id)
                return True

        monkeypatch.setattr(calendar_tool, "CalendarService", FakeCalendarService)

        result = CancelAppointmentTool()._run(
            patient_name="Maria Silva",
            patient_phone="5511999999999",
            event_id="evt-2",
        )

        assert "Consulta cancelada com sucesso" in result
        assert "Horario: 09:00" in result
        assert calls["cancelled"] == ["evt-2"]
