"""Servicos reutilizaveis para o assistente Rasa CALM."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .patient_service import PatientService
from ...infrastructure.config.config_service import ConfigService
from ...infrastructure.integrations.alert_service import AlertService


class RasaContextService:
    """Concentra contexto estatico e validacoes operacionais usadas pelo Rasa."""

    def __init__(self) -> None:
        self.config = ConfigService()
        self.alerts = AlertService()
        self.patients = PatientService()

    @staticmethod
    def _normalize(text: str) -> str:
        normalized = unicodedata.normalize("NFKD", text or "")
        normalized = normalized.encode("ascii", "ignore").decode("ascii").lower()
        return re.sub(r"\s+", " ", normalized).strip()

    @staticmethod
    def _format_series(values: list[str]) -> str:
        clean_values = [value.strip() for value in values if value and value.strip()]
        if not clean_values:
            return ""
        if len(clean_values) == 1:
            return clean_values[0]
        if len(clean_values) == 2:
            return f"{clean_values[0]} e {clean_values[1]}"
        return ", ".join(clean_values[:-1]) + f" e {clean_values[-1]}"

    def get_clinic_context(self, phone: str, fallback_name: str = "") -> dict[str, Any]:
        """Retorna dados base da clinica para respostas do assistente."""
        patient_name = self.patients.resolve_name(phone, fallback_name)
        direct_plans = [
            str(plan.get("name", "")).strip()
            for plan in self.config.get_plans()
            if not plan.get("referral", False)
        ]
        referral_plans = [
            str(plan.get("name", "")).strip()
            for plan in self.config.get_referral_plans()
        ]
        referral_target = ""
        referral_plan = next(
            (plan for plan in self.config.get_referral_plans() if plan.get("referral_to")),
            None,
        )
        if referral_plan is not None:
            referral_target = str(referral_plan.get("referral_to", "")).strip()

        return {
            "doctor_name": self.config.get_doctor_name(),
            "clinic_address": self.config.get_doctor_address(),
            "patient_phone": phone,
            "patient_name": patient_name,
            "accepted_plans_text": self._format_series(direct_plans),
            "referral_plans_text": self._format_series(referral_plans),
            "default_referral_target": referral_target,
        }

    def check_plan_info(self, text: str) -> dict[str, Any]:
        """Resolve se a mensagem cita um convenio conhecido e como ele deve ser tratado."""
        plan = self.config.extract_plan_from_text(text)
        if plan is None:
            return {
                "plan_found": False,
                "plan_name": "",
                "plan_is_referral": False,
                "referral_target": "",
            }

        return {
            "plan_found": True,
            "plan_name": str(plan.get("name", "")).strip(),
            "plan_is_referral": bool(plan.get("referral", False)),
            "referral_target": str(plan.get("referral_to", "")).strip(),
        }

    def _match_procedure_rule(self, text: str) -> dict[str, Any] | None:
        normalized_text = self._normalize(text)
        if not normalized_text:
            return None

        for rule in self.config.get_procedure_rules():
            keywords = rule.get("keywords", [])
            if not isinstance(keywords, list):
                continue

            for keyword in keywords:
                normalized_keyword = self._normalize(str(keyword))
                if normalized_keyword and normalized_keyword in normalized_text:
                    return rule
        return None

    def check_procedure_policy(self, text: str) -> dict[str, Any]:
        """Resolve regras operacionais de procedimentos cobrindo plano e restricoes."""
        rule = self._match_procedure_rule(text)
        plan = self.config.extract_plan_from_text(text)

        if rule is None:
            return {
                "procedure_found": False,
                "procedure_key": "",
                "procedure_label": "",
                "procedure_status": "",
                "procedure_allowed_plans_text": "",
                "procedure_requires_card_photo": False,
                "matched_plan_name": plan["name"] if plan else "",
            }

        allowed_plans = [str(name).strip() for name in rule.get("allowed_plans", []) if str(name).strip()]
        allowed_plans_text = self._format_series(allowed_plans)
        matched_plan_name = str(plan.get("name", "")).strip() if plan else ""
        requires_card_photo = bool(rule.get("requires_card_photo", False))

        status = "available"
        if rule.get("not_performed", False):
            status = "not_performed"
        elif allowed_plans == ["Particular"]:
            status = "only_particular"
        elif requires_card_photo:
            if matched_plan_name:
                status = (
                    "allowed_with_card_photo"
                    if matched_plan_name in allowed_plans
                    else "plan_not_allowed"
                )
            else:
                status = "allowed_plans_with_card_photo"
        elif allowed_plans:
            if matched_plan_name:
                status = "allowed" if matched_plan_name in allowed_plans else "plan_not_allowed"
            else:
                status = "allowed_plans_only"

        return {
            "procedure_found": True,
            "procedure_key": str(rule.get("key", "")).strip(),
            "procedure_label": str(rule.get("label", "")).strip(),
            "procedure_status": status,
            "procedure_allowed_plans_text": allowed_plans_text,
            "procedure_requires_card_photo": requires_card_photo,
            "matched_plan_name": matched_plan_name,
        }

    def send_referral(
        self,
        *,
        patient_phone: str,
        patient_name: str,
        consultation_reason: str,
        referral_to: str,
    ) -> bool:
        """Dispara o encaminhamento objetivo para a doutora."""
        resolved_name = (patient_name or self.patients.resolve_name(patient_phone)).strip()
        final_name = resolved_name or "Nao informado"

        if resolved_name:
            self.patients.upsert(patient_phone, resolved_name)

        sent = self.alerts.send_referral_alert(
            patient_name=final_name,
            patient_phone=patient_phone,
            consultation_reason=consultation_reason or "Nao informado",
            referral_to=referral_to or "profissional parceira",
        )
        if sent and resolved_name:
            self.patients.save_interaction(
                patient_phone,
                "referral",
                f"Encaminhamento para {referral_to or 'profissional parceira'}: {consultation_reason or 'Nao informado'}",
            )
        return sent
