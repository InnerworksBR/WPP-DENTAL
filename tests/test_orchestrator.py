"""Testes do orquestrador determinístico (impl 016).

Cobre o contrato da FSM e as transições já migradas (coleta de nome/plano, escalação fora de
escopo) e o comportamento de deferimento (`handled=False`) para o que ainda é do motor atual.
"""

from src.application.flow import ConversationOrchestrator, FlowState
from src.application.nlu import IntentClassifier
from src.application.services.conversation_state_service import ConversationState


class _FakeConfig:
    def __init__(self, plan=None, direct_plan=None):
        self._plan = plan
        self._direct_plan = direct_plan  # plano válido p/ get_plan_by_name/find_plan_fuzzy

    def extract_plan_from_text(self, text):
        return self._plan

    def get_plan_by_name(self, name):
        return self._direct_plan

    def find_plan_fuzzy(self, query):
        return self._direct_plan

    def get_message(self, key, **kw):
        return "Vou encaminhar para a doutora e ela entrara em contato."

    def get_doctor_name(self):
        return "Dra. Teste"

    def get_openai_model(self):
        return "gpt-4o-mini"

    def get_suggestions_count(self):
        return 2


class _FakeLLM:
    def __init__(self, intent="outro"):
        self._intent = intent

    def invoke(self, prompt):
        return type("R", (), {"intent": self._intent})()


class _FakeCalendar:
    def __init__(self, events=None, slots=None):
        self._events = events or []
        self._slots = slots

    def find_appointments_by_phone(self, phone):
        return self._events

    def find_next_available_slots(self, **kwargs):
        return self._slots


def _orch(plan=None, llm_intent="outro", calendar=None, direct_plan=None):
    config = _FakeConfig(plan=plan, direct_plan=direct_plan)
    classifier = IntentClassifier(structured_llm=_FakeLLM(llm_intent), config=config)
    return ConversationOrchestrator(classifier=classifier, config=config, calendar=calendar)


_CONFIRM_HISTORY = [{"role": "assistant", "content": "Maria, separei este horario. Posso confirmar sua consulta?"}]


def _offer_state(**kw):
    return ConversationState(offered_date="23/06/2026", offered_times=["09:00", "10:00"], **kw)


def _evt(evt_id, dt):
    return {"id": evt_id, "start": {"dateTime": dt}}


def _pending_state(stage, **kw):
    return ConversationState(
        stage=stage,
        pending_slot_date="23/06/2026",
        pending_slot_time="09:00",
        **kw,
    )


# ── Contexto ────────────────────────────────────────────────────────────────────


def test_build_context_detects_pending_offer():
    state = ConversationState(offered_date="23/06/2026", offered_times=["09:00", "10:00"])
    ctx = _orch().build_context(state)
    assert ctx.has_pending_offer is True
    assert ctx.offered_times == ["09:00", "10:00"]


def test_build_context_detects_awaiting_name():
    state = ConversationState(stage=FlowState.AWAITING_NAME.value)
    ctx = _orch().build_context(state)
    assert ctx.awaiting_name is True


# ── Coleta de nome ──────────────────────────────────────────────────────────────


def test_pending_name_resolved():
    state = _pending_state(FlowState.AWAITING_NAME.value, plan_name="Amil")
    res = _orch().handle("Maria Silva", state)
    assert res.handled is True
    assert res.status == "pending_slot_name_resolved"
    # Fiel ao handler antigo: estado volta a IDLE total; o nome é persistido no cadastro (efeito).
    assert res.next_state.stage == FlowState.IDLE.value
    assert res.next_state.patient_name == ""
    assert res.next_state.pending_slot_date == ""
    upsert = [e for e in res.effects if e.kind == "upsert_patient"]
    assert upsert and upsert[0].payload["name"] == "Maria Silva"
    assert upsert[0].payload["plan"] == "Amil"
    assert "Posso confirmar" in res.reply_text


