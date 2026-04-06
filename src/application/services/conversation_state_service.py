"""Persistencia do estado estruturado das conversas."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from ...infrastructure.persistence.connection import get_db


@dataclass
class ConversationState:
    """Estado persistido do fluxo de atendimento por telefone."""

    stage: str = "idle"
    intent: str = ""
    patient_name: str = ""
    plan_name: str = ""
    requested_procedure: str = ""
    requested_reason: str = ""
    requested_period: str = ""
    requested_date: str = ""
    pending_event_id: str = ""
    pending_event_label: str = ""
    reschedule_event_id: str = ""
    reschedule_event_label: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


class ConversationStateService:
    """Le, grava e limpa o estado atual de uma conversa."""

    @staticmethod
    def get(phone: str) -> ConversationState:
        db = get_db()
        row = db.execute(
            "SELECT state_json FROM conversation_state WHERE phone = ?",
            (phone,),
        ).fetchone()
        if row is None or not row["state_json"]:
            return ConversationState()

        try:
            payload = json.loads(row["state_json"])
        except json.JSONDecodeError:
            return ConversationState()

        if not isinstance(payload, dict):
            return ConversationState()

        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            payload["metadata"] = {}

        return ConversationState(**payload)

    @staticmethod
    def save(phone: str, state: ConversationState) -> None:
        db = get_db()
        db.execute(
            "INSERT INTO conversation_state (phone, state_json, updated_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(phone) DO UPDATE SET "
            "state_json = excluded.state_json, "
            "updated_at = CURRENT_TIMESTAMP",
            (phone, json.dumps(asdict(state), ensure_ascii=True)),
        )
        db.commit()

    @staticmethod
    def clear(phone: str) -> None:
        db = get_db()
        db.execute("DELETE FROM conversation_state WHERE phone = ?", (phone,))
        db.commit()
