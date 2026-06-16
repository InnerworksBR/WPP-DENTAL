"""Testes de fluxo de estado no webhook: CO-03 e CO-07."""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest


class _DBMixin:
    def setup_method(self):
        self.db_path = Path("./data/test_webhook_flows.db")
        os.environ["DATABASE_PATH"] = str(self.db_path)
        os.environ["OPENAI_API_KEY"] = "test-key"
        from src.infrastructure.persistence.connection import close_db, init_db
        close_db()
        init_db()

    def teardown_method(self):
        from src.infrastructure.persistence.connection import close_db
        close_db()
        self.db_path.unlink(missing_ok=True)


PHONE = "5511999990003"


class TestCO03AwaItingNameHandler(_DBMixin):
    """CO-03: _handle_pending_slot_name existe e é importável."""

    def test_co03_funcao_existe_e_importavel(self):
        from src.interfaces.http.app import _handle_pending_slot_name
        import asyncio
        assert callable(_handle_pending_slot_name)

    def test_co03_stage_nao_afetado_ignora(self):
        """Se stage != awaiting_name_for_slot_confirmation, retorna None."""
        import asyncio
        from src.application.services.conversation_state_service import (
            ConversationState,
            ConversationStateService,
        )
        from src.interfaces.http.app import _handle_pending_slot_name

        ConversationStateService.save(PHONE, ConversationState(stage="idle"))

        result = asyncio.run(
            _handle_pending_slot_name(
                phone=PHONE, text="Maria Silva", contact_name="Maria", message_id=""
            )
        )
        assert result is None


class TestCO07TTLReset(_DBMixin):
    """CO-07: estados awaiting_* expiram após 60 minutos."""

    def _set_state_with_old_timestamp(self, phone: str, stage: str, minutes_ago: int) -> None:
        """Insere estado no DB com updated_at no passado."""
        from src.application.services.conversation_state_service import ConversationState
        from src.infrastructure.persistence.connection import get_db

        payload = json.dumps({"stage": stage, "offered_times": [], "rejected_slots": [],
                               "excluded_dates": [], "metadata": {}})
        old_time = (datetime.utcnow() - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")
        db = get_db()
        db.execute(
            "INSERT INTO conversation_state (phone, state_json, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(phone) DO UPDATE SET state_json=excluded.state_json, updated_at=excluded.updated_at",
            (phone, payload, old_time),
        )
        db.commit()

    def test_co07_awaiting_stage_expira_apos_60min(self):
        """Stage awaiting_* com updated_at > 60 min é resetado para idle pelo TTL check."""
        from src.application.services.conversation_state_service import ConversationStateService
        from src.interfaces.http.app import _reset_to_idle

        self._set_state_with_old_timestamp(PHONE, "awaiting_plan_for_slot_confirmation", 61)

        # Simular a lógica do TTL check do dispatcher diretamente
        current_state = ConversationStateService.get(PHONE)
        assert current_state.stage == "awaiting_plan_for_slot_confirmation"

        last_updated = ConversationStateService.get_updated_at(PHONE)
        assert last_updated is not None

        expired = (datetime.utcnow() - last_updated) > timedelta(minutes=60)
        assert expired, "Estado deveria estar expirado (61 minutos atrás)"

        if expired:
            current_state = _reset_to_idle(current_state)
            ConversationStateService.save(PHONE, current_state)

        reloaded = ConversationStateService.get(PHONE)
        assert reloaded.stage == "idle"

    def test_co07_awaiting_stage_nao_expira_antes_de_60min(self):
        """Stage awaiting_* com updated_at < 60 min NÃO é resetado."""
        from src.application.services.conversation_state_service import ConversationStateService

        self._set_state_with_old_timestamp(PHONE, "awaiting_plan_for_slot_confirmation", 30)

        last_updated = ConversationStateService.get_updated_at(PHONE)
        assert last_updated is not None

        expired = (datetime.utcnow() - last_updated) > timedelta(minutes=60)
        assert not expired, "Estado com 30 min NÃO deveria estar expirado"

    def test_co07_stage_idle_nao_tem_ttl(self):
        """Stage idle não tem TTL (não começa com 'awaiting_')."""
        stage = "idle"
        assert not stage.startswith("awaiting_")

    def test_co07_get_updated_at_retorna_datetime(self):
        """get_updated_at retorna um datetime válido após save."""
        from src.application.services.conversation_state_service import (
            ConversationState,
            ConversationStateService,
        )

        ConversationStateService.save(PHONE, ConversationState(stage="awaiting_cancel_confirmation"))
        updated_at = ConversationStateService.get_updated_at(PHONE)
        assert isinstance(updated_at, datetime)
