"""Bridge HTTP para um assistente Rasa CALM externo."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class RasaAssistantService:
    """Envia a mensagem do paciente para o Rasa e consolida a resposta textual."""

    DEFAULT_WEBHOOK_PATH = "/webhooks/rest/webhook"

    def __init__(self) -> None:
        self.webhook_url = self._resolve_webhook_url()
        self.timeout_seconds = float(os.getenv("RASA_TIMEOUT_SECONDS", "15"))

    @classmethod
    def _resolve_webhook_url(cls) -> str:
        direct_url = os.getenv("RASA_WEBHOOK_URL", "").strip()
        if direct_url:
            return direct_url

        assistant_url = os.getenv("RASA_ASSISTANT_URL", "").strip().rstrip("/")
        if assistant_url:
            return f"{assistant_url}{cls.DEFAULT_WEBHOOK_PATH}"
        return ""

    @classmethod
    def enabled(cls) -> bool:
        engine = os.getenv("CONVERSATION_ENGINE", "").strip().lower()
        if engine == "rasa":
            return True
        return bool(cls._resolve_webhook_url())

    @staticmethod
    def should_fallback_to_legacy() -> bool:
        raw_value = os.getenv("RASA_FALLBACK_TO_LEGACY", "1").strip().lower()
        return raw_value not in {"0", "false", "no", "off"}

    @staticmethod
    def _merge_text_responses(payload: Any) -> str:
        if isinstance(payload, dict):
            payload = [payload]

        if not isinstance(payload, list):
            return ""

        text_chunks: list[str] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            if text:
                text_chunks.append(text)
        return "\n\n".join(text_chunks).strip()

    def process_message(
        self,
        *,
        patient_phone: str,
        patient_message: str,
        patient_name: str = "",
        history_text: str | None = None,
        is_first_message: bool | None = None,
    ) -> str:
        """Processa a mensagem via Rasa REST webhook."""
        if not self.webhook_url:
            raise RuntimeError(
                "Rasa habilitado sem URL configurada. Defina RASA_WEBHOOK_URL ou RASA_ASSISTANT_URL."
            )

        payload = {
            "sender": patient_phone,
            "message": patient_message,
            "metadata": {
                "patient_name": patient_name,
                "history_text": history_text or "",
                "is_first_message": bool(is_first_message),
                "channel": "whatsapp",
            },
        }
        logger.info("Encaminhando mensagem de %s para o Rasa em %s", patient_phone, self.webhook_url)
        response = httpx.post(self.webhook_url, json=payload, timeout=self.timeout_seconds)
        response.raise_for_status()

        message = self._merge_text_responses(response.json())
        if not message:
            raise RuntimeError("Rasa respondeu sem nenhum bloco de texto utilizavel.")
        return message
