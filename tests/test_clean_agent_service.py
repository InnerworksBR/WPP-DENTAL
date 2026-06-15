"""Testes de resiliencia do motor de conversa (CleanAgentService) — Implementacao 001.

Cobre:
- AG-01: cliente LLM com request_timeout/max_tokens e retry + fallback amigavel.
- AG-06: excecao de tool vira mensagem segura no ToolMessage (sem vazar detalhe tecnico).
"""

import os
from pathlib import Path

import httpx
import pytest
from langchain_core.messages import AIMessage


class _FakeLLMBase:
    """Substitui ChatOpenAI: registra kwargs e devolve a si mesmo em bind_tools."""

    def __init__(self, **kwargs):
        type(self).captured = dict(kwargs)

    def bind_tools(self, tools):
        return self


class TestCleanAgentResilience:
    def setup_method(self):
        self.db_path = Path("./data/test_clean_agent.db")
        os.environ["DATABASE_PATH"] = str(self.db_path)
        os.environ["OPENAI_API_KEY"] = "test-key"
        from src.infrastructure.persistence.connection import close_db, init_db

        close_db()
        init_db()

    def teardown_method(self):
        from src.infrastructure.persistence.connection import close_db

        close_db()
        self.db_path.unlink(missing_ok=True)

    def test_llm_client_configured_with_timeout_and_max_tokens(self, monkeypatch):
        """CA-001: ChatOpenAI criado com request_timeout (20-30s) e max_tokens."""
        from src.application.services import clean_agent_service

        captured = {}

        class FakeLLM:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def bind_tools(self, tools):
                return self

        monkeypatch.setattr(clean_agent_service, "ChatOpenAI", FakeLLM)

        clean_agent_service.CleanAgentService()

        assert "request_timeout" in captured
        assert 20 <= float(captured["request_timeout"]) <= 30
        assert captured.get("max_tokens")
        assert captured.get("temperature") == 0

    def test_run_loop_returns_friendly_message_on_llm_timeout(self, monkeypatch):
        """CA-002: timeout do LLM -> retry curto e mensagem 'tente novamente', sem excecao."""
        from src.application.services import clean_agent_service
        from openai import APITimeoutError

        calls = {"n": 0}

        class FakeLLM(_FakeLLMBase):
            def invoke(self, messages):
                calls["n"] += 1
                raise APITimeoutError(request=httpx.Request("POST", "https://api.openai.com"))

        monkeypatch.setattr(clean_agent_service, "ChatOpenAI", FakeLLM)
        # nao dormir de verdade entre as tentativas
        monkeypatch.setattr(clean_agent_service.time, "sleep", lambda *a, **k: None)

        svc = clean_agent_service.CleanAgentService()
        out = svc._run_loop([], "5511999999999")

        assert calls["n"] == clean_agent_service._LLM_RETRY_ATTEMPTS
        assert out == clean_agent_service._LLM_BUSY_MESSAGE
        assert "instantes" in out.lower()

    def test_run_loop_masks_tool_exception(self, monkeypatch):
        """CA-006: excecao de tool -> ToolMessage seguro, sem stack trace nem detalhe tecnico."""
        from src.application.services import clean_agent_service

        responses = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "consultar_agendamento",
                        "args": {"patient_phone": "5511999999999"},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(content="Resposta final ao paciente"),
        ]
        seen_tool_messages = []

        class FakeLLM(_FakeLLMBase):
            def invoke(self, messages):
                for message in messages:
                    if message.__class__.__name__ == "ToolMessage":
                        seen_tool_messages.append(str(message.content))
                return responses.pop(0)

        monkeypatch.setattr(clean_agent_service, "ChatOpenAI", FakeLLM)

        svc = clean_agent_service.CleanAgentService()

        class BoomTool:
            def invoke(self, args):
                raise RuntimeError("Google HttpError 500 detalhe interno secreto")

        svc._tool_map["consultar_agendamento"] = BoomTool()

        out = svc._run_loop([], "5511999999999")

        assert out == "Resposta final ao paciente"
        assert seen_tool_messages, "esperava ao menos um ToolMessage capturado"
        joined = " ".join(seen_tool_messages)
        assert joined.startswith("Erro:") or "Erro:" in joined
        # nao pode vazar detalhe tecnico ao LLM/paciente
        assert "secreto" not in joined
        assert "HttpError" not in joined
