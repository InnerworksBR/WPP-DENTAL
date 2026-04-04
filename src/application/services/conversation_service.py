"""Servico de gerenciamento de historico de conversa por telefone."""

import logging
import re
import unicodedata
from datetime import datetime, timedelta

from ...infrastructure.persistence.connection import get_db

logger = logging.getLogger(__name__)

CONVERSATION_TIMEOUT_MINUTES = 60


class ConversationService:
    """Gerencia o historico de conversa para manter contexto entre mensagens."""

    _TERMINAL_ASSISTANT_PATTERNS = (
        "consulta agendada com sucesso",
        "agendamento confirmado",
        "cancelada com sucesso",
        "consulta cancelada com sucesso",
        "se precisar de mais alguma coisa",
        "caso precise remarcar ou cancelar",
        "estou a disposicao",
        "posso ajudar com mais alguma coisa",
        "vou encaminhar para a",
        "entrara em contato com voce em breve",
        "sera notificada e entrara em contato",
    )

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Normaliza texto para comparacoes robustas."""
        normalized = unicodedata.normalize("NFKD", text or "")
        normalized = normalized.encode("ascii", "ignore").decode("ascii").lower()
        return re.sub(r"\s+", " ", normalized).strip()

    @staticmethod
    def add_message(phone: str, role: str, content: str) -> None:
        """Adiciona uma mensagem ao historico de conversa."""
        db = get_db()
        db.execute(
            "INSERT INTO conversation_history (phone, role, content) VALUES (?, ?, ?)",
            (phone, role, content),
        )
        db.commit()

    @staticmethod
    def get_history(phone: str, limit: int = 20) -> list[dict]:
        """
        Retorna as mensagens mais recentes da conversa.

        O retorno preserva a ordem cronologica original.
        """
        db = get_db()
        cutoff = datetime.utcnow() - timedelta(minutes=CONVERSATION_TIMEOUT_MINUTES)
        cursor = db.execute(
            "SELECT id, role, content, created_at FROM conversation_history "
            "WHERE phone = ? AND created_at > ? "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (phone, cutoff.strftime("%Y-%m-%d %H:%M:%S"), limit),
        )
        rows = cursor.fetchall()
        rows.reverse()
        return [{"role": row["role"], "content": row["content"]} for row in rows]

    @staticmethod
    def has_recent_history(phone: str) -> bool:
        """Indica se houve conversa recente dentro da janela de contexto."""
        db = get_db()
        cutoff = datetime.utcnow() - timedelta(minutes=CONVERSATION_TIMEOUT_MINUTES)
        cursor = db.execute(
            "SELECT 1 FROM conversation_history "
            "WHERE phone = ? AND created_at > ? "
            "LIMIT 1",
            (phone, cutoff.strftime("%Y-%m-%d %H:%M:%S")),
        )
        return cursor.fetchone() is not None

    @staticmethod
    def clear_history(phone: str) -> None:
        """Limpa o historico de conversa de um telefone."""
        db = get_db()
        db.execute("DELETE FROM conversation_history WHERE phone = ?", (phone,))
        db.commit()

    @staticmethod
    def last_message(phone: str) -> dict | None:
        """Retorna a mensagem mais recente dentro da janela de contexto."""
        history = ConversationService.get_history(phone, limit=1)
        return history[-1] if history else None

    @staticmethod
    def is_terminal_assistant_message(content: str) -> bool:
        """Indica se a resposta da assistente encerrou o atendimento anterior."""
        normalized = ConversationService._normalize_text(content)
        return any(
            pattern in normalized
            for pattern in ConversationService._TERMINAL_ASSISTANT_PATTERNS
        )

    @staticmethod
    def reset_context_if_finished(phone: str) -> bool:
        """Limpa o contexto quando o ultimo atendimento ja foi concluido."""
        last_message = ConversationService.last_message(phone)
        if not last_message or last_message.get("role") != "assistant":
            return False
        if not ConversationService.is_terminal_assistant_message(last_message.get("content", "")):
            return False
        ConversationService.clear_history(phone)
        logger.info("Contexto de conversa reiniciado para %s apos atendimento concluido", phone)
        return True

    @staticmethod
    def format_history_for_prompt(phone: str) -> str:
        """Formata o historico como texto para injetar no prompt do agente."""
        history = ConversationService.get_history(phone)
        if not history:
            return "Nenhum historico anterior. Esta e a PRIMEIRA mensagem do paciente."

        lines = []
        for msg in history:
            prefix = "PACIENTE" if msg["role"] == "patient" else "ASSISTENTE"
            lines.append(f"{prefix}: {msg['content']}")
        return "\n".join(lines)
