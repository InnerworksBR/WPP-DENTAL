"""Pacote de transporte de mensagens (WhatsApp).

Exporta o contrato neutro (`MessagingGateway`, `InboundMessage`) e a fábrica `get_gateway`,
que resolve o adapter concreto por `TRANSPORT_PROVIDER`.
"""

from .gateway import InboundMessage, MessagingGateway, get_gateway

__all__ = ["InboundMessage", "MessagingGateway", "get_gateway"]
