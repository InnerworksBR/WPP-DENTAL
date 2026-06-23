"""Testes da rotina automatica de confirmacao de consultas."""

import asyncio
import os
from datetime import datetime
from pathlib import Path

from src.infrastructure.integrations.calendar_service import SAO_PAULO_TZ


class TestAppointmentConfirmationService:
    """Valida o envio automatico e o rastreio das confirmacoes."""

    def setup_method(self):
        self.db_path = Path("./data/test_confirmation_job.db")
        os.environ["DATABASE_PATH"] = str(self.db_path)

        from src.infrastructure.persistence.connection import close_db

        close_db()

    def teardown_method(self):
        from src.infrastructure.persistence.connection import close_db

        close_db()
        self.db_path.unlink(missing_ok=True)

    def test_send_next_day_confirmations_persists_state_and_avoids_duplicates(self, monkeypatch):
        from src.application.services.appointment_confirmation_service import AppointmentConfirmationService
        from src.application.services.conversation_service import ConversationService
        from src.application.services.conversation_state_service import ConversationStateService
        from src.application.services.patient_service import PatientService
        from src.infrastructure.persistence.connection import get_db, init_db

        init_db()
        PatientService.upsert("5511999999999", "Maria Silva", "Amil Dental")
        # 019: isola o caminho de envio ao paciente (sem o relatorio diario a clinica, que
        # dispara quando DOCTOR_PHONE esta configurado — coberto por TestReminderCoverage019).
        monkeypatch.setattr(
            "src.infrastructure.config.config_service.ConfigService.get_doctor_phone",
            lambda self: "",
        )

        tomorrow_appointment = {
            "event_id": "evt-1",
            "patient_name": "Maria Silva",
            "patient_phone": "11999999999",
            "start_time": datetime(2026, 4, 7, 8, 0, tzinfo=SAO_PAULO_TZ),
            "end_time": datetime(2026, 4, 7, 8, 15, tzinfo=SAO_PAULO_TZ),
        }
        sent_messages: list[tuple[str, str]] = []

        async def fake_send_message(self, phone, message):
            sent_messages.append((phone, message))
            return True

        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.find_patient_appointments_for_date",
            lambda self, date: [tomorrow_appointment],
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )

        service = AppointmentConfirmationService()
        first_run = asyncio.run(
            service.send_next_day_confirmations(
                reference_time=datetime(2026, 4, 6, 20, 0, tzinfo=SAO_PAULO_TZ)
            )
        )
        second_run = asyncio.run(
            service.send_next_day_confirmations(
                reference_time=datetime(2026, 4, 6, 20, 5, tzinfo=SAO_PAULO_TZ)
            )
        )

        state = ConversationStateService.get("5511999999999")
        history = ConversationService.get_history("5511999999999")
        confirmation = get_db().execute(
            "SELECT status, phone FROM appointment_confirmations WHERE event_id = ?",
            ("evt-1",),
        ).fetchone()

        assert first_run["sent"] == 1
        assert second_run["skipped_duplicates"] + second_run["skipped_busy"] == 1
        assert len(sent_messages) == 1
        assert "consulta de amanha" in sent_messages[0][1].lower()
        assert state.stage == AppointmentConfirmationService.CONFIRMATION_STAGE
        assert state.plan_name == "Amil Dental"
        assert state.pending_event_id == "evt-1"
        assert history[-1]["role"] == "assistant"
        assert "Voce consegue comparecer?" in history[-1]["content"]
        assert confirmation is not None
        assert confirmation["status"] == "sent"
        assert confirmation["phone"] == "5511999999999"

    def test_send_next_day_confirmations_skips_patient_with_active_flow(self, monkeypatch):
        from src.application.services.appointment_confirmation_service import AppointmentConfirmationService
        from src.application.services.conversation_state_service import ConversationState, ConversationStateService
        from src.infrastructure.persistence.connection import get_db, init_db

        init_db()
        ConversationStateService.save(
            "5511999999999",
            ConversationState(
                stage="awaiting_cancel_confirmation",
                patient_name="Maria Silva",
                pending_event_id="evt-old",
                pending_event_label="07/04/2026 as 08:00",
            ),
        )

        tomorrow_appointment = {
            "event_id": "evt-1",
            "patient_name": "Maria Silva",
            "patient_phone": "11999999999",
            "start_time": datetime(2026, 4, 7, 8, 0, tzinfo=SAO_PAULO_TZ),
            "end_time": datetime(2026, 4, 7, 8, 15, tzinfo=SAO_PAULO_TZ),
        }

        async def fake_send_message(self, phone, message):
            return True

        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.find_patient_appointments_for_date",
            lambda self, date: [tomorrow_appointment],
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )

        service = AppointmentConfirmationService()
        result = asyncio.run(
            service.send_next_day_confirmations(
                reference_time=datetime(2026, 4, 6, 20, 0, tzinfo=SAO_PAULO_TZ)
            )
        )

        row = get_db().execute(
            "SELECT COUNT(*) AS total FROM appointment_confirmations"
        ).fetchone()

        assert result["sent"] == 0
        assert result["skipped_busy"] == 1
        assert row["total"] == 0


