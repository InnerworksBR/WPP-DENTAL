"""Testes da NLU estruturada (impl 015).

Foca nos caminhos determinísticos (sem rede), na desambiguação escolha-vs-restrição (CA-004),
na paridade de entidades com o extrator atual (CA-002) e no fallback quando o LLM falha.
"""

import pytest

from src.application.nlu import Intent, IntentClassifier, NluContext
from src.application.nlu.intent_classifier import _LLM_INTENT_MAP  # noqa: F401  (sanity de import)
from src.domain.policies.appointment_offer_service import AppointmentOfferService


class _FakeLLM:
    """LLM estruturado fake: retorna sempre a intenção fornecida."""

    def __init__(self, intent: str):
        self._intent = intent

    def invoke(self, prompt):
        return type("R", (), {"intent": self._intent})()


class _FailLLM:
    def invoke(self, prompt):
        raise RuntimeError("LLM indisponível")


def _clf(llm=None):
    # LLM fake por padrão evita qualquer chamada de rede nos testes determinísticos.
    return IntentClassifier(structured_llm=llm or _FailLLM())


# ── Intenções determinísticas ──────────────────────────────────────────────────


def test_greeting():
    res = _clf().classify("Oi, bom dia")
    assert res.intent == Intent.SAUDACAO


def test_schedule():
    res = _clf().classify("Quero agendar uma consulta")
    assert res.intent == Intent.AGENDAR


def test_cancel_takes_priority_over_consulta_word():
    res = _clf().classify("quero cancelar minha consulta")
    assert res.intent == Intent.CANCELAR


def test_reschedule():
    res = _clf().classify("preciso remarcar")
    assert res.intent == Intent.REMARCAR


def test_confirm_with_pending_confirmation():
    ctx = NluContext(has_pending_confirmation=True)
    res = _clf().classify("sim, pode confirmar", ctx)
    assert res.intent == Intent.CONFIRMAR
    assert res.entities.affirmation is True


def test_refuse_offer_broad_rejection():
    ctx = NluContext(has_pending_offer=True, offered_date="23/06/2026", offered_times=["09:00", "10:00"])
    res = _clf().classify("nenhum desses", ctx)
    assert res.intent == Intent.RECUSAR
    assert res.entities.rejects_current_slot is True


# ── Desambiguação escolha-vs-restrição (CA-004) ─────────────────────────────────


def test_selection_of_offered_time():
    ctx = NluContext(has_pending_offer=True, offered_date="23/06/2026", offered_times=["09:00", "10:00"])
    res = _clf().classify("pode ser as 9", ctx)
    assert res.intent == Intent.ESCOLHER_HORARIO
    assert res.entities.selected_time == "09:00"
    assert res.entities.selected_option == 1


def test_new_time_restriction_is_not_selection():
    ctx = NluContext(has_pending_offer=True, offered_date="23/06/2026", offered_times=["09:00", "10:00"])
    res = _clf().classify("so depois das 13h", ctx)
    assert res.intent == Intent.RECUSAR
    assert res.entities.earliest_time == "13:00"
    assert res.entities.selected_time == ""


# ── Nome e plano ────────────────────────────────────────────────────────────────


def test_awaiting_name_returns_name_intent():
    ctx = NluContext(awaiting_name=True)
    res = _clf().classify("Maria Silva", ctx)
    assert res.intent == Intent.INFORMAR_NOME
    assert res.entities.name == "Maria Silva"


def test_awaiting_plan_returns_plan_intent():
    ctx = NluContext(awaiting_plan=True)
    res = _clf().classify("amil dental", ctx)
    assert res.intent == Intent.INFORMAR_PLANO


# ── Camada LLM e fallback ───────────────────────────────────────────────────────


def test_llm_used_when_deterministic_undetermined():
    res = _clf(_FakeLLM("fora_escopo")).classify("quanto custa um clareamento?")
    assert res.intent == Intent.FORA_ESCOPO
    assert res.source == "llm"


def test_fallback_to_ambiguous_when_llm_fails():
    res = _clf(_FailLLM()).classify("xpto coisa qualquer aleatoria zzz")
    assert res.intent == Intent.AMBIGUO
    assert res.source == "deterministic"


# ── Paridade de entidades com o extrator atual (CA-002) ─────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "tarde de quinta, menos dia 1",
        "so depois das 13h",
        "dia 23 as 18:30",
        "nenhum, quero outro",
        "pode ser de manha",
    ],
)
def test_entity_parity_with_extractor(message):
    constraints = AppointmentOfferService.extract_request_constraints(message)
    entities = _clf().classify(message).entities
    assert entities.period == constraints.requested_period
    assert entities.earliest_time == constraints.earliest_time
    assert entities.weekday == constraints.requested_weekday
    assert entities.excluded_dates == constraints.excluded_dates
    assert entities.excluded_day_numbers == constraints.excluded_day_numbers
    assert entities.requested_day_number == constraints.requested_day_number
    assert entities.time == constraints.requested_time
    assert entities.date == constraints.requested_date
    assert entities.rejects_current_slot == constraints.rejects_current_slot
