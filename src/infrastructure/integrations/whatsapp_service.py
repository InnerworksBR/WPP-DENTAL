"""Serviço de integração com a Evolution API (WhatsApp)."""

import os
import logging
from typing import Optional

import httpx

from ..persistence.outbound_message_store import OutboundMessageStore

logger = logging.getLogger(__name__)


class WhatsAppService:
    """Gerencia envio e recebimento de mensagens via Evolution API."""

    def __init__(self) -> None:
        self.base_url = os.getenv("EVOLUTION_API_URL", "http://localhost:8080")
        self.api_key = os.getenv("EVOLUTION_API_KEY", "")
        self.instance = os.getenv("EVOLUTION_INSTANCE", "dental-bot")

    def _get_headers(self) -> dict[str, str]:
        """Retorna headers padrão para a API."""
        return {
            "Content-Type": "application/json",
            "apikey": self.api_key,
        }

    def _format_phone(self, phone: str) -> str:
        """
        Formata o telefone para o padrão Evolution API.
        Remove caracteres especiais e garante formato correto.
        """
        # Remove tudo que não é dígito
        digits = "".join(c for c in phone if c.isdigit())

        # Se não começa com 55 (Brasil), adiciona
        if not digits.startswith("55"):
            digits = "55" + digits

        return digits

    async def send_message(self, phone: str, message: str) -> bool:
        """
        Envia uma mensagem de texto via WhatsApp.

        Args:
            phone: Número do destinatário
            message: Texto da mensagem

        Returns:
            True se enviada com sucesso
        """
        formatted_phone = self._format_phone(phone)
        url = f"{self.base_url.rstrip('/')}/message/sendText/{self.instance}"

        payload = {
            "number": formatted_phone,
            "text": message,
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers=self._get_headers(),
                )
                response.raise_for_status()
                OutboundMessageStore.record(formatted_phone, message)
                logger.info(f"Mensagem enviada para {formatted_phone}")
                return True
        except httpx.HTTPError as e:
            logger.error(f"Erro ao enviar mensagem para {formatted_phone}: {e}")
            return False

    def send_message_sync(self, phone: str, message: str) -> bool:
        """
        Versão síncrona do envio de mensagem (para uso em tools CrewAI).
        """
        formatted_phone = self._format_phone(phone)
        url = f"{self.base_url.rstrip('/')}/message/sendText/{self.instance}"

        payload = {
            "number": formatted_phone,
            "text": message,
        }

        try:
            with httpx.Client(timeout=30) as client:
                response = client.post(
                    url,
                    json=payload,
                    headers=self._get_headers(),
                )
                response.raise_for_status()
                OutboundMessageStore.record(formatted_phone, message)
                logger.info(f"Mensagem enviada para {formatted_phone}")
                return True
        except httpx.HTTPError as e:
            logger.error(f"Erro ao enviar mensagem para {formatted_phone}: {e}")
            return False
