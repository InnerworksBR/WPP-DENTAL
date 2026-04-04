"""Operacoes de leitura e escrita de pacientes e interacoes."""

from __future__ import annotations

from typing import Any

from ...domain.policies.phone_service import build_phone_search_term, normalize_internal_phone
from ...infrastructure.persistence.connection import get_db


class PatientService:
    """Centraliza a persistencia de pacientes."""

    @staticmethod
    def find_by_phone(phone: str) -> dict[str, Any] | None:
        search_term = build_phone_search_term(phone)
        db = get_db()
        row = db.execute(
            "SELECT id, name, phone, plan FROM patients "
            "WHERE phone LIKE ? ORDER BY id DESC LIMIT 1",
            (f"%{search_term}%",),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "name": str(row["name"] or "").strip(),
            "phone": str(row["phone"] or "").strip(),
            "plan": str(row["plan"] or "").strip(),
        }

    @staticmethod
    def resolve_name(phone: str, fallback_name: str = "") -> str:
        patient = PatientService.find_by_phone(phone)
        if patient and patient["name"]:
            return patient["name"]
        return (fallback_name or "").strip()

    @staticmethod
    def upsert(phone: str, name: str, plan: str | None = None) -> None:
        normalized_phone = normalize_internal_phone(phone)
        patient_name = (name or "").strip()
        plan_name = (plan or "").strip() or None
        search_term = build_phone_search_term(phone)
        db = get_db()

        existing = db.execute(
            "SELECT id, plan FROM patients WHERE phone LIKE ? ORDER BY id DESC LIMIT 1",
            (f"%{search_term}%",),
        ).fetchone()

        if existing:
            final_plan = plan_name if plan_name is not None else existing["plan"]
            db.execute(
                "UPDATE patients SET phone = ?, name = ?, plan = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (normalized_phone, patient_name, final_plan, existing["id"]),
            )
        else:
            db.execute(
                "INSERT INTO patients (phone, name, plan) VALUES (?, ?, ?)",
                (normalized_phone, patient_name, plan_name),
            )
        db.commit()

    @staticmethod
    def save_interaction(phone: str, interaction_type: str, summary: str) -> None:
        patient = PatientService.find_by_phone(phone)
        if patient is None:
            return

        db = get_db()
        db.execute(
            "INSERT INTO interactions (patient_id, type, summary) VALUES (?, ?, ?)",
            (patient["id"], interaction_type, summary),
        )
        db.commit()
