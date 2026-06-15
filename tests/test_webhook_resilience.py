"""Testes de resiliencia do webhook — Implementacao 001.

Cobre:
- EVENT-LOOP: process_message roda fora do event loop (via asyncio.to_thread).
- WE-10: falha de SQLite no claim/mark e na leitura de estado nao vira HTTP 500.
"""

import os
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient


def _build_payload(message_id: str = "msg-res-1") -> dict:
    return {
        "event": "messages.upsert",
        "data": {
            "key": {
                "id": message_id,
                "fromMe": False,
                "remoteJid": "5511999999999@s.whatsapp.net",
            },
            "pushName": "Maria",
            "message": {"conversation": "Oi"},
        },
    }


class TestWebhookResilience:
    def setup_method(self):
        self.db_path = Path("./data/test_webhook_resilience.db")
        os.environ["DATABASE_PATH"] = str(self.db_path)
        os.environ["WEBHOOK_API_KEY"] = "test-secret"
        os.environ["ENABLE_APPOINTMENT_CONFIRMATION_SCHEDULER"] = "0"
        os.environ.pop("EVOLUTION_WEBHOOK_API_KEY", None)
        os.environ.pop("EVOLUTION_API_KEY", None)

        from src.infrastructure.persistence.connection import close_db

        close_db()

    def teardown_method(self):
        from src.infrastructure.persistence.connection import close_db

        close_db()
        self.db_path.unlink(missing_ok=True)

    def _mock_io(self, monkeypatch, process_message):
        import src.main as main

        async def fake_send_message(self, phone, message):
            return True

        monkeypatch.setattr(main.dental_crew, "process_message", process_message)
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )
        return main

    def test_process_message_runs_off_event_loop_thread(self, monkeypatch):
        """CA-003: process_message executa fora do event loop (asyncio.to_thread)."""
        import asyncio

        captured = {}

        def fake_process_message(**kwargs):
            try:
                asyncio.get_running_loop()
                captured["in_loop"] = True
            except RuntimeError:
                captured["in_loop"] = False
            return "ok"

        main = self._mock_io(monkeypatch, fake_process_message)

        with TestClient(main.app) as client:
            response = client.post("/webhook/message", json=_build_payload("offload-1"))

        assert response.status_code == 200
        # Se rodasse direto no handler async, haveria event loop na thread -> in_loop True.
        assert captured.get("in_loop") is False

    def test_sqlite_error_in_claim_and_mark_does_not_return_500(self, monkeypatch):
        """CA-004: erro de SQLite no claim/mark degrada com seguranca, sem 500."""
        import importlib

        # O pacote re-exporta `app` (instancia FastAPI), entao buscamos o modulo real.
        app_module = importlib.import_module("src.interfaces.http.app")

        main = self._mock_io(monkeypatch, lambda **kwargs: "ok")

        with TestClient(main.app) as client:
            # Patch apos o startup (lifespan ja criou as tabelas via connection.get_db).
            def boom():
                raise sqlite3.OperationalError("database is locked")

            monkeypatch.setattr(app_module, "get_db", boom)
            response = client.post("/webhook/message", json=_build_payload("claim-degraded-1"))

        assert response.status_code == 200
        assert response.json()["status"] == "processed"

    def test_state_get_falls_back_to_default_on_db_error(self, monkeypatch):
        """CA-004: ConversationStateService.get degrada para estado padrao em erro de SQLite."""
        from src.application.services import conversation_state_service as css

        def boom():
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(css, "get_db", boom)

        state = css.ConversationStateService.get("5511999999999")

        assert isinstance(state, css.ConversationState)
        assert state.stage == "idle"
        assert state.intent == ""
