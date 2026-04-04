"""Testes do banco de dados."""

import os
import sqlite3
import pytest
from pathlib import Path


class TestDatabase:
    """Testa a camada de banco de dados."""

    def setup_method(self):
        """Setup: usa banco de dados em memória para testes."""
        os.environ["DATABASE_PATH"] = ":memory:"
        # Reset singleton
        from src.infrastructure.persistence.connection import close_db
        close_db()

    def test_init_db_creates_tables(self):
        """Verifica se as tabelas são criadas corretamente."""
        from src.infrastructure.persistence.connection import init_db, get_db, close_db

        close_db()
        # Para teste em memória, precisamos reconfigurar
        os.environ["DATABASE_PATH"] = "./data/test_dental.db"
        from src.infrastructure.persistence import connection
        connection._db_connection = None

        init_db()
        db = get_db()

        # Verifica tabela patients
        cursor = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='patients'"
        )
        assert cursor.fetchone() is not None

        # Verifica tabela interactions
        cursor = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='interactions'"
        )
        assert cursor.fetchone() is not None

        cursor = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='conversation_state'"
        )
        assert cursor.fetchone() is not None

        close_db()

        # Cleanup
        test_db = Path("./data/test_dental.db")
        if test_db.exists():
            test_db.unlink()

    def test_patient_crud(self):
        """Testa operações CRUD de pacientes."""
        from src.infrastructure.persistence.connection import init_db, get_db, close_db

        os.environ["DATABASE_PATH"] = "./data/test_dental.db"
        from src.infrastructure.persistence import connection
        connection._db_connection = None

        init_db()
        db = get_db()

        # Insert
        db.execute(
            "INSERT INTO patients (phone, name, plan) VALUES (?, ?, ?)",
            ("5511999999999", "Maria Silva", "Amil Dental")
        )
        db.commit()

        # Read
        cursor = db.execute(
            "SELECT * FROM patients WHERE phone = ?",
            ("5511999999999",)
        )
        patient = cursor.fetchone()
        assert patient is not None
        assert patient["name"] == "Maria Silva"
        assert patient["plan"] == "Amil Dental"

        # Update
        db.execute(
            "UPDATE patients SET plan = ? WHERE phone = ?",
            ("Bradesco Dental", "5511999999999")
        )
        db.commit()

        cursor = db.execute(
            "SELECT plan FROM patients WHERE phone = ?",
            ("5511999999999",)
        )
        assert cursor.fetchone()["plan"] == "Bradesco Dental"

        close_db()

        # Cleanup
        test_db = Path("./data/test_dental.db")
        if test_db.exists():
            test_db.unlink()
