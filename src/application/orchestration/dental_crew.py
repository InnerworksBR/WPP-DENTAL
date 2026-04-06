"""Orquestrador principal do WPP-DENTAL sem dependencia de framework de agentes."""

from __future__ import annotations

import logging

from ..services.conversation_workflow_service import ConversationWorkflowService
from ..services.rasa_assistant_service import RasaAssistantService

logger = logging.getLogger(__name__)


class DentalCrew:
    """Mantem a interface antiga, mas delega para um workflow deterministico."""

    def __init__(self) -> None:
        self.workflow = ConversationWorkflowService()
        self.rasa = RasaAssistantService()

    def process_message(
        self,
        patient_phone: str,
        patient_message: str,
        patient_name: str = "",
        history_text: str | None = None,
        is_first_message: bool | None = None,
    ) -> str:
        if self.rasa.enabled():
            try:
                result = self.rasa.process_message(
                    patient_phone=patient_phone,
                    patient_message=patient_message,
                    patient_name=patient_name,
                    history_text=history_text,
                    is_first_message=is_first_message,
                )
                logger.info("Workflow Rasa finalizado para %s: %s", patient_phone, result)
                return result
            except Exception:
                if not self.rasa.should_fallback_to_legacy():
                    raise
                logger.exception(
                    "Falha ao usar Rasa para %s; retomando workflow legado.",
                    patient_phone,
                )

        result = self.workflow.process_message(
            patient_phone=patient_phone,
            patient_message=patient_message,
            patient_name=patient_name,
            history_text=history_text,
            is_first_message=is_first_message,
        )
        logger.info("Workflow finalizado para %s: %s", patient_phone, result)
        return result
