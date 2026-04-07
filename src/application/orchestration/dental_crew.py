"""Orquestrador principal do WPP-DENTAL sem dependencia de framework de agentes."""

from __future__ import annotations

import logging

from ..services.agent_conversation_service import AgentConversationService
from ..services.langgraph_conversation_service import LangGraphConversationService
from ..services.conversation_workflow_service import ConversationWorkflowService

logger = logging.getLogger(__name__)


class DentalCrew:
    """Delega para o engine de conversa configurado: agent > langgraph > legacy."""

    def __init__(self) -> None:
        self.agent = AgentConversationService()
        self.workflow = ConversationWorkflowService()
        self.langgraph = LangGraphConversationService()

    def process_message(
        self,
        patient_phone: str,
        patient_message: str,
        patient_name: str = "",
        history_text: str | None = None,
        is_first_message: bool | None = None,
    ) -> str:
        # 1. Agente ReAct com LLM nativo (CONVERSATION_ENGINE=agent)
        if self.agent.enabled():
            result = self.agent.process_message(
                patient_phone=patient_phone,
                patient_message=patient_message,
                patient_name=patient_name,
                history_text=history_text,
                is_first_message=is_first_message,
            )
            logger.info("Agente ReAct finalizado para %s", patient_phone)
            return result

        # 2. LangGraph router + legacy (CONVERSATION_ENGINE=langgraph)
        if self.langgraph.enabled():
            try:
                result = self.langgraph.process_message(
                    patient_phone=patient_phone,
                    patient_message=patient_message,
                    patient_name=patient_name,
                    history_text=history_text,
                    is_first_message=is_first_message,
                )
                logger.info("Workflow LangGraph finalizado para %s", patient_phone)
                return result
            except Exception:
                if not self.langgraph.should_fallback_to_legacy():
                    raise
                logger.exception(
                    "Falha ao usar LangGraph para %s; retomando workflow legado.",
                    patient_phone,
                )

        # 3. Motor legado deterministico (fallback / CONVERSATION_ENGINE=legacy)
        result = self.workflow.process_message(
            patient_phone=patient_phone,
            patient_message=patient_message,
            patient_name=patient_name,
            history_text=history_text,
            is_first_message=is_first_message,
        )
        logger.info("Workflow legado finalizado para %s", patient_phone)
        return result
