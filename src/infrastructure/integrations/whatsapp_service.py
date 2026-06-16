"""Serviço de integração com a Evolution API (WhatsApp)."""

import asyncio
import os
import time
import logging

import httpx

from ..persistence.outbound_message_store import OutboundMessageStore

logger = logging.getLogger(__name__)

_WHATSAPP_SEND_RETRIES = int(os.getenv("WHATSAPP_SEND_RETRIES", "2"))


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
        Formata e valida o telefone para o padrão Evolution API.
        Rejeita números com DDD inválido ou tamanho incorreto.
        """
        if "@lid" in str(phone or "").lower():
            return ""

        digits = "".join(c for c in phone if c.isdigit())
        if not digits:
            return ""

        if not digits.startswith("55"):
            digits = "55" + digits

        # Telefones válidos: 55 + DDD(2) + número(8 ou 9) = 12 ou 13 dígitos
        if len(digits) not in (12, 13):
            logger.warning(
                "Telefone com tamanho invalido apos formatacao (%d digitos): %s",
                len(digits),
                digits,
            )
            return ""

        ddd = int(digits[2:4])
        if ddd < 11 or ddd > 99:
            logger.warning("DDD invalido: %s (telefone: %s)", ddd, digits)
            return ""

        return digits

    @staticmethod
    def _extract_message_id(response: httpx.Response) -> str:
        """Extrai o ID da mensagem retornado pela Evolution API quando disponivel."""
        try:
            payload = response.json()
        except ValueError:
            return ""
        if not isinstance(payload, dict):
            return ""
        key = payload.get("key", {})
        if isinstance(key, dict) and key.get("id"):
            return str(key["id"])
        return str(payload.get("id", ""))

    async def send_message(self, phone: str, message: str, kind: str = "bot") -> bool:
        """
        Envia uma mensagem de texto via WhatsApp com retry exponencial.

        Args:
            phone: Número do destinatário
            message: Texto da mensagem
            kind: Tipo de mensagem ('bot' ou 'doctor_alert')

        Returns:
            True se enviada com sucesso
        """
        formatted_phone = self._format_phone(phone)
        if not formatted_phone:
            logger.error("Destinatario invalido para envio WhatsApp: %s", phone)
            return False

        url = f"{self.base_url.rstrip('/')}/message/sendText/{self.instance}"
        payload = {"number": formatted_phone, "text": message}
        retries = int(os.getenv("WHATSAPP_SEND_RETRIES", "2"))

        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(
                        url, json=payload, headers=self._get_headers()
                    )
                    response.raise_for_status()
                    try:
                        OutboundMessageStore.record(
                            formatted_phone,
                            message,
                            self._extract_message_id(response),
                            kind=kind,
                        )
                    except Exception as exc:
                        logger.error(
                            "Falha ao registrar mensagem enviada (entrega ja concluida): %s",
                            exc,
                            exc_info=True,
                        )
                    logger.info("Mensagem enviada para %s", formatted_phone)
                    return True
            except httpx.HTTPError as e:
                if attempt < retries:
                    logger.warning(
                        "Tentativa %d/%d falhou para %s: %s — aguardando %ds",
                        attempt + 1,
                        retries + 1,
                        formatted_phone,
                        e,
                        2 ** attempt,
                    )
                    await asyncio.sleep(2 ** attempt)
                else:
                    logger.error(
                        "Erro ao enviar mensagem para %s apos %d tentativas: %s",
                        formatted_phone,
                        retries + 1,
                        e,
                    )
                    return False

        return False

    def send_message_sync(self, phone: str, message: str, kind: str = "bot") -> bool:
        """
        Versão síncrona do envio de mensagem com retry exponencial (para uso em tools CrewAI).
        """
        formatted_phone = self._format_phone(phone)
        if not formatted_phone:
            logger.error("Destinatario invalido para envio WhatsApp: %s", phone)
            return False

        url = f"{self.base_url.rstrip('/')}/message/sendText/{self.instance}"
        payload = {"number": formatted_phone, "text": message}
        retries = int(os.getenv("WHATSAPP_SEND_RETRIES", "2"))

        for attempt in range(retries + 1):
            try:
                with httpx.Client(timeout=30) as client:
                    response = client.post(
                        url, json=payload, headers=self._get_headers()
                    )
                    response.raise_for_status()
                    try:
                        OutboundMessageStore.record(
                            formatted_phone,
                            message,
                            self._extract_message_id(response),
                            kind=kind,
                        )
                    except Exception as exc:
                        logger.error(
                            "Falha ao registrar mensagem enviada (entrega ja concluida): %s",
                            exc,
                            exc_info=True,
                        )
                    logger.info("Mensagem enviada para %s", formatted_phone)
                    return True
            except httpx.HTTPError as e:
                if attempt < retries:
                    logger.warning(
                        "Tentativa %d/%d falhou para %s: %s — aguardando %ds",
                        attempt + 1,
                        retries + 1,
                        formatted_phone,
                        e,
                        2 ** attempt,
                    )
                    time.sleep(2 ** attempt)
                else:
                    logger.error(
                        "Erro ao enviar mensagem para %s apos %d tentativas: %s",
                        formatted_phone,
                        retries + 1,
                        e,
                    )
                    return False

        return False
