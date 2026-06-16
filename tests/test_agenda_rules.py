"""Testes de regressao das Regras de Agenda e Disponibilidade — Implementacao 007.

Cobre: WE-05/CA-02, AG-03, AG-08, AG-04, CA-07, CA-08, CA-03, CA-10, CA-09, WE-11, CO-05.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.infrastructure.integrations.calendar_service import CalendarService, SAO_PAULO_TZ


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(**kwargs):
    defaults = dict(
        offered_date="",
        offered_times=[],
        rejected_slots=[],
        excluded_dates=[],
        requested_weekday="",
        earliest_time="",
        pending_slot_date="",
        pending_slot_time="",
        intent="",
        stage="idle",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ===========================================================================
# T-002 / T-009 — ConfigService.get_holidays
# ===========================================================================
class TestGetHolidays:
    """CA-010 parcial: parsing de feriados configurados."""

    def test_retorna_lista_vazia_por_padrao(self):
        from src.infrastructure.config.config_service import ConfigService
        config = ConfigService()
        config._configs = {"settings": {"scheduling": {}}}
        assert config.get_holidays() == []

    def test_parse_dd_mm(self):
        from src.infrastructure.config.config_service import ConfigService
        config = ConfigService()
        config._configs = {"settings": {"scheduling": {"holidays": ["25/12", "01/01"]}}}
        holidays = config.get_holidays()
        assert "25/12" in holidays
        assert "01/01" in holidays

    def test_parse_dd_mm_yyyy(self):
        from src.infrastructure.config.config_service import ConfigService
        config = ConfigService()
        config._configs = {"settings": {"scheduling": {"holidays": ["07/09/2026"]}}}
        holidays = config.get_holidays()
        assert "07/09/2026" in holidays

    def test_entrada_invalida_ignorada(self, caplog):
        from src.infrastructure.config.config_service import ConfigService
        config = ConfigService()
        config._configs = {"settings": {"scheduling": {"holidays": ["invalido", "99/99"]}}}
        with caplog.at_level("WARNING"):
            holidays = config.get_holidays()
        assert holidays == []
        assert any("Feriado invalido" in r.message for r in caplog.records)


# ===========================================================================
# T-003 — Mínimo de dias úteis em create_appointment_if_available (WE-05/CA-02)
# ===========================================================================
class TestMinBusinessDaysCreation:
    """CA-001, CA-002: create_appointment_if_available rejeita dentro da janela proibida."""

    def _make_svc(self, min_bdays=2, holidays=None):
        svc = CalendarService.__new__(CalendarService)
        svc.config = MagicMock()
        svc.config.get_slot_duration.return_value = 15
        svc.config.get_max_days_ahead.return_value = 60
        svc.config.get_min_business_days_ahead.return_value = min_bdays
        svc.config.get_holidays.return_value = holidays or []
        svc.calendar_id = "primary"
        svc._service = None
        return svc

    def test_rejeita_slot_dentro_da_janela_proibida(self, monkeypatch):
        """CA-001: slot dentro da janela minima lanca ValueError."""
        svc = self._make_svc(min_bdays=2)
        monkeypatch.setattr(svc, "_is_within_business_hours", lambda s, e: True)
        # tomorrow is within 2-business-day window
        tomorrow = datetime.now(SAO_PAULO_TZ).replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)
        # skip to weekday if tomorrow is weekend
        while tomorrow.weekday() >= 5:
            tomorrow += timedelta(days=1)
        with pytest.raises(ValueError, match="dias uteis"):
            svc.create_appointment_if_available("Maria", "5511999999999", tomorrow)

    def test_aceita_slot_apos_janela(self, monkeypatch):
        """CA-001: slot fora da janela proibida nao lanca ValueError por janela."""
        svc = self._make_svc(min_bdays=2)
        monkeypatch.setattr(svc, "_is_within_business_hours", lambda s, e: True)
        monkeypatch.setattr(svc, "find_appointments_by_phone", lambda phone: [])
        monkeypatch.setattr(svc, "_slot_conflicts", lambda s, e: False)
        monkeypatch.setattr(svc, "create_appointment", lambda *a, **kw: {"id": "evt-ok"})
        # 3 business days from now
        target = datetime.now(SAO_PAULO_TZ).replace(hour=9, minute=0, second=0, microsecond=0)
        counted = 0
        while counted < 3:
            target += timedelta(days=1)
            if target.weekday() < 5:
                counted += 1
        result = svc.create_appointment_if_available("Maria", "5511999999999", target)
        assert result["id"] == "evt-ok"

    def test_rejeita_slot_em_feriado_dentro_da_janela(self, monkeypatch):
        """CA-010: feriado conta como dia nao util no calculo da janela minima."""
        now = datetime.now(SAO_PAULO_TZ)
        # find next 2 business days, the first of which will be marked as holiday
        day1 = now.date() + timedelta(days=1)
        while day1.weekday() >= 5:
            day1 += timedelta(days=1)
        # mark day1 as holiday
        holiday_str = day1.strftime("%d/%m")
        svc = self._make_svc(min_bdays=2, holidays=[holiday_str])
        monkeypatch.setattr(svc, "_is_within_business_hours", lambda s, e: True)
        # day1 slot would normally be 1 business day away but is a holiday
        # with holiday, the 2nd real business day is day2 (skipping day1)
        slot = datetime.combine(day1, datetime.min.time()).replace(
            tzinfo=SAO_PAULO_TZ, hour=9
        )
        # After the holiday, the window extends — day1 should be rejected
        with pytest.raises(ValueError):
            svc.create_appointment_if_available("Maria", "5511999999999", slot)


# ===========================================================================
# T-004 — Mínimo de dias úteis em GetAvailableSlotsTool (WE-05)
# ===========================================================================
class TestMinBusinessDaysTool:
    """CA-003: GetAvailableSlotsTool rejeita data especifica dentro da janela proibida."""

    def test_data_dentro_da_janela_retorna_mensagem_informativa(self, monkeypatch):
        import src.interfaces.tools.calendar_tool as ct
        from src.interfaces.tools.calendar_tool import GetAvailableSlotsTool
        tool = GetAvailableSlotsTool()
        # Find next weekday (within 2-business-day window)
        tomorrow = datetime.now(SAO_PAULO_TZ) + timedelta(days=1)
        while tomorrow.weekday() >= 5:
            tomorrow += timedelta(days=1)
        tomorrow_str = tomorrow.strftime("%d/%m/%Y")
        monkeypatch.setattr(ct.ConfigService, "get_min_business_days_ahead", lambda self: 2)
        monkeypatch.setattr(ct.ConfigService, "get_holidays", lambda self: [])
        result = tool._run(date=tomorrow_str)
        assert "janela minima" in result
        assert "buscar_proximo_dia_disponivel" in result

    def test_data_fora_da_janela_retorna_slots(self, monkeypatch):
        import src.interfaces.tools.calendar_tool as ct
        from src.interfaces.tools.calendar_tool import GetAvailableSlotsTool
        tool = GetAvailableSlotsTool()
        # Use a date 10 business days from now
        target = datetime.now(SAO_PAULO_TZ)
        counted = 0
        while counted < 10:
            target += timedelta(days=1)
            if target.weekday() < 5:
                counted += 1
        date_str = target.strftime("%d/%m/%Y")
        monkeypatch.setattr(ct.ConfigService, "get_min_business_days_ahead", lambda self: 2)
        monkeypatch.setattr(ct.ConfigService, "get_holidays", lambda self: [])
        monkeypatch.setattr(ct.ConfigService, "get_suggestions_count", lambda self: 2)
        monkeypatch.setattr(
            ct.CalendarService,
            "get_available_slots",
            lambda self, d, p=None: [{"formatted": f"{date_str} as 09:00"}],
        )
        result = tool._run(date=date_str)
        assert "janela minima" not in result
        assert "09:00" in result


# ===========================================================================
# T-005 — _is_offered_slot fail-closed (AG-03 + AG-08)
# ===========================================================================
class TestIsOfferedSlotFailClosed:
    """CA-004, CA-005, CA-007: _is_offered_slot nega por padrao."""

    def test_nega_quando_sem_oferta_previa(self):
        """CA-004 (AG-03): sem offered_date/offered_times retorna False."""
        from src.application.services.clean_agent_service import _is_offered_slot
        state = _make_state()
        assert _is_offered_slot("20/06/2026 09:00", state) is False

    def test_nega_para_data_malformada(self):
        """CA-005 (AG-03): data malformada retorna False, nao True."""
        from src.application.services.clean_agent_service import _is_offered_slot
        state = _make_state(offered_date="20/06/2026", offered_times=["09:00"])
        assert _is_offered_slot("nao-e-data", state) is False

    def test_aceita_slot_ofertado_valido(self):
        """Slot valido dentro do estado ofertado deve retornar True."""
        from src.application.services.clean_agent_service import _is_offered_slot
        state = _make_state(offered_date="20/06/2026", offered_times=["09:00", "10:00"])
        assert _is_offered_slot("20/06/2026 09:00", state) is True

    def test_weekday_comparado_por_int(self):
        """CA-007 (AG-08): weekday comparado por tipo consistente (int x int)."""
        from src.application.services.clean_agent_service import _is_offered_slot
        # 20/06/2026 is Saturday (5) — weekday("4"=sexta) should reject
        state = _make_state(
            offered_date="20/06/2026",
            offered_times=["09:00"],
            requested_weekday="4",  # sexta-feira
        )
        # Saturday (5) != Friday (4) → False
        assert _is_offered_slot("20/06/2026 09:00", state) is False

    def test_weekday_str_int_consistente_com_match(self):
        """AG-08: requested_weekday como string '4' deve ser compativel com dt.weekday() int."""
        from src.application.services.clean_agent_service import _is_offered_slot
        # 19/06/2026 is Friday (4)
        state = _make_state(
            offered_date="19/06/2026",
            offered_times=["09:00"],
            requested_weekday="4",
        )
        assert _is_offered_slot("19/06/2026 09:00", state) is True


# ===========================================================================
# T-006 — _has_valid_direct_plan aceita "Particular" (AG-04)
# ===========================================================================
class TestHasValidDirectPlan:
    """CA-006: paciente Particular tem plano direto valido."""

    def test_particular_retorna_true(self, monkeypatch):
        from src.application.services.clean_agent_service import _has_valid_direct_plan
        from src.infrastructure.config.config_service import ConfigService
        monkeypatch.setattr(
            "src.application.services.clean_agent_service.PatientService.find_by_phone",
            lambda phone: None,
        )
        config = ConfigService()
        config._configs = {}
        state = _make_state(plan_name="Particular")
        assert _has_valid_direct_plan("5511999999999", state, config) is True

    def test_particular_case_insensitive(self, monkeypatch):
        from src.application.services.clean_agent_service import _has_valid_direct_plan
        from src.infrastructure.config.config_service import ConfigService
        monkeypatch.setattr(
            "src.application.services.clean_agent_service.PatientService.find_by_phone",
            lambda phone: None,
        )
        config = ConfigService()
        config._configs = {}
        state = _make_state(plan_name="particular")
        assert _has_valid_direct_plan("5511999999999", state, config) is True

    def test_plano_referral_continua_bloqueado(self, monkeypatch):
        from src.application.services.clean_agent_service import _has_valid_direct_plan
        from src.infrastructure.config.config_service import ConfigService
        monkeypatch.setattr(
            "src.application.services.clean_agent_service.PatientService.find_by_phone",
            lambda phone: None,
        )
        config = ConfigService()
        config._configs = {"settings": {"plans": [{"name": "Unimed", "referral": True}]}}
        state = _make_state(plan_name="Unimed")
        assert _has_valid_direct_plan("5511999999999", state, config) is False


# ===========================================================================
# T-007 — resolve_selection exige contexto de hora (CA-07)
# ===========================================================================
class TestResolveSelectionHoraComContexto:
    """CA-008: numero solto nao seleciona horario; contexto explícito sim."""

    def _offer(self, date_str, times):
        from src.domain.policies.appointment_offer_service import AppointmentOffer
        return AppointmentOffer(date_str=date_str, times=times)

    def test_numero_solto_nao_seleciona(self):
        """Numero sem contexto de hora nao deve selecionar horario."""
        from src.domain.policies.appointment_offer_service import AppointmentOfferService
        offer = self._offer("20/06/2026", ["09:00", "10:00"])
        # "somos 3 pessoas" — digit 3 never matches _FIRST or _SECOND_OPTION_PATTERN
        result = AppointmentOfferService.resolve_selection("somos 3 pessoas", offer)
        assert result is None

    def test_numero_com_h_seleciona(self):
        """'9h' deve selecionar 09:00."""
        from src.domain.policies.appointment_offer_service import AppointmentOfferService
        offer = self._offer("20/06/2026", ["09:00", "10:00"])
        result = AppointmentOfferService.resolve_selection("9h", offer)
        assert result == "09:00"

    def test_as_numero_seleciona(self):
        """'as 10' deve selecionar 10:00."""
        from src.domain.policies.appointment_offer_service import AppointmentOfferService
        offer = self._offer("20/06/2026", ["09:00", "10:00"])
        result = AppointmentOfferService.resolve_selection("as 10", offer)
        assert result == "10:00"

    def test_numero_horas_seleciona(self):
        """'9 horas' deve selecionar 09:00."""
        from src.domain.policies.appointment_offer_service import AppointmentOfferService
        offer = self._offer("20/06/2026", ["09:00", "10:00"])
        result = AppointmentOfferService.resolve_selection("9 horas", offer)
        assert result == "09:00"

    def test_dia_numero_sem_contexto_hora_nao_seleciona(self):
        """'dia 3, 2 pessoas' sem contexto de hora nao seleciona nada."""
        from src.domain.policies.appointment_offer_service import AppointmentOfferService
        offer = self._offer("20/06/2026", ["02:00", "03:00"])
        result = AppointmentOfferService.resolve_selection("dia 3, 2 pessoas", offer)
        assert result is None


# ===========================================================================
# T-008 — Resolução de ano por data de referência (CA-08)
# ===========================================================================
class TestResolveYear:
    """CA-009: oferta DD/MM feita no fim de ano usa o proximo ano quando necessario."""

    def test_data_futura_mantém_ano_atual(self):
        """Data futura: nao deve usar proximo ano."""
        from src.domain.policies.appointment_offer_service import AppointmentOfferService
        # Use far future date to guarantee it stays in current year
        result = AppointmentOfferService._resolve_year("31/12")
        now = datetime.now()
        # 31/12 in current year >= today → keep year
        # (unless today IS 31/12, but that would add 1 year which is fine)
        expected_year = now.year
        dec31 = datetime(now.year, 12, 31)
        if dec31.date() < now.date():
            expected_year += 1
        assert result == f"31/12/{expected_year}"

    def test_data_passada_resolve_para_proximo_ano(self, monkeypatch):
        """CA-009: DD/MM ja no passado resolve para o proximo ano."""
        from src.domain.policies.appointment_offer_service import AppointmentOfferService
        # Simulate being on Dec 31 by patching datetime.now in the service
        import src.domain.policies.appointment_offer_service as mod

        class FakeDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 12, 31, 10, 0)

        monkeypatch.setattr(mod, "datetime", FakeDT)
        # 02/01 in 2026 is in the past relative to Dec 31, 2026
        result = AppointmentOfferService._resolve_year("02/01")
        assert result == "02/01/2027"

    def test_extract_latest_offer_resolve_ano(self, monkeypatch):
        """extract_latest_offer usa _resolve_year para data DD/MM."""
        from src.domain.policies.appointment_offer_service import AppointmentOfferService
        import src.domain.policies.appointment_offer_service as mod

        class FakeDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 12, 31, 10, 0)

        monkeypatch.setattr(mod, "datetime", FakeDT)
        history = [
            {
                "role": "assistant",
                "content": (
                    "Encontrei horarios disponiveis em 02/01 as 09:00 e 10:00. "
                    "Qual voce prefere?"
                ),
            }
        ]
        offer = AppointmentOfferService.extract_latest_offer(history)
        assert offer is not None
        assert offer.date_str == "02/01/2027"


# ===========================================================================
# T-009 — Feriados na contagem de dias úteis (CA-03)
# ===========================================================================
class TestHolidaysInBusinessDaysCount:
    """CA-010: feriado configurado e pulado na contagem de dias uteis."""

    def test_create_appointment_pula_feriado(self, monkeypatch):
        """Feriado conta como dia nao util — slot no dia apos feriado deve ser aceito."""
        from src.infrastructure.integrations.calendar_service import CalendarService
        svc = CalendarService.__new__(CalendarService)
        now = datetime.now(SAO_PAULO_TZ)

        # Find 1st weekday from tomorrow
        day1 = now.date() + timedelta(days=1)
        while day1.weekday() >= 5:
            day1 += timedelta(days=1)
        # Find 2nd weekday
        day2 = day1 + timedelta(days=1)
        while day2.weekday() >= 5:
            day2 += timedelta(days=1)

        # Mark day1 as holiday → day2 is now 1st real business day, day3 is 2nd
        holiday_str = day1.strftime("%d/%m")
        day3 = day2 + timedelta(days=1)
        while day3.weekday() >= 5:
            day3 += timedelta(days=1)

        svc.config = MagicMock()
        svc.config.get_slot_duration.return_value = 15
        svc.config.get_max_days_ahead.return_value = 60
        svc.config.get_min_business_days_ahead.return_value = 2
        svc.config.get_holidays.return_value = [holiday_str]
        svc.calendar_id = "primary"

        monkeypatch.setattr(svc, "_is_within_business_hours", lambda s, e: True)

        # day2 is 1st business day (day1 is holiday) — within window → reject
        slot_day2 = datetime.combine(day2, datetime.min.time()).replace(
            tzinfo=SAO_PAULO_TZ, hour=9
        )
        with pytest.raises(ValueError, match="dias uteis"):
            svc.create_appointment_if_available("Maria", "5511999999999", slot_day2)

    def test_find_next_skips_holiday(self, monkeypatch):
        """FindNextAvailableDayTool pula feriado na busca."""
        import src.interfaces.tools.calendar_tool as ct
        from src.interfaces.tools.calendar_tool import FindNextAvailableDayTool

        class FixedNow(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 5, 26, 10, 0, tzinfo=tz)

        tool = FindNextAvailableDayTool()
        monkeypatch.setattr(ct, "datetime", FixedNow)
        # From May 26 (Tue), 2 bdays: May 27 (Wed) + May 28 (Thu); but mark May 27 as holiday
        # So: May 27 = holiday (skip), May 28 = bday 1, May 29 = bday 2 → earliest = May 29
        monkeypatch.setattr(ct.ConfigService, "get_min_business_days_ahead", lambda self: 2)
        monkeypatch.setattr(ct.ConfigService, "get_holidays", lambda self: ["27/05"])
        monkeypatch.setattr(ct.ConfigService, "get_suggestions_count", lambda self: 1)
        monkeypatch.setattr(ct.ConfigService, "get_max_days_ahead", lambda self: 30)

        found = []

        def fake_slots(self, target, period=None):
            found.append(target.strftime("%d/%m/%Y"))
            return [{"formatted": f"{target.strftime('%d/%m/%Y')} as 09:00"}]

        monkeypatch.setattr(ct.CalendarService, "get_available_slots", fake_slots)

        result = tool._run(min_business_days=2)
        # First day offered must be >= May 29 (holiday counted properly)
        assert found, "get_available_slots deve ser chamado"
        first_day = datetime.strptime(found[0], "%d/%m/%Y").date()
        from datetime import date as date_type
        assert first_day >= date_type(2026, 5, 29), f"Expected >= 29/05/2026, got {first_day}"


# ===========================================================================
# T-010 — Filtro de eventos cancelled (CA-10)
# ===========================================================================
class TestCancelledEventsFiltered:
    """CA-011: eventos cancelled nao aparecem em buscas nem bloqueiam slots."""

    def test_find_appointments_filtra_cancelled(self, monkeypatch):
        """find_appointments_by_phone ignora eventos com status=cancelled."""
        from src.infrastructure.integrations.calendar_service import CalendarService
        svc = CalendarService.__new__(CalendarService)
        svc.config = MagicMock()
        svc.config.get_slot_duration.return_value = 15
        svc.calendar_id = "primary"

        cancelled_event = {
            "id": "evt-cancelled",
            "summary": "Maria - 5511999999999",
            "status": "cancelled",
            "start": {"dateTime": "2026-07-01T09:00:00-03:00"},
        }
        active_event = {
            "id": "evt-active",
            "summary": "Maria - 5511999999999",
            "start": {"dateTime": "2026-07-10T09:00:00-03:00"},
        }

        fake_service = MagicMock()
        fake_service.events().list().execute.return_value = {
            "items": [cancelled_event, active_event]
        }
        monkeypatch.setattr(svc, "_get_service", lambda: fake_service)
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.build_phone_search_term",
            lambda phone: "5511999999999",
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.phones_match",
            lambda a, b: True,
        )

        results = svc.find_appointments_by_phone("5511999999999")
        ids = [e["id"] for e in results]
        assert "evt-cancelled" not in ids
        assert "evt-active" in ids

    def test_get_available_slots_ignora_cancelled(self, monkeypatch):
        """get_available_slots nao bloqueia slot por evento cancelled."""
        from src.infrastructure.integrations.calendar_service import CalendarService
        from unittest.mock import patch

        svc = CalendarService.__new__(CalendarService)
        svc.config = MagicMock()
        svc.config.get_slot_duration.return_value = 60
        svc.config.get_periods.return_value = {"manha": {"start": "09:00", "end": "12:00"}}
        svc.calendar_id = "primary"

        target = datetime(2026, 7, 1, 9, 0, tzinfo=SAO_PAULO_TZ)
        cancelled_event = {
            "id": "evt-c",
            "status": "cancelled",
            "start": {"dateTime": target.isoformat()},
            "end": {"dateTime": (target + timedelta(hours=1)).isoformat()},
        }

        monkeypatch.setattr(svc, "get_events", lambda d, s=None, e=None: [cancelled_event])

        slots = svc.get_available_slots(target, "manha")
        # Cancelled event must not block the 09:00 slot
        assert any(s["start"].hour == 9 for s in slots), "09:00 deve estar disponivel"


# ===========================================================================
# T-011 — DST safety in _normalize_datetime (CA-09)
# ===========================================================================
class TestNormalizeDatetimeDST:
    """CA-012: datetime sem offset nao desloca hora em borda de DST."""

    def test_datetime_com_offset_convertido(self):
        """Aware datetime e convertido para Sao Paulo."""
        from zoneinfo import ZoneInfo
        utc = ZoneInfo("UTC")
        dt_utc = datetime(2026, 6, 15, 12, 0, 0, tzinfo=utc)
        result = CalendarService._normalize_datetime(dt_utc)
        assert result.tzinfo is not None
        # UTC-3 → 09:00 SP
        assert result.hour == 9

    def test_datetime_sem_offset_usa_sao_paulo(self):
        """Naive datetime e tratado como hora local em Sao Paulo."""
        naive = datetime(2026, 6, 15, 9, 0, 0)
        result = CalendarService._normalize_datetime(naive)
        assert result.tzinfo is not None
        assert result.hour == 9  # hora mantida como Sao Paulo local

    def test_datetime_sem_offset_fold_zero(self):
        """Naive datetime com fold=0 (primeira ocorrencia em DST ambiguo)."""
        # Simulate DST overlap: 2024-02-18 00:00 (horario de verao -> normal no Brasil)
        # Not testing calendar behavior, just that fold is set correctly
        naive = datetime(2026, 2, 15, 0, 0, 0)
        result = CalendarService._normalize_datetime(naive)
        assert result.fold == 0


# ===========================================================================
# T-012 — reset_context_if_finished preserva estado pendente (WE-11)
# ===========================================================================
class TestResetContextPreservaPendente:
    """CA-013: reset nao limpa quando ha agenda pendente."""

    def setup_method(self):
        import os
        from pathlib import Path
        self.db_path = Path("./data/test_agenda_rules_reset.db")
        os.environ["DATABASE_PATH"] = str(self.db_path)
        from src.infrastructure.persistence.connection import close_db, init_db
        close_db()
        init_db()

    def teardown_method(self):
        from src.infrastructure.persistence.connection import close_db
        close_db()
        self.db_path.unlink(missing_ok=True)

    def test_nao_reseta_com_oferta_pendente(self):
        """CA-013: has_pending_agenda=True impede o reset."""
        from src.application.services.conversation_service import ConversationService
        result = ConversationService.reset_context_if_finished(
            "5511999999999", has_pending_agenda=True
        )
        assert result is False

    def test_reseta_sem_agenda_pendente_quando_terminal(self):
        """reset funciona normalmente quando has_pending_agenda=False."""
        from src.application.services.conversation_service import ConversationService
        from src.infrastructure.persistence.connection import get_db
        db = get_db()
        db.execute(
            "INSERT INTO conversation_history (phone, role, content) VALUES (?, ?, ?)",
            ("5511000000001", "assistant", "consulta agendada com sucesso"),
        )
        db.commit()
        result = ConversationService.reset_context_if_finished(
            "5511000000001", has_pending_agenda=False
        )
        assert result is True

    def test_padroes_ambiguos_removidos(self):
        """WE-11: 'estou a disposicao' e 'posso ajudar com mais alguma coisa' nao sao terminais."""
        from src.application.services.conversation_service import ConversationService
        assert not ConversationService.is_terminal_assistant_message(
            "Estou a disposicao para ajudar com qualquer duvida."
        )
        assert not ConversationService.is_terminal_assistant_message(
            "Posso ajudar com mais alguma coisa?"
        )

    def test_padrao_agendamento_confirmado_e_terminal(self):
        """'consulta agendada com sucesso' ainda e terminal."""
        from src.application.services.conversation_service import ConversationService
        assert ConversationService.is_terminal_assistant_message(
            "Sua consulta agendada com sucesso! Ate breve."
        )
