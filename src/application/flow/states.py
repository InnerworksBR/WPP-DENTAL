"""Estados explícitos da máquina de conversa (impl 016).

Os valores do enum são IDÊNTICOS às strings de `stage` já persistidas hoje em
`ConversationState` (ex.: "idle", "awaiting_name_for_slot_confirmation"). Isso torna a FSM um
drop-in sobre o estado existente — sem migração de dados e mantendo os testes de estado válidos.
"""

from __future__ import annotations

from enum import Enum


class FlowState(str, Enum):
    """Estágios explícitos do atendimento. Valores == `ConversationState.stage` atuais."""

    IDLE = "idle"
    # Coleta de dados antes de confirmar um horário escolhido
    AWAITING_NAME = "awaiting_name_for_slot_confirmation"
    AWAITING_PLAN = "awaiting_plan_for_slot_confirmation"
    # Confirmações
    AWAITING_CANCEL_CONFIRMATION = "awaiting_cancel_confirmation"
    AWAITING_APPOINTMENT_CONFIRMATION = "awaiting_appointment_confirmation"

    @classmethod
    def from_stage(cls, stage: str) -> "FlowState":
        """Resolve o `stage` persistido para um FlowState; desconhecido => IDLE (defensivo)."""
        try:
            return cls(stage)
        except ValueError:
            return cls.IDLE

    @property
    def is_awaiting(self) -> bool:
        """True para estágios que aguardam um dado específico do paciente (sujeitos a TTL)."""
        return self.value.startswith("awaiting_")