def test_pending_name_placeholder_asks_again():
    state = _pending_state(FlowState.AWAITING_NAME.value)
    res = _orch().handle("12", state)
    assert res.handled is True
    assert res.status == "awaiting_name"


# ── Coleta de plano ─────────────────────────────────────────────────────────────


def test_pending_plan_resolved():
    state = _pending_state(FlowState.AWAITING_PLAN.value)
    res = _orch(plan={"name": "Amil", "referral": False}).handle(
        "amil", state, resolved_name="Joao Souza"
    )
    assert res.handled is True
    assert res.status == "pending_slot_plan_resolved"
    assert res.next_state.plan_name == "Amil"
    assert any(e.kind == "upsert_patient" for e in res.effects)


def test_pending_plan_valid_but_no_name_asks_name():
    state = _pending_state(FlowState.AWAITING_PLAN.value)
    res = _orch(plan={"name": "Amil", "referral": False}).handle("amil", state, resolved_name="")
    assert res.handled is True
    assert res.status == "pending_slot_plan_awaiting_name"
    assert res.next_state.stage == FlowState.AWAITING_NAME.value
    assert res.next_state.plan_name == "Amil"


def test_pending_plan_referral_escalates():
    state = _pending_state(FlowState.AWAITING_PLAN.value)
    res = _orch(plan={"name": "SulAmerica", "referral": True}).handle(
        "sulamerica", state, resolved_name="Joao"
    )
    assert res.handled is True
    assert res.status == "pending_slot_plan_referral"
    assert any(e.kind == "clear_state" for e in res.effects)


def test_pending_plan_unknown_asks_again():
    state = _pending_state(FlowState.AWAITING_PLAN.value)
    res = _orch(plan=None).handle("plano-inexistente-xyz", state, resolved_name="Joao")
    assert res.handled is True
    assert res.status == "pending_slot_plan_unknown"


# ── Fora de escopo ──────────────────────────────────────────────────────────────


def test_out_of_scope_escalates():
    state = ConversationState()
    res = _orch(llm_intent="fora_escopo").handle("quanto custa um clareamento?", state)
    assert res.handled is True
    assert res.status == "escalated"
    kinds = {e.kind for e in res.effects}
    assert "alert_doctor" in kinds
    assert "clear_state" in kinds


# ── Deferimento (ainda do motor atual) ──────────────────────────────────────────


def test_scheduling_intent_is_deferred_for_now():
    state = ConversationState()
    res = _orch().handle("quero agendar uma consulta", state)
    assert res.handled is False
    assert res.status == "deferred"
    assert res.nlu is not None


# ── Cancelamento orgânico (try_cancellation) ────────────────────────────────────


def test_cancellation_single_appointment():
    cal = _FakeCalendar([_evt("evt-1", "2026-05-19T09:15:00-03:00")])
    res = _orch(calendar=cal).try_cancellation("quero cancelar", ConversationState(), "5511999999999")
    assert res.handled is True
    assert res.status == "cancel_confirmation_requested"
    assert res.next_state.stage == FlowState.AWAITING_CANCEL_CONFIRMATION.value
    assert res.next_state.pending_event_id == "evt-1"
    assert "19/05/2026 as 09:15" in res.reply_text


def test_cancellation_no_appointment():
    res = _orch(calendar=_FakeCalendar([])).try_cancellation("cancelar", ConversationState(), "p")
    assert res.handled is True
    assert res.status == "cancel_no_appointment"


def test_cancellation_multiple_defers():
    cal = _FakeCalendar([_evt("a", "2026-05-19T09:15:00-03:00"), _evt("b", "2026-05-20T09:15:00-03:00")])
    res = _orch(calendar=cal).try_cancellation("cancelar", ConversationState(), "p")
    assert res.handled is False


def test_cancellation_non_cancel_defers():
    res = _orch(calendar=_FakeCalendar([])).try_cancellation("quero agendar", ConversationState(), "p")
    assert res.handled is False


