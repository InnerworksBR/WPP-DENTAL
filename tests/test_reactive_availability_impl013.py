"""Testes da implementação 013 — Disponibilidade Reativa e Cobertura do Cron.

Cobre:
- A: recusa ampla ("nenhum", "outro") marca recusa.
- B: captura de horário/dia específico ("11:00", "dia 23 as 18:30").
- Núcleo: CalendarService.find_next_available_slots.
- C: PatientService.find_by_name e resolução de telefone por nome no cron.
"""

import os
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest

from src.domain.policies.appointment_offer_service import AppointmentOfferService
from src.infrastructure.integrations.calendar_service import CalendarService, SAO_PAULO_TZ


# ─────────────────────────────────────────────────────────────────────────────
# A — Vocabulário de recusa amplo
# ─────────────────────────────────────────────────────────────────────────────
class TestRejectionVocabulary:
    @pytest.mark.parametrize("text", ["nenhum", "Nenhum", "nenhum desses", "não gostei", "outro", "mais opcoes", "tem outro"])
    def test_broad_rejections_are_detected(self, text):
        c = AppointmentOfferService.extract_request_constraints(text)
        assert c.rejects_current_slot is True
        assert c.changes_pending_confirmation is True

    @pytest.mark.parametrize("text", ["sim", "pode confirmar", "obrigada"])
    def test_non_rejections_are_not_flagged(self, text):
        c = AppointmentOfferService.extract_request_constraints(text)
        assert c.rejects_current_slot is False


# ─────────────────────────────────────────────────────────────────────────────
# B — Captura de horário/dia específico
# ─────────────────────────────────────────────────────────────────────────────
class TestSpecificTimeAndDayCapture:
    def test_bare_time_with_colon(self):
        c = AppointmentOfferService.extract_request_constraints("11:00 hrs tem disponivel")
        assert c.requested_time == "11:00"
        assert c.changes_pending_confirmation is True

    def test_hour_only_format(self):
        c = AppointmentOfferService.extract_request_constraints("consigo as 18h")
        assert c.requested_time == "18:00"

    def test_day_and_time(self):
        c = AppointmentOfferService.extract_request_constraints("dia 23 as 18:30")
        assert c.requested_time == "18:30"
        assert c.requested_day_number == 23

    def test_explicit_date(self):
        c = AppointmentOfferService.extract_request_constraints("pode ser 23/06/2026")
        assert c.requested_date == "23/06/2026"

    def test_excluded_day_is_not_requested_day(self):
        c = AppointmentOfferService.extract_request_constraints("menos no dia 23")
        assert c.requested_day_number == 0
        assert 23 in c.excluded_day_numbers


