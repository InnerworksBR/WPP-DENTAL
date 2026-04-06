"""Testes da orquestracao com LangGraph."""

from __future__ import annotations

import os
from pathlib import Path


class _FakeRouteModel:
    def __init__(self, decision):
        self.decision = decision

    def with_structured_output(self, schema):
        return self

    def invoke(self, messages):
        del messages
        return self.decision


class _FakeRephraseResponse:
    def __init__(self, content: str):
        self.content = content


class _FakeRephraseModel:
    def __init__(self, content: str):
        self.content = content

    def invoke(self, messages):
        del messages
        return _FakeRephraseResponse(self.content)


class TestLangGraphConversationService:
    """Valida o roteamento contextual e o fallback legado."""

    def setup_method(self):
        self.db_path = Path("./data/test_langgraph.db")
        os.environ["DATABASE_PATH"] = str(self.db_path)

        from src.infrastructure.persistence.connection import close_db, init_db

        close_db()
        init_db()

    def teardown_method(self):
        from src.infrastructure.persistence.connection import close_db

        close_db()
        self.db_path.unlink(missing_ok=True)

    def test_address_question_uses_contextual_branch(self, monkeypatch):
        from src.application.services.langgraph_conversation_service import (
            LangGraphConversationService,
            RouteDecision,
        )

        monkeypatch.setattr(
            LangGraphConversationService,
            "_build_route_model",
            lambda self: _FakeRouteModel(RouteDecision(route="address")),
        )
        monkeypatch.setattr(
            LangGraphConversationService,
            "_build_rephrase_model",
            lambda self: _FakeRephraseModel("A clínica fica na Benjamin Constant, 61 - sala 1114, Centro, São Vicente/SP."),
        )

        service = LangGraphConversationService()
        monkeypatch.setattr(
            service.workflow,
            "_handle_address_query",
            lambda: "Endereco bruto",
        )
        monkeypatch.setattr(
            service.workflow,
            "process_message",
            lambda **kwargs: (_ for _ in ()).throw(AssertionError("Nao deveria usar o legado aqui")),
        )

        response = service.process_message(
            patient_phone="5511999999999",
            patient_message="Qual o endereco mesmo?",
            history_text="ASSISTENTE: Oi",
            is_first_message=False,
        )

        assert "Benjamin Constant" in response

    def test_legacy_route_uses_existing_workflow(self, monkeypatch):
        from src.application.services.langgraph_conversation_service import (
            LangGraphConversationService,
            RouteDecision,
        )

        monkeypatch.setattr(
            LangGraphConversationService,
            "_build_route_model",
            lambda self: _FakeRouteModel(RouteDecision(route="legacy")),
        )
        monkeypatch.setattr(
            LangGraphConversationService,
            "_build_rephrase_model",
            lambda self: _FakeRephraseModel("Nao deveria reescrever"),
        )

        service = LangGraphConversationService()
        monkeypatch.setattr(
            service.workflow,
            "process_message",
            lambda **kwargs: "Fluxo legado",
        )

        response = service.process_message(
            patient_phone="5511999999999",
            patient_message="Quero remarcar",
            history_text="ASSISTENTE: Oi",
            is_first_message=False,
        )

        assert response == "Fluxo legado"

    def test_social_route_rephrases_polite_response(self, monkeypatch):
        from src.application.services.langgraph_conversation_service import (
            LangGraphConversationService,
            RouteDecision,
        )

        monkeypatch.setattr(
            LangGraphConversationService,
            "_build_route_model",
            lambda self: _FakeRouteModel(RouteDecision(route="social")),
        )
        monkeypatch.setattr(
            LangGraphConversationService,
            "_build_rephrase_model",
            lambda self: _FakeRephraseModel("Por nada! Se precisar, estou por aqui."),
        )

        service = LangGraphConversationService()

        response = service.process_message(
            patient_phone="5511999999999",
            patient_message="obrigado",
            history_text="ASSISTENTE: Claro, atendemos Unimed.",
            is_first_message=False,
        )

        assert response == "Por nada! Se precisar, estou por aqui."
