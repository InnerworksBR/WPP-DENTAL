"""Conexao e gerenciamento do banco de dados SQLite."""

import os
import sqlite3
import threading
from pathlib import Path

from ...domain.policies.phone_service import normalize_internal_phone

# Conexoes por thread evitam compartilhar a mesma conexao em requests paralelos.
_local = threading.local()

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS patients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    plan TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER,
    type TEXT NOT NULL,
    summary TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES patients(id)
);

CREATE TABLE IF NOT EXISTS conversation_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS conversation_state (
    phone TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS processed_messages (
    message_id TEXT PRIMARY KEY,
    phone TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'processed',
    last_error TEXT,
    processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS appointment_confirmations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    phone TEXT NOT NULL,
    patient_name TEXT,
    reminder_type TEXT NOT NULL DEFAULT 'day_before',
    appointment_start TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'sent',
    response_text TEXT,
    sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    responded_at DATETIME,
    UNIQUE(event_id, reminder_type, appointment_start)
);

CREATE INDEX IF NOT EXISTS idx_patients_phone ON patients(phone);
CREATE INDEX IF NOT EXISTS idx_interactions_patient ON interactions(patient_id);
CREATE INDEX IF NOT EXISTS idx_conversation_phone ON conversation_history(phone);
CREATE INDEX IF NOT EXISTS idx_conversation_state_updated_at ON conversation_state(updated_at);
CREATE INDEX IF NOT EXISTS idx_processed_messages_at ON processed_messages(processed_at);
CREATE INDEX IF NOT EXISTS idx_appointment_confirmations_phone ON appointment_confirmations(phone);
CREATE INDEX IF NOT EXISTS idx_appointment_confirmations_status ON appointment_confirmations(status);
"""


def _get_db_path() -> str:
    """Retorna o caminho do banco de dados a partir do .env ou padrao."""
    db_path = os.getenv("DATABASE_PATH", "./data/dental.db")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return db_path


def get_db() -> sqlite3.Connection:
    """Retorna a conexao com o banco de dados da thread atual."""
    if not hasattr(_local, "connection") or _local.connection is None:
        db_path = _get_db_path()
        _local.connection = sqlite3.connect(db_path)
        _local.connection.row_factory = sqlite3.Row
        _local.connection.execute("PRAGMA journal_mode=WAL")
        _local.connection.execute("PRAGMA foreign_keys=ON")
    return _local.connection


def _ensure_column(db: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    """Adiciona uma coluna caso ela ainda nao exista."""
    cursor = db.execute(f"PRAGMA table_info({table})")
    columns = {row["name"] for row in cursor.fetchall()}
    if column not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _normalize_patient_phone_rows(db: sqlite3.Connection) -> None:
    """Normaliza pacientes legados para o formato interno sem codigo do pais."""
    rows = db.execute(
        "SELECT id, phone, name, plan FROM patients ORDER BY id ASC"
    ).fetchall()
    grouped_by_phone: dict[str, list[sqlite3.Row]] = {}

    for row in rows:
        normalized_phone = normalize_internal_phone(row["phone"])
        if not normalized_phone:
            continue
        grouped_by_phone.setdefault(normalized_phone, []).append(row)

    for normalized_phone, group in grouped_by_phone.items():
        canonical = next((row for row in group if row["phone"] == normalized_phone), group[0])
        if canonical["phone"] != normalized_phone:
            db.execute(
                "UPDATE patients SET phone = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (normalized_phone, canonical["id"]),
            )

        canonical_plan = canonical["plan"]
        for row in group:
            if row["id"] == canonical["id"]:
                continue

            if row["plan"] and not canonical_plan:
                db.execute(
                    "UPDATE patients SET plan = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (row["plan"], canonical["id"]),
                )
                canonical_plan = row["plan"]

            db.execute(
                "UPDATE interactions SET patient_id = ? WHERE patient_id = ?",
                (canonical["id"], row["id"]),
            )
            db.execute("DELETE FROM patients WHERE id = ?", (row["id"],))


def _run_migrations(db: sqlite3.Connection) -> None:
    """Aplica migracoes leves compativeis com bancos ja existentes."""
    _ensure_column(db, "processed_messages", "status", "TEXT NOT NULL DEFAULT 'processed'")
    _ensure_column(db, "processed_messages", "last_error", "TEXT")
    _normalize_patient_phone_rows(db)


def init_db() -> None:
    """Inicializa o banco de dados criando as tabelas e migracoes."""
    db = get_db()
    db.executescript(_CREATE_TABLES)
    _run_migrations(db)
    db.commit()


def close_db() -> None:
    """Fecha a conexao com o banco de dados da thread atual."""
    if hasattr(_local, "connection") and _local.connection is not None:
        _local.connection.close()
        _local.connection = None
