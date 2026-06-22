"""Contrato de transporte de mensagens (WhatsApp) e fábrica de adapters.

Isola o provedor de WhatsApp (hoje Evolution) atrás de uma interface única. O orquestrador
e o webhook passam a falar com `MessagingGateway`/`InboundMessage`, nunca com o formato cru
de um provedor específico — o que torna a troca de transporte um detalhe de um único adapter.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InboundMessage:
    """Mensagem recebida, neutra em relação ao provedor de WhatsApp."""

    phone: str
    text: str
    contact_name: str = ""
    message_id: str = ""
    from_me: bool = False


class MessagingGateway(ABC):
    """Interface única de transporte: parsear o webhook e enviar texto."""

    @abstractmethod
    def parse_inbound(self, payload: dict) -> "InboundMessage | None":
        """Extrai a mensagem do payload cru do webhook.

        Retorna ``None`` quando o payload não corresponde a uma mensagem de texto recebida
        (mesma semântica defensiva do parsing anterior do ``app.py``).
        """

    @abstractmethod
    async def send_text(self, phone: str, message: str, kind: str = "bot") -> bool:
        """Envia uma mensagem de texto ao paciente (assíncrono)."""

    @abstractmethod
    def send_text_sync(self, phone: str, message: str, kind: str = "bot") -> bool:
        """Versão síncrona do envio (para contextos não-async)."""


def get_gateway() -> MessagingGateway:
    """Resolve o adapter de transporte por ``TRANSPORT_PROVIDER`` (default: ``evolution``).

    Provedor desconhecido faz fallback para ``evolution`` com um aviso, em vez de derrubar
    a aplicação.
    """
    provider = os.getenv("TRANSPORT_PROVIDER", "evolution").strip().lower()
    from .evolution_adapter import EvolutionAdapter  # import tardio evita ciclo

    if provider not in ("", "evolution"):
        logger.warning(
            "TRANSPORT_PROVIDER='%s' desconhecido; usando 'evolution' como fallback.",
            provider,
        )
    return EvolutionAdapter()
