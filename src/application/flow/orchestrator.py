"""Orquestrador determinístico da conversa (impl 016).

Máquina de estados que decide a próxima ação a partir da NLU (015) e do estado. Projetado para
religamento INCREMENTAL: `handle()` devolve `handled=False` para os casos que ainda não assume,
permitindo que o webhook recaia no motor atual sem big-bang. À medida que cada transição é migrada,
o orquestrador passa a retornar `handled=True` e o caminho antigo correspondente é aposentado.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

from .states import FlowState
from ..nlu import IntentClassifier, Intent, NluContext, NluResult
from ..services.conversation_state_service import ConversationState
from ...infrastructure.config.config_service import ConfigService


@dataclass
class Effect:
    """Efeito colateral a ser aplicado pelo webhook (persistência/alerta), mantendo a FSM testável."""

    kind: str  # "upsert_patient" | "alert_doctor" | "register_interaction" | "clear_state"
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrchestratorResult:
    """Decisão do orquestrador para uma mensagem."""

    handled: bool
    reply_text: str = ""
    next_state: ConversationState | None = None
    effects: list[Effect] = field(default_factory=list)
    status: str = "deferred"
    nlu: NluResult | None = None


def _deferred(nlu: NluResult | None = None) -> OrchestratorResult:
    return OrchestratorResult(handled=False, status="deferred", nlu=nlu)


class ConversationOrchestrator:
    """Decide a ação determinística para a mensagem do paciente."""

    def __init__(self, classifier: IntentClassifier | None = None, config: ConfigService | None = None) -> None:
        self._config = config or ConfigService()
        self._classifier = classifier or IntentClassifier(config=self._config)

    # ── Contexto ───────────────────────────────────────────────────────────────

    def build_context(self, state: ConversationState) -> NluContext:
        flow = FlowState.from_stage(state.stage)
        return NluContext(
            has_pending_offer=bool(state.offered_date and state.offered_times),
            has_pending_confirmation=bool(state.pending_slot_date and state.pending_slot_time),
            offered_date=state.offered_date,
            offered_times=list(state.offered_times),
            requested_period=state.requested_period,
            awaiting_name=flow == FlowState.AWAITING_NAME,
            awaiting_plan=flow == FlowState.AWAITING_PLAN,
        )

    # ── Decisão ────────────────────────────────────────────────────────────────

    def handle(self, message: str, state: ConversationState, resolved_name: str = "") -> OrchestratorResult:
        context = self.build_context(state)
        nlu = self._classifier.classify(message, context)
        flow = FlowState.from_stage(state.stage)

        # Coleta de nome (estado AWAITING_NAME com horário pendente)
        if flow == FlowState.AWAITING_NAME and state.pending_slot_date and state.pending_slot_time:
            return self._resolve_pending_name(message, state, nlu)

        # Coleta de plano (estado AWAITING_PLAN com horário pendente)
        if flow == FlowState.AWAITING_PLAN and state.pending_slot_date and state.pending_slot_time:
            return self._resolve_pending_plan(message, state, nlu, resolved_name)

        # Fora de escopo: escalar para a doutora
        if nlu.intent == Intent.FORA_ESCOPO:
            return self._escalate(message, state, nlu)

        # Demais intenções (oferta/escolha/confirmação/cancelamento/remarcação) ainda
        # são tratadas pelo motor atual — serão migradas nas próximas tarefas.
        return _deferred(nlu)

    # ── Transições implementadas ───────────────────────────────────────────────

    def _resolve_pending_name(self, message: str, state: ConversationState, nlu: NluResult) -> OrchestratorResult:
        # nlu.entities.name já descarta placeholder/dígitos/len<3 (mesma regra do handler antigo)
        name = nlu.entities.name
        if not name:
            reply = (
                "Preciso do seu nome completo para confirmar a consulta.\n"
                "Pode me dizer seu nome, por favor?"
            )
            return OrchestratorResult(
                handled=True, reply_text=reply, next_state=state, status="awaiting_name", nlu=nlu
            )

        # Espelha _handle_pending_slot_name: estado volta a IDLE total (pending limpo); o nome é
        # persistido no cadastro (efeito), não no estado de conversa.
        plan_name = state.plan_name or None
        reply = _slot_confirmation_request(name, state.pending_slot_date, state.pending_slot_time)
        return OrchestratorResult(
            handled=True,
            reply_text=reply,
            next_state=ConversationState(),
            effects=[Effect("upsert_patient", {"name": name, "plan": plan_name})],
            status="pending_slot_name_resolved",
            nlu=nlu,
        )

    def _resolve_pending_plan(
        self, message: str, state: ConversationState, nlu: NluResult, resolved_name: str
    ) -> OrchestratorResult:
        plan = self._config.extract_plan_from_text(message)
        if plan and plan.get("referral", False):
            reply = (
                "Esse convenio e atendido por uma profissional parceira. "
                "Vou encaminhar para a equipe verificar e te orientar."
            )
            return OrchestratorResult(
                handled=True, reply_text=reply, next_state=ConversationState(),
                effects=[Effect("clear_state")], status="pending_slot_plan_referral", nlu=nlu,
            )

        plan_name = str(plan.get("name", "")).strip() if plan else ""
        if not plan_name:
            reply = (
                "Nao consegui localizar esse convenio no sistema.\n"
                "Pode conferir o nome, por favor? Se for particular, responda \"particular\"."
            )
            return OrchestratorResult(
                handled=True, reply_text=reply, next_state=state, status="pending_slot_plan_unknown", nlu=nlu
            )

        # Plano válido mas nome ainda não confiável: pedir o nome antes de confirmar (espelha o
        # ramo de _handle_pending_slot_plan que vai para awaiting_name).
        if not _is_valid_booking_name(resolved_name):
            next_state = _clone(state)
            next_state.plan_name = plan_name
            next_state.stage = FlowState.AWAITING_NAME.value
            reply = "Perfeito. Agora me informe seu nome completo para eu confirmar a consulta."
            return OrchestratorResult(
                handled=True, reply_text=reply, next_state=next_state,
                status="pending_slot_plan_awaiting_name", nlu=nlu,
            )

        next_state = _clone(state)
        next_state.plan_name = plan_name
        next_state.stage = FlowState.IDLE.value
        reply = _slot_confirmation_request(resolved_name, state.pending_slot_date, state.pending_slot_time)
        return OrchestratorResult(
            handled=True,
            reply_text=reply,
            next_state=next_state,
            effects=[Effect("upsert_patient", {"name": resolved_name, "plan": plan_name})],
            status="pending_slot_plan_resolved",
            nlu=nlu,
        )

    def _escalate(self, message: str, state: ConversationState, nlu: NluResult) -> OrchestratorResult:
        reply = self._config.get_message(
            "escalation.to_patient", doctor_name=self._config.get_doctor_name()
        ).strip()
        return OrchestratorResult(
            handled=True,
            reply_text=reply,
            next_state=_clone(state),
            effects=[
                Effect(
                    "alert_doctor",
                    {
                        "summary": "Pergunta fora do escopo de agenda.",
                        "reason": "fora_do_escopo",
                        "last_message": message,
                    },
                ),
                Effect("clear_state"),
            ],
            status="escalated",
            nlu=nlu,
        )


def _is_valid_booking_name(name: str) -> bool:
    """Espelha _is_valid_booking_name do app.py: rejeita vazio, 'paciente' e telefone."""
    clean = (name or "").strip()
    if not clean:
        return False
    if clean.lower() == "paciente":
        return False
    if clean.replace("+", "").isdigit():
        return False
    return True


def _clone(state: ConversationState) -> ConversationState:
    return dataclasses.replace(
        state,
        metadata=dict(state.metadata),
        offered_times=list(state.offered_times),
        rejected_slots=list(state.rejected_slots),
        excluded_dates=list(state.excluded_dates),
    )


def _slot_confirmation_request(patient_name: str, date_str: str, time_str: str) -> str:
    first_name = (patient_name or "").strip().split()[0] if patient_name else ""
    prefix = f"{first_name}, " if first_name else ""
    return (
        f"{prefix}separei este horario para voce 😊\n"
        f"{date_str} as {time_str}\n\n"
        "Posso confirmar sua consulta?"
    )
