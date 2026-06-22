"""Testes do orquestrador determinístico (impl 016).

Cobre o contrato da FSM e as transições já migradas (coleta de nome/plano, escalação fora de
escopo) e o comportamento de deferimento (`handled=False`) para o que ainda é do motor atual.
"""

from src.application.flow import ConversationOrchestrator, FlowState
from src.application.nlu import IntentClassifier
from src.application.services.conversation_state_service import ConversationState


class _FakeConfig:
    def __init__(self, plan=None):
        self._plan = plan

    def extract_plan_from_text(self, text):
        return self._plan

    def get_message(self, key, **kw):
        return "Vou encaminhar para a doutora e ela entrara em contato."

    def get_doctor_name(self):
        return "Dra. Teste"

    def get_openai_model(self):
        return "gpt-4o-mini"


class _FakeLLM:
    def __init__(self, intent="outro"):
        self._intent = intent

    def invoke(self, prompt):
        return type("R", (), {"intent": self._intent})()


def _orch(plan=None, llm_intent="outro"):
    config = _FakeConfig(plan=plan)
    classifier = IntentClassifier(structured_llm=_FakeLLM(llm_intent), config=config)
    return ConversationOrchestrator(classifier=classifier, config=config)


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
    state = _pending_state(FlowState.AWAITING_NAME.value)
    res = _orch().handle("Maria Silva", state)
    assert res.handled is True
    assert res.status == "pending_slot_name_resolved"
    assert res.next_state.patient_name == "Maria Silva"
    assert res.next_state.stage == FlowState.IDLE.value
    assert any(e.kind == "upsert_patient" for e in res.effects)
    assert "Posso confirmar" in res.reply_text


def test_pending_name_placeholder_asks_again():
    state = _pending_state(FlowState.AWAITING_NAME.value)
    res = _orch().handle("12", state)
    assert res.handled is True
    assert res.status == "awaiting_name"


# ── Coleta de plano ─────────────────────────────────────────────────────────────


def test_pending_plan_resolved():
    state = _pending_state(FlowState.AWAITING_PLAN.value, patient_name="Joao Souza")
    res = _orch(plan={"name": "Amil", "referral": False}).handle("amil", state)
    assert res.handled is True
    assert res.status == "pending_slot_plan_resolved"
    assert res.next_state.plan_name == "Amil"
    assert any(e.kind == "upsert_patient" for e in res.effects)


def test_pending_plan_referral_escalates():
    state = _pending_state(FlowState.AWAITING_PLAN.value, patient_name="Joao")
    res = _orch(plan={"name": "SulAmerica", "referral": True}).handle("sulamerica", state)
    assert res.handled is True
    assert res.status == "pending_slot_plan_referral"
    assert any(e.kind == "clear_state" for e in res.effects)


def test_pending_plan_unknown_asks_again():
    state = _pending_state(FlowState.AWAITING_PLAN.value, patient_name="Joao")
    res = _orch(plan=None).handle("plano-inexistente-xyz", state)
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
