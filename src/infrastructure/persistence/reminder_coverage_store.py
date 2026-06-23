"""Persistência da cobertura do cron de lembretes (impl 019).

Registra, por execução diária, os pacientes que **não** receberam lembrete e o motivo. É a
fonte durável do relatório diário à clínica e do painel `/admin` — o objetivo é acabar com o
descarte silencioso: um lembrete pode até falhar para um caso, mas nunca em silêncio.
"""

from __future__ import annotations

import logging
from typing import Any

from .connection import get_db

logger = logging.getLogger(__name__)


class ReminderCoverageStore:
    """Persiste e consulta a cobertura (pulados/falhas) de cada execução do cron."""

    @classmethod
    def record_misses(cls, *, run_date: str, skipped_details: list[dict[str, Any]]) -> None:
        """Persiste os pacientes não contatados de uma execução.

        `skipped_details` é uma lista de dicts `{name, phone, event_id, reason, category}` onde
        `category` ∈ {'skipped', 'failed'}. Idempotente por execução: limpa as linhas anteriores
        da mesma `run_date` antes de regravar (catch-up/reexecução não duplica)."""
        if not run_date:
            return
        try:
            db = get_db()
            db.execute("DELETE FROM reminder_coverage WHERE run_date = ?", (run_date,))
            for item in skipped_details:
                db.execute(
                    "INSERT INTO reminder_coverage "
                    "(run_date, event_id, patient_name, phone, outcome, reason) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        run_date,
                        str(item.get("event_id", "") or "").strip(),
                        str(item.get("name", "") or "").strip(),
                        str(item.get("phone", "") or "").strip(),
                        str(item.get("category", "skipped") or "skipped").strip(),
                        str(item.get("reason", "") or "").strip(),
                    ),
                )
            db.commit()
            logger.info(
                "[cobertura] %d pendência(s) de lembrete persistida(s) para %s",
                len(skipped_details),
                run_date,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[cobertura] falha ao persistir cobertura: %s", exc, exc_info=True)

    @classmethod
    def get_misses(cls, *, run_date: str) -> list[dict[str, Any]]:
        """Retorna os pacientes não contatados de uma execução (para o /admin)."""
        try:
            db = get_db()
            rows = db.execute(
                "SELECT run_date, event_id, patient_name, phone, outcome, reason, created_at "
                "FROM reminder_coverage WHERE run_date = ? ORDER BY created_at",
                (run_date,),
            ).fetchall()
            return [dict(row) for row in rows]
        except Exception as exc:  # noqa: BLE001
            logger.error("[cobertura] falha ao consultar cobertura: %s", exc, exc_info=True)
            return []

    @classmethod
    def latest_run_date(cls) -> str:
        """Retorna a `run_date` mais recente registrada (ou "" se não houver)."""
        try:
            db = get_db()
            row = db.execute(
                "SELECT run_date FROM reminder_coverage ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            return str(row["run_date"]) if row else ""
        except Exception as exc:  # noqa: BLE001
            logger.error("[cobertura] falha ao consultar última execução: %s", exc, exc_info=True)
            return ""
