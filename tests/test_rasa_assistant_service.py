"""Testes do bridge HTTP para o Rasa."""

from __future__ import annotations


class _DummyResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status={self.status_code}")


class TestRasaAssistantService:
    """Valida habilitacao, parse e consolidacao da resposta do Rasa."""

    def test_enabled_when_conversation_engine_is_rasa(self, monkeypatch):
        from src.application.services.rasa_assistant_service import RasaAssistantService

        monkeypatch.setenv("CONVERSATION_ENGINE", "rasa")
        monkeypatch.delenv("RASA_ASSISTANT_URL", raising=False)
        monkeypatch.delenv("RASA_WEBHOOK_URL", raising=False)

        assert RasaAssistantService.enabled() is True

    def test_process_message_reads_rest_webhook_and_merges_text_blocks(self, monkeypatch):
        from src.application.services.rasa_assistant_service import RasaAssistantService

        captured = {}

        def fake_post(url, json, timeout):
            captured["url"] = url
            captured["json"] = json
            captured["timeout"] = timeout
            return _DummyResponse(
                [
                    {"text": "Oi, Cristian."},
                    {"text": "Posso te ajudar com convenios, endereco ou agendamento."},
                ]
            )

        monkeypatch.setenv("RASA_ASSISTANT_URL", "http://rasa:5005")
        monkeypatch.delenv("RASA_WEBHOOK_URL", raising=False)
        monkeypatch.setattr("src.application.services.rasa_assistant_service.httpx.post", fake_post)

        service = RasaAssistantService()
        response = service.process_message(
            patient_phone="5511999999999",
            patient_message="Oi",
            patient_name="Cristian",
            history_text="Nenhum historico",
            is_first_message=True,
        )

        assert captured["url"] == "http://rasa:5005/webhooks/rest/webhook"
        assert captured["json"]["sender"] == "5511999999999"
        assert captured["json"]["message"] == "Oi"
        assert response == "Oi, Cristian.\n\nPosso te ajudar com convenios, endereco ou agendamento."

    def test_process_message_rejects_empty_text_response(self, monkeypatch):
        from src.application.services.rasa_assistant_service import RasaAssistantService

        monkeypatch.setenv("RASA_WEBHOOK_URL", "http://rasa:5005/webhooks/rest/webhook")
        monkeypatch.setattr(
            "src.application.services.rasa_assistant_service.httpx.post",
            lambda url, json, timeout: _DummyResponse([{"image": "ignored"}]),
        )

        service = RasaAssistantService()

        try:
            service.process_message(
                patient_phone="5511999999999",
                patient_message="Oi",
            )
        except RuntimeError as exc:
            assert "sem nenhum bloco de texto" in str(exc)
        else:
            raise AssertionError("Deveria falhar quando o Rasa nao devolve texto.")
