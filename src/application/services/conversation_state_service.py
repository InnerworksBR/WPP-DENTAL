"""Persistencia do estado estruturado das conversas."""

from __future__ import annotations

import dataclasses
import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime

from ...infrastructure.persistence.connection import get_db

logger = logging.getLogger("wpp-dental")


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
    offered_date: str = ""
    offered_times: list[str] = field(default_factory=list)
    pending_slot_date: str = ""
    pending_slot_time: str = ""
    rejected_slots: list[str] = field(default_factory=list)
    excluded_dates: list[str] = field(default_factory=list)
    requested_weekday: str = ""
    earliest_time: str = ""


class ConversationStateService:
    """Le, grava e limpa o estado atual de uma conversa."""

    @staticmethod
    def get(phone: str) -> ConversationState:
        try:
            db = get_db()
            row = db.execute(
                "SELECT state_json FROM conversation_state WHERE phone = ?",
                (phone,),
            ).fetchone()
        except sqlite3.Error as exc:
            # Erro de I/O do SQLite (lock/busy): degrada para estado padrao em vez
            # de propagar 500 pelo caminho do webhook (WE-10).
            logger.error(
                "Falha ao ler estado da conversa de %s; usando estado padrao: %s",
                phone,
                exc,
                exc_info=True,
            )
            return ConversationState()
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

        # CO-01: ignorar campos desconhecidos para sobreviver a schema drift
        valid_fields = {f.name for f in dataclasses.fields(ConversationState)}
        filtered = {k: v for k, v in payload.items() if k in valid_fields}
        state = ConversationState(**filtered)

        # CO-02: garantir que campos list nunca chegam como None
        for list_field in ("offered_times", "rejected_slots", "excluded_dates"):
            if not isinstance(getattr(state, list_field), list):
                setattr(state, list_field, [])

        # CO-02: garantir que campos str nunca chegam como None
        for f in dataclasses.fields(ConversationState):
            if isinstance(f.default, str) and getattr(state, f.name) is None:
                setattr(state, f.name, f.default)

        return state

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
    def get_updated_at(phone: str) -> datetime | None:
        """Retorna quando o estado foi atualizado pela última vez (UTC)."""
        db = get_db()
        row = db.execute(
            "SELECT updated_at FROM conversation_state WHERE phone = ?",
            (phone,),
        ).fetchone()
        if row is None or not row["updated_at"]:
            return None
        try:
            return datetime.strptime(str(row["updated_at"]), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    @staticmethod
    def clear(phone: str) -> None:
        db = get_db()
        db.execute("DELETE FROM conversation_state WHERE phone = ?", (phone,))
        db.commit()
