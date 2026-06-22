"""Adapter de transporte para a Evolution API v2.

Concentra TODAS as esquisitices do provedor: resolução de telefone real vs ``@lid``, formatos
de payload (data como dict único ou lista de ``messages``), ``conversation`` vs
``extendedTextMessage``, e o envio via ``/message/sendText``. O parsing foi movido do ``app.py``
sem reescrever a lógica (preservação de comportamento — impl 014).
"""

from __future__ import annotations

from typing import Any

from .gateway import InboundMessage, MessagingGateway
from ..whatsapp_service import WhatsAppService
from ....domain.policies.phone_service import normalize_conversation_phone


def _is_lid_jid(value: str) -> bool:
    return str(value or "").strip().lower().endswith("@lid")


def _is_whatsapp_jid(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized.endswith("@s.whatsapp.net") or normalized.endswith("@c.us")


def _get_nested_string(source: dict[str, Any], path: tuple[str, ...]) -> str:
    current: Any = source
    for key in path:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return current.strip() if isinstance(current, str) else ""


class EvolutionAdapter(MessagingGateway):
    """Implementa o contrato de transporte para a Evolution API v2."""

    def parse_inbound(self, payload: dict) -> "InboundMessage | None":
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        return self._extract_message_data(data)

    # ── Parsing (movido de app.py, lógica preservada) ──────────────────────────

    def _extract_message_data(self, data: "dict[str, Any] | list[Any]") -> "InboundMessage | None":
        if isinstance(data, dict) and "key" in data and "message" in data:
            return self._build_message_data(data)

        parent_data: dict[str, Any] = {}
        if isinstance(data, dict):
            parent_data = {key: value for key, value in data.items() if key != "messages"}

        messages = data if isinstance(data, list) else data.get("messages", [])
        if isinstance(messages, list):
            for message in messages:
                if isinstance(message, dict):
                    extracted = self._build_message_data({**parent_data, **message})
                    if extracted is not None:
                        return extracted

        return None

    def _resolve_message_phone(self, message_wrapper: dict[str, Any]) -> str:
        """Resolve o telefone real, evitando usar LID como destinatário quando possível."""
        key = message_wrapper.get("key", {})
        remote_jid = key.get("remoteJid", "") if isinstance(key, dict) else ""

        candidate_paths = (
            ("key", "remoteJid"),
            ("key", "participant"),
            ("key", "participantJid"),
            ("participant",),
            ("participantJid",),
            ("sender",),
            ("senderJid",),
            ("from",),
            ("remoteJid",),
            ("contact", "id"),
            ("contact", "jid"),
            ("contact", "remoteJid"),
        )
        for path in candidate_paths:
            candidate = _get_nested_string(message_wrapper, path)
            if _is_whatsapp_jid(candidate):
                return normalize_conversation_phone(candidate)

        if _is_lid_jid(remote_jid):
            local_part = remote_jid.split("@", 1)[0].strip()
            return f"{local_part}@lid" if local_part else ""

        return normalize_conversation_phone(remote_jid)

    def _build_message_data(self, message_wrapper: dict[str, Any]) -> "InboundMessage | None":
        key = message_wrapper.get("key", {})
        phone = self._resolve_message_phone(message_wrapper)

        message = message_wrapper.get("message", {})
        text = (
            message.get("conversation")
            or message.get("extendedTextMessage", {}).get("text")
            or ""
        )
        if not text:
            return None

        return InboundMessage(
            phone=phone,
            text=text,
            contact_name=message_wrapper.get("pushName", ""),
            message_id=key.get("id", "") if isinstance(key, dict) else "",
            from_me=bool(key.get("fromMe", False)) if isinstance(key, dict) else False,
        )

    # ── Envio (delega ao WhatsAppService existente) ────────────────────────────

    async def send_text(self, phone: str, message: str, kind: str = "bot") -> bool:
        return await WhatsAppService().send_message(phone, message, kind=kind)

    def send_text_sync(self, phone: str, message: str, kind: str = "bot") -> bool:
        return WhatsAppService().send_message_sync(phone, message, kind=kind)
