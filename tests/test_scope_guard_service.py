"""Testes do guardrail de escopo do atendimento."""

from src.domain.policies.scope_guard_service import ScopeGuardService


class TestScopeGuardService:
    """Valida o bloqueio deterministico de mensagens fora do escopo."""

    def test_detects_price_request(self):
        decision = ScopeGuardService.classify_patient_message(
            "Quanto custa um implante?"
        )

        assert decision is not None
        assert decision.reason == "fora_do_escopo"

    def test_detects_clinical_question(self):
        decision = ScopeGuardService.classify_patient_message(
            "Estou com muita dor e sangramento, o que eu faco?"
        )

        assert decision is not None
        assert decision.reason == "duvida_clinica"

    def test_allows_regular_scheduling_message(self):
        decision = ScopeGuardService.classify_patient_message(
            "Quero agendar consulta na proxima semana de tarde."
        )

        assert decision is None

    def test_flags_unsafe_generated_response(self):
        assert ScopeGuardService.response_is_safe("O implante custa R$ 500.") is False

    def test_allows_safe_escalation_response(self):
        assert (
            ScopeGuardService.response_is_safe(
                "Nao posso informar valores por aqui. A doutora entrara em contato."
            )
            is True
        )
