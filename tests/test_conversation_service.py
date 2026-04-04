"""Testes de regressao do historico de conversa."""

import os
from pathlib import Path


class TestConversationService:
    """Valida comportamento do contexto de conversa."""

    def setup_method(self):
        self.db_path = Path("./data/test_conversation.db")
        os.environ["DATABASE_PATH"] = str(self.db_path)

        from src.infrastructure.persistence.connection import close_db

        close_db()

    def teardown_method(self):
        from src.infrastructure.persistence.connection import close_db

        close_db()
        self.db_path.unlink(missing_ok=True)

    def test_first_message_has_no_recent_history(self):
        from src.infrastructure.persistence.connection import init_db
        from src.application.services.conversation_service import ConversationService

        init_db()

        assert ConversationService.has_recent_history("5511999999999") is False

    def test_get_history_returns_most_recent_messages_in_order(self):
        from src.infrastructure.persistence.connection import get_db, init_db
        from src.application.services.conversation_service import ConversationService

        init_db()
        db = get_db()
        phone = "5511888888888"

        for minute in range(25):
            db.execute(
                "INSERT INTO conversation_history (phone, role, content, created_at) "
                "VALUES (?, 'patient', ?, datetime('now', ?))",
                (phone, f"msg-{minute}", f"-{59 - minute} minutes"),
            )
        db.commit()

        history = ConversationService.get_history(phone, limit=20)

        assert len(history) == 20
        assert history[0]["content"] == "msg-5"
        assert history[-1]["content"] == "msg-24"

    def test_reset_context_if_finished_clears_terminal_history(self):
        from src.infrastructure.persistence.connection import init_db
        from src.application.services.conversation_service import ConversationService

        init_db()
        phone = "5511999999999"
        ConversationService.add_message(phone, "patient", "Gostaria de cancelar")
        ConversationService.add_message(
            phone,
            "assistant",
            "Sua consulta foi cancelada com sucesso. Se precisar de mais alguma coisa, estou a disposicao.",
        )

        cleared = ConversationService.reset_context_if_finished(phone)

        assert cleared is True
        assert ConversationService.get_history(phone) == []
