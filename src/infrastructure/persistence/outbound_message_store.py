"""Persistencia leve de mensagens enviadas pelo bot via WhatsApp."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta

from ...domain.policies.phone_service import extract_digits
from .connection import get_db


class OutboundMessageStore:
    """Rastreia mensagens enviadas para diferenciar eco do webhook de resposta manual."""

    MATCH_WINDOW_MINUTES = 5
    RETENTION_HOURS = 24

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        digits = extract_digits(phone or "")
        if digits and not digits.startswith("55"):
            digits = f"55{digits}"
        return digits

    @staticmethod
    def _normalize_content(content: str) -> str:
        normalized = unicodedata.normalize("NFKD", content or "")
        normalized = normalized.encode("ascii", "ignore").decode("ascii").lower()
        return re.sub(r"\s+", " ", normalized).strip()

    @classmethod
    def _cleanup(cls) -> None:
        """Remove registros antigos que nao sao mais necessarios."""
        db = get_db()
        cutoff = datetime.utcnow() - timedelta(hours=cls.RETENTION_HOURS)
        db.execute(
            "DELETE FROM outbound_messages WHERE created_at <= ?",
            (cutoff.strftime("%Y-%m-%d %H:%M:%S"),),
        )
        db.commit()

    @classmethod
    def record(cls, phone: str, content: str) -> None:
        """Registra uma mensagem enviada pelo bot para posterior conciliacao com o webhook."""
        normalized_phone = cls._normalize_phone(phone)
        normalized_content = cls._normalize_content(content)
        if not normalized_phone or not normalized_content:
            return

        db = get_db()
        db.execute(
            "INSERT INTO outbound_messages (phone, content) VALUES (?, ?)",
            (normalized_phone, content.strip()),
        )
        db.commit()
        cls._cleanup()

    @classmethod
    def consume_recent_match(cls, phone: str, content: str) -> bool:
        """Consome um registro recente quando o webhook devolve o eco de uma mensagem do bot."""
        normalized_phone = cls._normalize_phone(phone)
        normalized_content = cls._normalize_content(content)
        if not normalized_phone or not normalized_content:
            return False

        db = get_db()
        cutoff = datetime.utcnow() - timedelta(minutes=cls.MATCH_WINDOW_MINUTES)
        rows = db.execute(
            "SELECT id, content FROM outbound_messages "
            "WHERE phone = ? AND created_at > ? "
            "ORDER BY created_at ASC, id ASC",
            (normalized_phone, cutoff.strftime("%Y-%m-%d %H:%M:%S")),
        ).fetchall()

        for row in rows:
            if cls._normalize_content(row["content"]) != normalized_content:
                continue

            db.execute("DELETE FROM outbound_messages WHERE id = ?", (row["id"],))
            db.commit()
            cls._cleanup()
            return True

        cls._cleanup()
        return False
