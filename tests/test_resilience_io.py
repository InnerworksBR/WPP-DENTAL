"""Testes de resiliencia de persistencia e integracoes — Implementacao 001.

Cobre:
- CONNECTION: get_db aplica busy_timeout e mantem journal_mode=WAL.
- CA-03: tool de calendar com Google indisponivel retorna mensagem segura.
- WH-07: send_message_sync retorna True mesmo se OutboundMessageStore.record falhar.
"""

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


def test_get_db_has_busy_timeout_and_wal(tmp_path, monkeypatch):
    """CA-005: conexao com busy_timeout > 0 e journal_mode=wal."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    from src.infrastructure.persistence import connection

    connection.close_db()
    try:
        db = connection.get_db()
        busy = db.execute("PRAGMA busy_timeout").fetchone()[0]
        journal = db.execute("PRAGMA journal_mode").fetchone()[0]

        assert int(busy) >= 1000
        assert str(journal).lower() == "wal"
    finally:
        connection.close_db()


def test_get_available_slots_tool_returns_safe_error(monkeypatch):
    """CA-007: falha da API Google dentro da tool retorna mensagem segura padronizada."""
    from src.interfaces.tools import calendar_tool
    from src.interfaces.tools.calendar_tool import GetAvailableSlotsTool

    class BoomCalendar:
        def get_available_slots(self, dt, period):
            raise RuntimeError("HttpError 500 segredo interno do Google")

    monkeypatch.setattr(calendar_tool, "CalendarService", BoomCalendar)

    # Garante um dia util (a tool recusa fim de semana antes de bater no Calendar).
    day = datetime(2026, 7, 6)
    while day.weekday() >= 5:
        day += timedelta(days=1)

    out = GetAvailableSlotsTool()._run(date=day.strftime("%d/%m/%Y"))

    assert out.startswith("Erro:")
    assert "segredo" not in out
    assert "HttpError" not in out


def test_send_message_sync_returns_true_when_record_fails(monkeypatch):
    """CA-008: persistencia do espelho de saida falha apos POST OK -> retorno True."""
    from src.infrastructure.integrations import whatsapp_service
    from src.infrastructure.integrations.whatsapp_service import WhatsAppService

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"key": {"id": "wamid-1"}}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(whatsapp_service.httpx, "Client", FakeClient)

    def boom_record(*args, **kwargs):
        raise sqlite3.Error("database is locked")

    monkeypatch.setattr(whatsapp_service.OutboundMessageStore, "record", boom_record)

    ok = WhatsAppService().send_message_sync("5511999999999", "Oi")

    assert ok is True
