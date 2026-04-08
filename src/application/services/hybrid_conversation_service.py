"""Motor híbrido: LLM extrai contexto da mensagem, legacy executa toda a lógica.

O LLM é chamado uma única vez por mensagem para extrair campos estruturados
(intent, procedimento, plano, período, data, nome). O ConversationWorkflowService
recebe esses valores e roda normalmente — sem que o LLM decida nenhuma ação.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from .appointment_confirmation_service import AppointmentConfirmationService
from .conversation_state_service import ConversationState, ConversationStateService
from .conversation_workflow_service import ConversationWorkflowService
from ...infrastructure.integrations.calendar_service import SAO_PAULO_TZ

logger = logging.getLogger(__name__)

# Stages tratados deterministicamente pelo legacy — sem extração LLM.
_DETERMINISTIC_STAGES = {
    AppointmentConfirmationService.CONFIRMATION_STAGE,
    "awaiting_cancel_confirmation",
    "awaiting_referral_reason",
}


class _Extraction(BaseModel):
    """Resultado estruturado da extração LLM."""

    intent: str = Field(
        default="",
        description=(
            "Intenção principal detectada. Valores possíveis: "
            "'schedule' (agendar), 'reschedule' (remarcar), 'cancel' (cancelar), "
            "'query' (consultar próxima consulta), 'address' (endereço da clínica). "
            "Deixe vazio se não for claro."
        ),
    )
    procedure_text: str = Field(
        default="",
        description=(
            "Procedimento odontológico mencionado pelo paciente, na forma mais próxima "
            "de como aparece na mensagem (ex: 'aparelho', 'ortodontia', 'canal no molar'). "
            "Deixe vazio se nenhum procedimento for citado."
        ),
    )
    plan_name: str = Field(
        default="",
        description=(
            "Nome do convênio/plano mencionado pelo paciente (ex: 'Amil', 'OdontoPrev'). "
            "Deixe vazio se nenhum plano for citado."
        ),
    )
    period: str = Field(
        default="",
        description=(
            "Período do dia preferido pelo paciente. "
            "Valores possíveis: 'manha', 'tarde', 'noite'. "
            "Deixe vazio se não mencionado."
        ),
    )
    date: str = Field(
        default="",
        description=(
            "Data solicitada no formato DD/MM/YYYY. "
            "Resolva referências relativas como 'próxima sexta' ou 'amanhã' "
            "com base na data de hoje informada no contexto. "
            "Deixe vazio se nenhuma data for mencionada."
        ),
    )
    patient_name: str = Field(
        default="",
        description=(
            "Nome completo do paciente, se ele se identificou nesta mensagem. "
            "Deixe vazio se não for possível extrair um nome."
        ),
    )


def _build_extraction_prompt(
    patient_message: str,
    state: ConversationState,
    available_plans: str,
    available_procedures: str,
    history_text: str | None,
) -> list:
    today = datetime.now(SAO_PAULO_TZ).strftime("%d/%m/%Y (%A)")
    history_block = history_text.strip() if history_text and history_text.strip() else "(sem histórico)"

    system = (
        "Você extrai informações estruturadas de mensagens de pacientes de uma clínica odontológica.\n"
        "Retorne apenas os campos que conseguir identificar com confiança. Não invente.\n\n"
        f"Data de hoje: {today}\n"
        f"Stage atual da conversa: {state.stage or 'idle'}\n"
        f"Intent atual: {state.intent or '(nenhum)'}\n"
        f"Planos disponíveis: {available_plans}\n"
        f"Procedimentos conhecidos: {available_procedures}"
    )
    human = (
        f"Histórico recente:\n{history_block}\n\n"
        f"Mensagem do paciente: {patient_message}"
    )
    return [SystemMessage(content=system), HumanMessage(content=human)]


class HybridConversationService(ConversationWorkflowService):
    """Subclasse do legacy que injeta extração LLM nas funções de detecção/extração.

    O LLM é chamado uma vez por mensagem para extrair campos estruturados.
    Todas as ações de negócio continuam sendo executadas pelo legacy deterministicamente.
    """

    def __init__(self) -> None:
        super().__init__()
        model = os.getenv("HYBRID_OPENAI_MODEL", self.config.get_openai_model())
        self._extractor = ChatOpenAI(model=model, temperature=0).with_structured_output(_Extraction)
        self._extraction: _Extraction | None = None

    @staticmethod
    def enabled() -> bool:
        return os.getenv("CONVERSATION_ENGINE", "").strip().lower() == "hybrid"

    # ── Extração LLM ─────────────────────────────────────────────────────────

    def _llm_extract(self, patient_message: str, state: ConversationState, history_text: str | None) -> _Extraction:
        available_plans = self._format_available_plans()
        available_procedures = ", ".join(
            str(r.get("label", "")) for r in self.config.get_procedure_rules() if r.get("label")
        )
        messages = _build_extraction_prompt(
            patient_message=patient_message,
            state=state,
            available_plans=available_plans,
            available_procedures=available_procedures,
            history_text=history_text,
        )
        result = self._extractor.invoke(messages)
        if isinstance(result, _Extraction):
            return result
        if isinstance(result, dict):
            return _Extraction(**result)
        return _Extraction()

    # ── Overrides das funções de extração/detecção ───────────────────────────

    def _detect_intent(self, text: str) -> str:
        if self._extraction and self._extraction.intent:
            logger.debug("hybrid._detect_intent: LLM=%r", self._extraction.intent)
            return self._extraction.intent
        return super()._detect_intent(text)

    def _detect_procedure_rule(self, text: str) -> dict[str, Any] | None:
        if self._extraction and self._extraction.procedure_text:
            result = super()._detect_procedure_rule(self._extraction.procedure_text)
            if result is not None:
                logger.debug("hybrid._detect_procedure_rule: LLM=%r → %r", self._extraction.procedure_text, result.get("key"))
                return result
        return super()._detect_procedure_rule(text)

    def _extract_plan_name(self, text: str, current_plan: str = "") -> str:
        if self._extraction and self._extraction.plan_name:
            result = super()._extract_plan_name(self._extraction.plan_name, current_plan)
            if result and result != current_plan:
                logger.debug("hybrid._extract_plan_name: LLM=%r → %r", self._extraction.plan_name, result)
                return result
        return super()._extract_plan_name(text, current_plan)

    def _extract_period(self, text: str) -> str:
        if self._extraction and self._extraction.period:
            logger.debug("hybrid._extract_period: LLM=%r", self._extraction.period)
            return self._extraction.period
        return super()._extract_period(text)

    def _extract_date(self, text: str) -> str:
        if self._extraction and self._extraction.date:
            logger.debug("hybrid._extract_date: LLM=%r", self._extraction.date)
            return self._extraction.date
        return super()._extract_date(text)

    def _extract_name(self, message: str, contact_name: str = "") -> str:
        if self._extraction and self._extraction.patient_name:
            logger.debug("hybrid._extract_name: LLM=%r", self._extraction.patient_name)
            return self._extraction.patient_name
        return super()._extract_name(message, contact_name)

    # ── process_message ───────────────────────────────────────────────────────

    def process_message(
        self,
        patient_phone: str,
        patient_message: str,
        patient_name: str = "",
        history_text: str | None = None,
        is_first_message: bool | None = None,
    ) -> str:
        state = ConversationStateService.get(patient_phone)

        # Stages determinísticos → legacy direto, sem extração LLM.
        if state.stage in _DETERMINISTIC_STAGES:
            self._extraction = None
            return super().process_message(
                patient_phone, patient_message, patient_name, history_text, is_first_message
            )

        # Extração LLM — uma única chamada, resultado cacheado para este ciclo.
        try:
            self._extraction = self._llm_extract(patient_message, state, history_text)
            logger.info(
                "[ENGINE=hybrid] %s | extração: intent=%r procedure=%r plan=%r period=%r date=%r name=%r",
                patient_phone,
                self._extraction.intent,
                self._extraction.procedure_text,
                self._extraction.plan_name,
                self._extraction.period,
                self._extraction.date,
                self._extraction.patient_name,
            )
        except Exception as exc:
            logger.warning("[ENGINE=hybrid] Extração LLM falhou para %s: %s — usando keywords", patient_phone, exc)
            self._extraction = None

        try:
            return super().process_message(
                patient_phone, patient_message, patient_name, history_text, is_first_message
            )
        finally:
            # Garante que o cache não vaze entre chamadas.
            self._extraction = None
