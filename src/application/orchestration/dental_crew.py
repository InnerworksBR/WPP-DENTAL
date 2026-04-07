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
        preview = (patient_message or "")[:60].replace("\n", " ")

        # 1. Agente ReAct com LLM nativo (CONVERSATION_ENGINE=agent)
        if self.agent.enabled():
            logger.info("[ENGINE=agent] %s | mensagem: %s", patient_phone, preview)
            result = self.agent.process_message(
                patient_phone=patient_phone,
                patient_message=patient_message,
                patient_name=patient_name,
                history_text=history_text,
                is_first_message=is_first_message,
            )
            logger.info("[ENGINE=agent] %s | resposta: %s", patient_phone, result[:80].replace("\n", " "))
            return result

        # 2. LangGraph router + legacy (CONVERSATION_ENGINE=langgraph)
        if self.langgraph.enabled():
            logger.info("[ENGINE=langgraph] %s | mensagem: %s", patient_phone, preview)
            try:
                result = self.langgraph.process_message(
                    patient_phone=patient_phone,
                    patient_message=patient_message,
                    patient_name=patient_name,
                    history_text=history_text,
                    is_first_message=is_first_message,
                )
                logger.info("[ENGINE=langgraph] %s | resposta: %s", patient_phone, result[:80].replace("\n", " "))
                return result
            except Exception:
                if not self.langgraph.should_fallback_to_legacy():
                    raise
                logger.exception(
                    "[ENGINE=langgraph] falha para %s; retomando legacy.", patient_phone
                )

        # 3. Motor legado deterministico (fallback / CONVERSATION_ENGINE=legacy)
        logger.info("[ENGINE=legacy] %s | mensagem: %s", patient_phone, preview)
        result = self.workflow.process_message(
            patient_phone=patient_phone,
            patient_message=patient_message,
            patient_name=patient_name,
            history_text=history_text,
            is_first_message=is_first_message,
        )
        logger.info("[ENGINE=legacy] %s | resposta: %s", patient_phone, result[:80].replace("\n", " "))
        return result
