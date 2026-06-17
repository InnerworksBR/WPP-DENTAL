"""Operacoes de leitura e escrita de pacientes e interacoes."""

from __future__ import annotations

import unicodedata
from typing import Any

from ...domain.policies.phone_service import (
    build_phone_search_term,
    canonical_phone,
    normalize_internal_phone,
    phones_match,
)
from ...infrastructure.persistence.connection import get_db


def _normalize_name(name: str) -> str:
    """Normaliza nome para comparacao (sem acento, minusculo, espacos colapsados)."""
    normalized = unicodedata.normalize("NFKD", name or "")
    normalized = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(normalized.split())


class PatientService:
    """Centraliza a persistencia de pacientes."""

    @staticmethod
    def find_by_phone(phone: str) -> dict[str, Any] | None:
        """PH-02: busca por igualdade canonica; fallback em memoria por phones_match."""
        canon = canonical_phone(phone)
        db = get_db()

        if canon:
            row = db.execute(
                "SELECT id, name, phone, plan FROM patients WHERE phone = ? LIMIT 1",
                (canon,),
            ).fetchone()
            if row:
                return {
                    "id": int(row["id"]),
                    "name": str(row["name"] or "").strip(),
                    "phone": str(row["phone"] or "").strip(),
                    "plan": str(row["plan"] or "").strip(),
                }

        # Fallback: comparar em memoria por phones_match (legados ainda nao normalizados)
        rows = db.execute(
            "SELECT id, name, phone, plan FROM patients ORDER BY id DESC"
        ).fetchall()
        for row in rows:
            if phones_match(row["phone"], phone):
                return {
                    "id": int(row["id"]),
                    "name": str(row["name"] or "").strip(),
                    "phone": str(row["phone"] or "").strip(),
                    "plan": str(row["plan"] or "").strip(),
                }
        return None

    @staticmethod
    def find_by_name(name: str) -> dict[str, Any] | None:
        """013-C: retorna o cadastro cujo nome normalizado bate, somente se houver UM
        unico match — evita lembrar/cancelar o paciente errado em caso de homonimo."""
        target = _normalize_name(name)
        if not target or len(target) < 3:
            return None
        db = get_db()
        rows = db.execute("SELECT id, name, phone, plan FROM patients").fetchall()
        matches = [row for row in rows if _normalize_name(row["name"]) == target]
        if len(matches) != 1:
            return None
        row = matches[0]
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
        """PA-01: nao-destrutivo — preserva nome valido e plano existentes."""
        canon = canonical_phone(phone) or normalize_internal_phone(phone)
        patient_name = (name or "").strip()
        plan_name = (plan or "").strip() or None
        db = get_db()

        existing = PatientService.find_by_phone(phone)

        if existing:
            ex_name = existing["name"]
            ex_plan = existing["plan"] or None
            is_placeholder = (
                not patient_name
                or patient_name.replace("+", "").isdigit()
                or len(patient_name) < 3
            )
            final_name = ex_name if (ex_name and is_placeholder) else (patient_name or ex_name)
            final_plan = plan_name if plan_name is not None else ex_plan
            db.execute(
                "UPDATE patients SET phone = ?, name = ?, plan = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (canon, final_name, final_plan, existing["id"]),
            )
        else:
            db.execute(
                "INSERT INTO patients (phone, name, plan) VALUES (?, ?, ?)",
                (canon, patient_name, plan_name),
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
