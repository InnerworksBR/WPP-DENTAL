"""Pacote do orquestrador de conversa (FSM determinística) — impl 016.

Exporta o orquestrador, os estados e os tipos de resultado. O orquestrador é a única fonte de
verdade das decisões de agenda; a NLU descreve e o webhook aplica os efeitos.
"""

from .orchestrator import ConversationOrchestrator, Effect, OrchestratorResult
from .states import FlowState

__all__ = ["ConversationOrchestrator", "Effect", "OrchestratorResult", "FlowState"]
