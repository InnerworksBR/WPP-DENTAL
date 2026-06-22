"""Classificador de intenção (impl 015).

Híbrido por robustez: as ENTIDADES de agenda vêm do extrator determinístico já testado
(`AppointmentOfferService`), e a INTENÇÃO de alto nível é resolvida deterministicamente quando
possível, recorrendo ao LLM (saída estruturada) só nos casos ambíguos. Se o LLM falhar, o
fallback determinístico mantém a função básica. A NLU não decide nada de agenda — só descreve.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from .schema import Entities, Intent, NluContext, NluResult
from ...domain.policies.appointment_offer_service import (
    AppointmentOffer,
    AppointmentOfferService,
)
from ...infrastructure.config.config_service import ConfigService

logger = logging.getLogger(__name__)

_CANCEL_TOKENS = ("cancelar", "cancela", "desmarcar", "desmarca")
_RESCHEDULE_TOKENS = ("remarcar", "reagendar", "remarca", "reagenda")
_GREETING_TOKENS = ("oi", "ola", "bom dia", "boa tarde", "boa noite", "tudo bem", "opa", "eai")
_CONSULT_TOKENS = (
    "minha consulta",
    "quando e minha",
    "que horas e minha",
    "ver minha consulta",
    "qual e minha consulta",
)
_SCHEDULE_TOKENS = (
    "agendar",
    "agenda",
    "marcar",
    "marca",
    "consulta",
    "horario",
    "atendimento",
    "avaliacao",
)

_LLM_INTENT_MAP = {
    "agendar": Intent.AGENDAR,
    "remarcar": Intent.REMARCAR,
    "cancelar": Intent.CANCELAR,
    "consultar": Intent.CONSULTAR,
    "saudacao": Intent.SAUDACAO,
    "fora_escopo": Intent.FORA_ESCOPO,
    "outro": None,
}


def _matches(normalized: str, tokens: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(token)}\b", normalized) for token in tokens)


class IntentClassifier:
    """Classifica a mensagem do paciente em intenção + entidades."""

    def __init__(self, structured_llm: Any = None, config: ConfigService | None = None) -> None:
        self._config = config or ConfigService()
        self._structured_llm = structured_llm  # injetável p/ teste; None => construção tardia
        self._llm_unavailable = False

    # ── API pública ────────────────────────────────────────────────────────────

    def classify(self, message: str, context: NluContext | None = None) -> NluResult:
        context = context or NluContext()
        constraints = AppointmentOfferService.extract_request_constraints(message)
        entities = self._build_entities(message, context, constraints)
        intent, source = self._resolve_intent(message, context, constraints, entities)
        return NluResult(intent=intent, entities=entities, source=source)

    # ── Entidades (determinísticas) ────────────────────────────────────────────

    def _build_entities(self, message: str, context: NluContext, constraints) -> Entities:
        entities = Entities(
            period=constraints.requested_period,
            date=constraints.requested_date,
            time=constraints.requested_time,
            earliest_time=constraints.earliest_time,
            weekday=constraints.requested_weekday,
            excluded_dates=list(constraints.excluded_dates),
            excluded_day_numbers=list(constraints.excluded_day_numbers),
            requested_day_number=constraints.requested_day_number,
            rejects_current_slot=constraints.rejects_current_slot,
            changes_pending_confirmation=constraints.changes_pending_confirmation,
        )

        plan = self._config.extract_plan_from_text(message)
        if plan:
            entities.plan = str(plan.get("name", "")).strip()

        if AppointmentOfferService.is_affirmative_confirmation(message):
            entities.affirmation = True
        elif constraints.rejects_current_slot:
            entities.affirmation = False

        if context.has_pending_offer and context.offered_date and context.offered_times:
            offer = AppointmentOffer(context.offered_date, list(context.offered_times))
            selected = AppointmentOfferService.resolve_selection(message, offer)
            if selected:
                entities.selected_time = selected
                try:
                    entities.selected_option = list(context.offered_times).index(selected) + 1
                except ValueError:
                    entities.selected_option = None

        if context.awaiting_name:
            name = message.strip()
            if name and not name.replace("+", "").isdigit() and len(name) >= 3:
                entities.name = name

        return entities

    # ── Intenção (determinística + LLM) ────────────────────────────────────────

    def _resolve_intent(
        self, message: str, context: NluContext, constraints, entities: Entities
    ) -> tuple[Intent, str]:
        norm = AppointmentOfferService._normalize(message)

        if context.awaiting_name:
            return Intent.INFORMAR_NOME, "deterministic"
        if context.awaiting_plan:
            return Intent.INFORMAR_PLANO, "deterministic"

        if _matches(norm, _CANCEL_TOKENS):
            return Intent.CANCELAR, "deterministic"
        if _matches(norm, _RESCHEDULE_TOKENS):
            return Intent.REMARCAR, "deterministic"

        if context.has_pending_confirmation:
            if entities.affirmation is True and not constraints.changes_pending_confirmation:
                return Intent.CONFIRMAR, "deterministic"
            if constraints.rejects_current_slot or constraints.changes_pending_confirmation:
                return Intent.RECUSAR, "deterministic"

        if context.has_pending_offer:
            if entities.selected_time and entities.selected_time in (context.offered_times or []):
                return Intent.ESCOLHER_HORARIO, "deterministic"
            if constraints.rejects_current_slot or constraints.changes_pending_confirmation:
                return Intent.RECUSAR, "deterministic"

        if entities.plan and len(norm.split()) <= 4 and not _matches(norm, _SCHEDULE_TOKENS):
            return Intent.INFORMAR_PLANO, "deterministic"

        if _matches(norm, _GREETING_TOKENS) and len(norm.split()) <= 5:
            return Intent.SAUDACAO, "deterministic"

        if _matches(norm, _CONSULT_TOKENS):
            return Intent.CONSULTAR, "deterministic"

        if _matches(norm, _SCHEDULE_TOKENS) or constraints.changes_pending_confirmation:
            return Intent.AGENDAR, "deterministic"

        llm_intent = self._llm_intent(message)
        if llm_intent is not None:
            return llm_intent, "llm"

        return Intent.AMBIGUO, "deterministic"

    # ── Camada LLM (defensiva) ─────────────────────────────────────────────────

    def _get_structured_llm(self) -> Any:
        if self._structured_llm is not None:
            return self._structured_llm
        if self._llm_unavailable:
            return None
        try:
            from langchain_openai import ChatOpenAI
            from pydantic import BaseModel, Field

            class _LlmIntent(BaseModel):
                intent: str = Field(
                    description=(
                        "Uma de: agendar, remarcar, cancelar, consultar, saudacao, "
                        "fora_escopo, outro"
                    )
                )

            llm = ChatOpenAI(
                model=os.getenv("OPENAI_MODEL", self._config.get_openai_model()),
                temperature=0,
                request_timeout=float(os.getenv("OPENAI_REQUEST_TIMEOUT", "20")),
                max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "1")),
            )
            self._structured_llm = llm.with_structured_output(_LlmIntent)
            return self._structured_llm
        except Exception as exc:  # pragma: no cover - ambiente sem SDK/credenciais
            logger.warning("[nlu] LLM indisponível para classificação: %s", exc)
            self._llm_unavailable = True
            return None

    def _llm_intent(self, message: str) -> Intent | None:
        llm = self._get_structured_llm()
        if llm is None:
            return None
        prompt = (
            "Classifique a intenção da mensagem de um paciente para a secretária de uma clínica "
            "odontológica. Categorias: agendar, remarcar, cancelar, consultar (consultar consulta "
            "existente), saudacao, fora_escopo (preço, dúvida clínica, qualquer coisa fora de "
            "agenda), outro.\n\n"
            f"Mensagem: {message}"
        )
        try:
            result = llm.invoke(prompt)
            return _LLM_INTENT_MAP.get(str(getattr(result, "intent", "")).strip().lower())
        except Exception as exc:
            logger.warning("[nlu] falha na classificação por LLM: %s", exc)
            return None