def test_cancellation_reschedule_not_hijacked():
    cal = _FakeCalendar([_evt("a", "2026-05-19T09:15:00-03:00")])
    res = _orch(calendar=cal).try_cancellation("quero remarcar minha consulta", ConversationState(), "p")
    assert res.handled is False


def test_cancellation_only_in_idle():
    cal = _FakeCalendar([_evt("a", "2026-05-19T09:15:00-03:00")])
    res = _orch(calendar=cal).try_cancellation(
        "cancelar", ConversationState(stage="awaiting_cancel_confirmation"), "p"
    )
    assert res.handled is False


# ── Seleção de horário ofertado (try_slot_selection) ────────────────────────────


def test_slot_selection_confirmation_requested_with_valid_plan(monkeypatch):
    monkeypatch.setattr(
        "src.application.flow.orchestrator.PatientService.find_by_phone", lambda phone: None
    )
    orch = _orch(direct_plan={"name": "Amil", "referral": False})
    res = orch.try_slot_selection("1", _offer_state(plan_name="Amil"), "5511999999999", "Maria Silva", [])
    assert res.handled is True
    assert res.status == "slot_confirmation_requested"
    assert res.next_state.pending_slot_time == "09:00"
    assert res.next_state.stage == "idle"
    assert res.extra.get("selected_time") == "09:00"


def test_slot_selection_plan_required_without_plan(monkeypatch):
    monkeypatch.setattr(
        "src.application.flow.orchestrator.PatientService.find_by_phone", lambda phone: None
    )
    res = _orch().try_slot_selection("2", _offer_state(), "5511999999999", "Maria Silva", [])
    assert res.handled is True
    assert res.status == "slot_plan_required"
    assert res.next_state.stage == "awaiting_plan_for_slot_confirmation"
    assert res.next_state.pending_slot_time == "10:00"


def test_slot_selection_affirmative_confirmation_defers_to_proven_handler():
    res = _orch().try_slot_selection("sim", _offer_state(), "p", "Maria Silva", _CONFIRM_HISTORY)
    assert res.handled is False


def test_slot_selection_no_offer_defers():
    res = _orch().try_slot_selection("1", ConversationState(), "p", "Maria Silva", [])
    assert res.handled is False


def test_slot_selection_not_among_options_is_rejected():
    res = _orch().try_slot_selection("11:00", _offer_state(), "p", "Maria Silva", [])
    assert res.handled is True
    assert res.status == "slot_selection_rejected"


def test_slot_selection_name_uncertain_defers():
    res = _orch().try_slot_selection("1", _offer_state(), "5511999999999", "5511999999999", [])
    assert res.handled is False


# ── Re-oferta reativa (try_reactive_reoffer) ────────────────────────────────────


def test_reactive_reoffer_with_slots():
    cal = _FakeCalendar(slots={"date_str": "24/06/2026", "times": ["08:00", "09:00"]})
    res = _orch(calendar=cal).try_reactive_reoffer("quero outro dia", ConversationState(), "p", [])
    assert res.handled is True
    assert res.status == "reactive_reoffer"
    assert res.next_state.offered_date == "24/06/2026"
    assert res.next_state.offered_times == ["08:00", "09:00"]
    assert res.extra["offered_date"] == "24/06/2026"
    assert "08:00" in res.reply_text


def test_reactive_reoffer_no_slots():
    res = _orch(calendar=_FakeCalendar(slots=None)).try_reactive_reoffer(
        "quero outro dia", ConversationState(), "p", []
    )
    assert res.handled is True
    assert res.status == "reoffer_none"


def test_reactive_reoffer_error_defers():
    class _BoomCal:
        def find_next_available_slots(self, **kwargs):
            raise RuntimeError("boom")

    res = _orch(calendar=_BoomCal()).try_reactive_reoffer(
        "quero outro dia", ConversationState(), "p", []
    )
    assert res.handled is False
