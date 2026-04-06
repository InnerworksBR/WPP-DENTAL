"""Controle de handoff manual quando a dentista assume a conversa."""

from __future__ import annotations

from datetime import datetime, timedelta

from .conversation_state_service import ConversationState, ConversationStateService


class HandoffService:
    """Ativa uma janela de silencio do agente apos interacao manual da dentista."""

    STAGE = "handoff_active"
    WINDOW_MINUTES = 30
    METADATA_UNTIL_KEY = "handoff_until_utc"

    @staticmethod
    def _parse_datetime(value: str) -> datetime | None:
        raw_value = (value or "").strip()
        if not raw_value:
            return None
        try:
            parsed = datetime.fromisoformat(raw_value)
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            return parsed.astimezone().replace(tzinfo=None)
        return parsed

    @classmethod
    def activate(cls, phone: str, duration_minutes: int | None = None) -> datetime:
        """Ativa o handoff manual para o telefone informado."""
        expires_at = datetime.utcnow() + timedelta(minutes=duration_minutes or cls.WINDOW_MINUTES)
        ConversationStateService.save(
            phone,
            ConversationState(
                stage=cls.STAGE,
                metadata={
                    cls.METADATA_UNTIL_KEY: expires_at.replace(microsecond=0).isoformat(),
                },
            ),
        )
        return expires_at

    @classmethod
    def get_expires_at(cls, phone: str) -> datetime | None:
        """Retorna quando o handoff manual expira para o telefone."""
        state = ConversationStateService.get(phone)
        if state.stage != cls.STAGE:
            return None
        expires_at = cls._parse_datetime(state.metadata.get(cls.METADATA_UNTIL_KEY, ""))
        if expires_at is None:
            ConversationStateService.clear(phone)
        return expires_at

    @classmethod
    def is_active(cls, phone: str) -> bool:
        """Indica se o handoff manual ainda esta ativo."""
        expires_at = cls.get_expires_at(phone)
        if expires_at is None:
            return False
        if datetime.utcnow() < expires_at:
            return True
        ConversationStateService.clear(phone)
        return False