# ─────────────────────────────────────────────────────────────────────────────
# Núcleo — find_next_available_slots
# ─────────────────────────────────────────────────────────────────────────────
class TestFindNextAvailableSlots:
    def _slot(self, day: datetime, hh: int, mm: int) -> dict:
        start = datetime.combine(day.date(), time(hh, mm)).replace(tzinfo=SAO_PAULO_TZ)
        return {"start": start, "end": start, "formatted": start.strftime("%d/%m/%Y as %H:%M")}

    def test_returns_first_day_with_slots(self, monkeypatch):
        svc = CalendarService()
        start = datetime.now(SAO_PAULO_TZ) + timedelta(days=10)
        # garante dia util
        while start.weekday() >= 5:
            start += timedelta(days=1)

        monkeypatch.setattr(svc, "_is_holiday", lambda d, h: False)
        monkeypatch.setattr(
            svc, "get_available_slots",
            lambda date, period=None: [self._slot(date, 8, 0), self._slot(date, 8, 15), self._slot(date, 11, 15)],
        )

        result = svc.find_next_available_slots(start_date=start, limit=2)
        assert result is not None
        assert result["times"] == ["08:00", "08:15"]

    def test_earliest_time_filter(self, monkeypatch):
        svc = CalendarService()
        start = datetime.now(SAO_PAULO_TZ) + timedelta(days=10)
        while start.weekday() >= 5:
            start += timedelta(days=1)
        monkeypatch.setattr(svc, "_is_holiday", lambda d, h: False)
        monkeypatch.setattr(
            svc, "get_available_slots",
            lambda date, period=None: [self._slot(date, 8, 0), self._slot(date, 11, 15), self._slot(date, 11, 30)],
        )
        result = svc.find_next_available_slots(start_date=start, earliest_time="11:00", limit=2)
        assert result["times"] == ["11:15", "11:30"]

    def test_exclude_slots_skips_rejected(self, monkeypatch):
        svc = CalendarService()
        start = datetime.now(SAO_PAULO_TZ) + timedelta(days=10)
        while start.weekday() >= 5:
            start += timedelta(days=1)
        day_str = start.strftime("%d/%m/%Y")
        monkeypatch.setattr(svc, "_is_holiday", lambda d, h: False)
        monkeypatch.setattr(
            svc, "get_available_slots",
            lambda date, period=None: [self._slot(date, 8, 0), self._slot(date, 8, 15), self._slot(date, 11, 15)],
        )
        result = svc.find_next_available_slots(
            start_date=start,
            exclude_slots=[f"{day_str} 08:00", f"{day_str} 08:15"],
            limit=2,
        )
        assert result["times"] == ["11:15"]


# ─────────────────────────────────────────────────────────────────────────────
# C — Resolução de telefone por nome (cron)
# ─────────────────────────────────────────────────────────────────────────────
class TestPhoneResolutionByName:
    def setup_method(self):
        self.db_path = Path("./data/test_impl013.db")
        os.environ["DATABASE_PATH"] = str(self.db_path)
        from src.infrastructure.persistence.connection import close_db, init_db
        close_db()
        self.db_path.unlink(missing_ok=True)
        init_db()

    def teardown_method(self):
        from src.infrastructure.persistence.connection import close_db
        close_db()
        self.db_path.unlink(missing_ok=True)

    def test_find_by_name_unique_match(self):
        from src.application.services.patient_service import PatientService
        from src.infrastructure.persistence.connection import get_db
        db = get_db()
        db.execute("INSERT INTO patients (phone, name, plan) VALUES (?, ?, ?)", ("13991601479", "Flora Souza", "Particular"))
        db.commit()
        match = PatientService.find_by_name("flora souza")
        assert match is not None
        assert match["phone"] == "13991601479"

    def test_find_by_name_ambiguous_returns_none(self):
        from src.application.services.patient_service import PatientService
        from src.infrastructure.persistence.connection import get_db
        db = get_db()
        db.execute("INSERT INTO patients (phone, name) VALUES (?, ?)", ("13991601479", "Maria Silva"))
        db.execute("INSERT INTO patients (phone, name) VALUES (?, ?)", ("13988823598", "Maria Silva"))
        db.commit()
        assert PatientService.find_by_name("Maria Silva") is None

    def test_resolve_missing_phones_fills_from_registry(self):
        from src.application.services.appointment_confirmation_service import AppointmentConfirmationService
        from src.infrastructure.persistence.connection import get_db
        db = get_db()
        db.execute("INSERT INTO patients (phone, name) VALUES (?, ?)", ("13996099700", "Felipe Lima"))
        db.commit()

        svc = AppointmentConfirmationService()
        appts = [
            {"patient_phone": "", "patient_name": "Felipe Lima", "event_id": "e1"},
            {"patient_phone": "", "patient_name": "Bloqueio Interno", "event_id": "e2"},
        ]
        resolved = svc._resolve_missing_phones(appts)
        assert resolved[0]["patient_phone"] == "13996099700"
        assert resolved[1]["patient_phone"] == ""  # sem cadastro -> permanece vazio (logado)