# ── 019: Lembretes confiáveis — cobertura observável (fim do descarte silencioso) ──


class TestReminderCoverage019:
    """019: cada paciente não contatado é registrado com nome+motivo; relatório diário à clínica."""

    def setup_method(self):
        self.db_path = Path("./data/test_coverage_019.db")
        os.environ["DATABASE_PATH"] = str(self.db_path)
        from src.infrastructure.persistence.connection import close_db, init_db
        close_db()
        init_db()

    def teardown_method(self):
        from src.infrastructure.persistence.connection import close_db
        close_db()
        self.db_path.unlink(missing_ok=True)

    def _appt(self, event_id, name, phone, hour=8):
        return {
            "event_id": event_id,
            "patient_name": name,
            "patient_phone": phone,
            "start_time": datetime(2026, 4, 7, hour, 0, tzinfo=SAO_PAULO_TZ),
            "end_time": datetime(2026, 4, 7, hour, 15, tzinfo=SAO_PAULO_TZ),
        }

    def _patch(self, monkeypatch, appointments, send_result=True, send_sink=None):
        async def fake_send_message(self, phone, message):
            if send_sink is not None:
                send_sink.append((phone, message))
            return send_result

        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.find_patient_appointments_for_date",
            lambda self, date: appointments,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )

    def _run(self, reference_time=datetime(2026, 4, 6, 20, 0, tzinfo=SAO_PAULO_TZ)):
        from src.application.services.appointment_confirmation_service import AppointmentConfirmationService
        return asyncio.run(AppointmentConfirmationService().send_next_day_confirmations(reference_time=reference_time))

    # CA-001/CA-006: caminho de descarte "sem telefone" deixa de ser silencioso
    def test_no_phone_patient_is_recorded_as_skip(self, monkeypatch):
        self._patch(monkeypatch, [self._appt("evt-1", "Joao Sem Fone", "")])
        result = self._run()
        details = result["skipped_details"]
        assert len(details) == 1
        assert details[0]["name"] == "Joao Sem Fone"
        assert "sem telefone" in details[0]["reason"]
        assert details[0]["category"] == "skipped"

    # CA-006: descarte por dados inválidos é observável
    def test_invalid_data_is_recorded_as_skip(self, monkeypatch):
        appt = self._appt("evt-x", "Sem Hora", "11999999999")
        appt["start_time"] = "nao-e-datetime"
        self._patch(monkeypatch, [appt])
        result = self._run()
        assert any("dados invalidos" in d["reason"] for d in result["skipped_details"])

    # CA-001: conversa em andamento (busy) é observável
    def test_busy_patient_is_recorded_as_skip(self, monkeypatch):
        from src.application.services.conversation_state_service import ConversationState, ConversationStateService
        ConversationStateService.save(
            "5511999999999", ConversationState(stage="awaiting_cancel_confirmation", patient_name="Maria")
        )
        self._patch(monkeypatch, [self._appt("evt-1", "Maria", "11999999999")])
        result = self._run()
        assert result["skipped_busy"] == 1
        assert any("conversa em andamento" in d["reason"] for d in result["skipped_details"])

    # CA-006: falha de envio é registrada como 'failed'
    def test_failed_delivery_is_recorded_as_failed(self, monkeypatch):
        self._patch(monkeypatch, [self._appt("evt-1", "Maria", "11999999999")], send_result=False)
        result = self._run()
        assert result["failed"] == 1
        failed = [d for d in result["skipped_details"] if d["category"] == "failed"]
        assert failed and "falha no envio" in failed[0]["reason"]

    @staticmethod
    def _set_doctor_phone(monkeypatch, phone):
        # Monkeypatch direto evita poluir o singleton de ConfigService (caching de ${DOCTOR_PHONE}).
        monkeypatch.setattr(
            "src.infrastructure.config.config_service.ConfigService.get_doctor_phone",
            lambda self: phone,
        )

    # CA-002: relatório diário enviado à clínica com nomes e motivos
    def test_daily_report_sent_with_names_and_reasons(self, monkeypatch):
        self._set_doctor_phone(monkeypatch, "5511888888888")
        sink: list = []
        self._patch(
            monkeypatch,
            [self._appt("evt-ok", "Maria", "11999999999", 8), self._appt("evt-nofone", "Joao", "", 9)],
            send_sink=sink,
        )
        self._run()
        reports = [m for (p, m) in sink if "Relatorio de lembretes" in m]
        assert len(reports) == 1
        report = reports[0]
        assert "07/04/2026" in report
        assert "Enviados: 1" in report
        assert "Pulados: 1" in report
        assert "Joao" in report and "sem telefone" in report

    # CA-002 borda: dia sem pendências reporta cobertura total
    def test_daily_report_all_contacted(self, monkeypatch):
        self._set_doctor_phone(monkeypatch, "5511888888888")
        sink: list = []
        self._patch(monkeypatch, [self._appt("evt-ok", "Maria", "11999999999")], send_sink=sink)
        self._run()
        report = next(m for (p, m) in sink if "Relatorio de lembretes" in m)
        assert "Enviados: 1" in report
        assert "Todos os pacientes do dia foram contatados" in report

    # CA-002: sem DOCTOR_PHONE, nenhum relatório é enviado (não quebra o cron)
    def test_no_report_without_doctor_phone(self, monkeypatch):
        self._set_doctor_phone(monkeypatch, "")
        sink: list = []
        self._patch(monkeypatch, [self._appt("evt-ok", "Maria", "11999999999")], send_sink=sink)
        self._run()
        assert all("Relatorio de lembretes" not in m for (p, m) in sink)

    # RF-006: falha ao enviar o relatório é persistida (nunca falha em silêncio)
    def test_report_delivery_failure_is_persisted(self, monkeypatch):
        from src.infrastructure.persistence.connection import get_db
        self._set_doctor_phone(monkeypatch, "5511888888888")
        self._patch(monkeypatch, [self._appt("evt-nofone", "Joao", "")], send_result=False)
        self._run()
        row = get_db().execute(
            "SELECT reason FROM pending_alerts WHERE reason = 'coverage_report_delivery_failed'"
        ).fetchone()
        assert row is not None

    # CA-004/CA-005: cobertura persistida para o /admin
    def test_coverage_persisted_to_store(self, monkeypatch):
        from src.infrastructure.persistence.reminder_coverage_store import ReminderCoverageStore
        self._patch(monkeypatch, [self._appt("evt-nofone", "Joao", "")])
        self._run()
        misses = ReminderCoverageStore.get_misses(run_date="2026-04-07")
        assert len(misses) == 1
        assert misses[0]["patient_name"] == "Joao"
        assert misses[0]["outcome"] == "skipped"
        assert ReminderCoverageStore.latest_run_date() == "2026-04-07"
