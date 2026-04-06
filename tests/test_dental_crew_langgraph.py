"""Testes da integracao do DentalCrew com o LangGraph."""


class _FakeLangGraphService:
    def __init__(self, enabled: bool = True, response: str = "via-graph", should_raise: bool = False):
        self._enabled = enabled
        self._response = response
        self._should_raise = should_raise

    def enabled(self) -> bool:
        return self._enabled

    def should_fallback_to_legacy(self) -> bool:
        return True

    def process_message(self, **kwargs) -> str:
        del kwargs
        if self._should_raise:
            raise RuntimeError("graph down")
        return self._response


class TestDentalCrewLangGraph:
    """Garante que a fachada ativa o LangGraph quando configurado."""

    def test_uses_langgraph_when_enabled(self, monkeypatch):
        import src.application.orchestration.dental_crew as dental_crew_module

        monkeypatch.setattr(
            dental_crew_module,
            "LangGraphConversationService",
            lambda: _FakeLangGraphService(enabled=True, response="via graph"),
        )

        crew = dental_crew_module.DentalCrew()
        monkeypatch.setattr(
            crew.workflow,
            "process_message",
            lambda **kwargs: "legacy",
        )

        response = crew.process_message(
            patient_phone="5511999999999",
            patient_message="Oi",
        )

        assert response == "via graph"

    def test_falls_back_to_legacy_when_langgraph_fails(self, monkeypatch):
        import src.application.orchestration.dental_crew as dental_crew_module

        monkeypatch.setattr(
            dental_crew_module,
            "LangGraphConversationService",
            lambda: _FakeLangGraphService(enabled=True, should_raise=True),
        )

        crew = dental_crew_module.DentalCrew()
        monkeypatch.setattr(
            crew.workflow,
            "process_message",
            lambda **kwargs: "legacy ok",
        )

        response = crew.process_message(
            patient_phone="5511999999999",
            patient_message="Oi",
        )

        assert response == "legacy ok"
