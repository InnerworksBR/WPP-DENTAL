"""Testes do Gateway de Transporte (impl 014).

Cobre o parsing do webhook movido para o EvolutionAdapter (preservação de comportamento) e a
fábrica get_gateway por TRANSPORT_PROVIDER. O envio é validado por delegação ao WhatsAppService.
"""

import asyncio

import pytest

from src.infrastructure.integrations.transport import (
    InboundMessage,
    MessagingGateway,
    get_gateway,
)
from src.infrastructure.integrations.transport import evolution_adapter
from src.infrastructure.integrations.transport.evolution_adapter import EvolutionAdapter


def _payload(data):
    return {"event": "messages.upsert", "instance": "dental-bot", "data": data}


def test_parse_conversation_text():
    adapter = EvolutionAdapter()
    msg = adapter.parse_inbound(
        _payload(
            {
                "key": {"remoteJid": "5511999998888@s.whatsapp.net", "id": "ABC123", "fromMe": False},
                "message": {"conversation": "Oi, quero agendar"},
                "pushName": "Maria",
            }
        )
    )
    assert isinstance(msg, InboundMessage)
    assert msg.phone == "5511999998888"
    assert msg.text == "Oi, quero agendar"
    assert msg.contact_name == "Maria"
    assert msg.message_id == "ABC123"
    assert msg.from_me is False


def test_parse_extended_text_message():
    adapter = EvolutionAdapter()
    msg = adapter.parse_inbound(
        _payload(
            {
                "key": {"remoteJid": "5511999998888@s.whatsapp.net", "id": "X"},
                "message": {"extendedTextMessage": {"text": "tudo bem?"}},
            }
        )
    )
    assert msg is not None
    assert msg.text == "tudo bem?"


def test_parse_lid_resolves_real_participant():
    adapter = EvolutionAdapter()
    msg = adapter.parse_inbound(
        _payload(
            {
                "key": {
                    "remoteJid": "1234567890@lid",
                    "participant": "5511988887777@s.whatsapp.net",
                    "id": "Y",
                },
                "message": {"conversation": "ola"},
            }
        )
    )
    assert msg is not None
    assert msg.phone == "5511988887777"


def test_parse_lid_without_real_jid_keeps_lid():
    adapter = EvolutionAdapter()
    msg = adapter.parse_inbound(
        _payload(
            {
                "key": {"remoteJid": "1234567890@lid", "id": "Z"},
                "message": {"conversation": "ola"},
            }
        )
    )
    assert msg is not None
    assert msg.phone == "1234567890@lid"


def test_parse_from_me_true():
    adapter = EvolutionAdapter()
    msg = adapter.parse_inbound(
        _payload(
            {
                "key": {"remoteJid": "5511999998888@s.whatsapp.net", "id": "M", "fromMe": True},
                "message": {"conversation": "resposta manual"},
            }
        )
    )
    assert msg is not None
    assert msg.from_me is True


def test_parse_non_text_returns_none():
    adapter = EvolutionAdapter()
    msg = adapter.parse_inbound(
        _payload(
            {
                "key": {"remoteJid": "5511999998888@s.whatsapp.net", "id": "I"},
                "message": {"imageMessage": {"url": "http://x"}},
            }
        )
    )
    assert msg is None


def test_parse_messages_list_form():
    adapter = EvolutionAdapter()
    msg = adapter.parse_inbound(
        _payload(
            {
                "messages": [
                    {
                        "key": {"remoteJid": "5511999998888@s.whatsapp.net", "id": "L1"},
                        "message": {"conversation": "via lista"},
                    }
                ]
            }
        )
    )
    assert msg is not None
    assert msg.text == "via lista"
    assert msg.message_id == "L1"


def test_parse_empty_payload_returns_none():
    adapter = EvolutionAdapter()
    assert adapter.parse_inbound({}) is None
    assert adapter.parse_inbound({"data": {}}) is None


def test_get_gateway_defaults_to_evolution(monkeypatch):
    monkeypatch.delenv("TRANSPORT_PROVIDER", raising=False)
    gateway = get_gateway()
    assert isinstance(gateway, EvolutionAdapter)
    assert isinstance(gateway, MessagingGateway)


def test_get_gateway_unknown_provider_falls_back(monkeypatch):
    monkeypatch.setenv("TRANSPORT_PROVIDER", "waha")
    gateway = get_gateway()
    assert isinstance(gateway, EvolutionAdapter)


def test_send_text_sync_delegates_to_whatsapp_service(monkeypatch):
    calls = {}

    class _FakeWhatsApp:
        def send_message_sync(self, phone, message, kind="bot"):
            calls["args"] = (phone, message, kind)
            return True

    monkeypatch.setattr(evolution_adapter, "WhatsAppService", _FakeWhatsApp)
    adapter = EvolutionAdapter()
    ok = adapter.send_text_sync("5511999998888", "oi", kind="bot")
    assert ok is True
    assert calls["args"] == ("5511999998888", "oi", "bot")


def test_send_text_async_delegates_to_whatsapp_service(monkeypatch):
    calls = {}

    class _FakeWhatsApp:
        async def send_message(self, phone, message, kind="bot"):
            calls["args"] = (phone, message, kind)
            return True

    monkeypatch.setattr(evolution_adapter, "WhatsAppService", _FakeWhatsApp)
    adapter = EvolutionAdapter()
    ok = asyncio.run(adapter.send_text("5511999998888", "ola", kind="doctor_alert"))
    assert ok is True
    assert calls["args"] == ("5511999998888", "ola", "doctor_alert")
