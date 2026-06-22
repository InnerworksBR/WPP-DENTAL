"""Orquestrador determinístico da conversa (impl 016).

Máquina de estados que decide a próxima ação a partir da NLU (015) e do estado. Projetado para
religamento INCREMENTAL: `handle()` devolve `handled=False` para os casos que ainda não assume,
permitindo que o webhook recaia no motor atual sem big-bang. À medida que cada transição é migrada,
o orquestrador passa a retornar `handled=True` e o caminho antigo correspondente é aposentado.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .states import FlowState
from ..nlu import IntentClassifier, Intent, NluContext, NluResult
from ..services.appointment_confirmation_service import AppointmentConfirmationService
from ..services.conversation_state_service import ConversationState
from ..services.patient_service import PatientService
from ...domain.policies.appointment_offer_service import AppointmentOffer, AppointmentOfferService
from ...infrastructure.config.config_service import ConfigService
from ...infrastructure.integrations.calendar_service import CalendarService, SAO_PAULO_TZ

logger = logging.getLogger(__name__)

_ORGANIC_CANCEL_TOKENS = ("cancelar", "desmarcar", "cancela", "cancele")


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
    extra: dict[str, Any] = field(default_factory=dict)


def _deferred(nlu: NluResult | None = None) -> OrchestratorResult:
    return OrchestratorResult(handled=False, status="deferred", nlu=nlu)


class ConversationOrchestrator:
    """Decide a ação determinística para a mensagem do paciente."""

    def __init__(
        self,
        classifier: IntentClassifier | None = None,
        config: ConfigService | None = None,
        calendar: CalendarService | None = None,
    ) -> None:
        self._config = config or ConfigService()
        self._classifier = classifier or IntentClassifier(config=self._config)
        self._calendar = calendar

    def _calendar_service(self) -> CalendarService:
        return self._calendar or CalendarService()

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

        # Demais intenções (oferta/escolha/confirmação/remarcação) ainda
        # são tratadas pelo motor atual — serão migradas nas próximas tarefas.
        return _deferred(nlu)

    def try_slot_selection(
        self,
        message: str,
        state: ConversationState,
        phone: str,
        resolved_name: str,
        history: list[dict],
    ) -> OrchestratorResult:
        """Seleção de horário ofertado (parte SEGURA do _handle_offered_slot_selection: só consulta
        estado/histórico, não altera o calendário). Fixa o horário escolhido e pede plano/confirmação.

        Defere (`handled=False`) a CONFIRMAÇÃO afirmativa (criação/remarcação atômica) — essa fica no
        handler provado — e os casos sem oferta / nome incerto / não-seleção.
        """
        # Guarda de nome (espelha o topo do handler antigo): nome não confiável => defere.
        if not _is_valid_booking_name(resolved_name):
            return _deferred()

        # Confirmação afirmativa de um horário pendente => Branch A (criação/remarcação): handler provado.
        pending_confirmation = AppointmentOfferService.extract_latest_confirmation_request(history)
        if pending_confirmation and AppointmentOfferService.is_affirmative_confirmation(message):
            return _deferred()

        next_state = _clone(state)
        if next_state.offered_date and next_state.offered_times:
            offer = AppointmentOffer(next_state.offered_date, list(next_state.offered_times))
        else:
            offer = AppointmentOfferService.extract_latest_offer(history)
            if offer:
                next_state.offered_date = offer.date_str
                next_state.offered_times = list(offer.times)
        if offer is None:
            return _deferred()

        selected_time = AppointmentOfferService.resolve_selection(message, offer)
        if selected_time is None:
            if next_state.offered_date and next_state.offered_times and _looks_like_slot_choice(message):
                return OrchestratorResult(
                    handled=True,
                    reply_text=_build_current_offer_message(next_state),
                    next_state=next_state,
                    status="slot_selection_rejected",
                )
            return _deferred()

        if not _slot_satisfies_state_filters(offer.date_str, selected_time, next_state):
            return OrchestratorResult(
                handled=True,
                reply_text=_build_stale_confirmation_message(),
                next_state=next_state,
                status="slot_selection_filtered",
            )

        next_state.pending_slot_date = offer.date_str
        next_state.pending_slot_time = selected_time
        if not self._resolve_valid_plan_name(next_state, phone):
            next_state.stage = FlowState.AWAITING_PLAN.value
            return OrchestratorResult(
                handled=True,
                reply_text=_build_plan_request_message(),
                next_state=next_state,
                status="slot_plan_required",
                extra={"selected_time": selected_time},
            )
        next_state.stage = FlowState.IDLE.value
        return OrchestratorResult(
            handled=True,
            reply_text=_slot_confirmation_request(resolved_name, offer.date_str, selected_time),
            next_state=next_state,
            status="slot_confirmation_requested",
            extra={"selected_time": selected_time},
        )

    def try_reactive_reoffer(
        self, message: str, state: ConversationState, phone: str, history: list[dict]
    ) -> OrchestratorResult:
        """Re-oferta determinística (impl 013): quando o paciente recusa ou pede horário/dia
        específico não ofertado, busca novos horários respeitando as restrições do estado.
        Só consulta o calendário (não altera). Defere em caso de falha na busca."""
        start_date = None
        if state.requested_date:
            try:
                start_date = datetime.strptime(state.requested_date, "%d/%m/%Y").replace(tzinfo=SAO_PAULO_TZ)
            except ValueError:
                start_date = None
        if start_date is None:
            ctx = (
                AppointmentOfferService.extract_latest_offer(history)
                or AppointmentOfferService.extract_latest_confirmation_request(history)
            )
            ctx_date = getattr(ctx, "date_str", "") if ctx else ""
            if ctx_date:
                try:
                    start_date = datetime.strptime(ctx_date, "%d/%m/%Y").replace(tzinfo=SAO_PAULO_TZ)
                except ValueError:
                    start_date = None
        if start_date is None:
            start_date = datetime.now(SAO_PAULO_TZ)

        try:
            result = self._calendar_service().find_next_available_slots(
                start_date=start_date,
                period=state.requested_period or None,
                earliest_time=state.earliest_time or "",
                exclude_dates=state.excluded_dates,
                exclude_slots=state.rejected_slots,
                limit=self._config.get_suggestions_count(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[orchestrator] reoferta: falha na busca: %s", exc, exc_info=True)
            return _deferred()

        if not result:
            return OrchestratorResult(
                handled=True,
                reply_text=(
                    "Nao encontrei horario livre com essa preferencia nos proximos dias. 😕\n"
                    "Quer que eu veja outro dia ou periodo?"
                ),
                next_state=state,
                status="reoffer_none",
            )

        next_state = _clone(state)
        next_state.offered_date = result["date_str"]
        next_state.offered_times = list(result["times"])
        options = "\n".join(f"{index}. {t}" for index, t in enumerate(result["times"], 1))
        return OrchestratorResult(
            handled=True,
            reply_text=(
                f"Tenho estes horarios disponiveis em {result['date_str']}:\n{options}\n\n"
                "Qual voce prefere?"
            ),
            next_state=next_state,
            status="reactive_reoffer",
            extra={"offered_date": result["date_str"]},
        )

    def _resolve_valid_plan_name(self, state: ConversationState, phone: str) -> str:
        """Espelha _resolve_valid_plan_name do app.py: valida plano do estado/cadastro contra o
        config e fixa o nome canônico no estado. Retorna "" se não houver plano direto válido."""
        candidates = [state.plan_name]
        patient = PatientService.find_by_phone(phone)
        if patient:
            candidates.append(patient.get("plan", ""))
        for candidate in candidates:
            plan_name = str(candidate or "").strip()
            if not plan_name:
                continue
            plan = self._config.get_plan_by_name(plan_name) or self._config.find_plan_fuzzy(plan_name)
            if plan and not plan.get("referral", False):
                canonical = str(plan.get("name", plan_name)).strip()
                if state.plan_name != canonical:
                    state.plan_name = canonical
                return canonical
        return ""

    def try_cancellation(self, message: str, state: ConversationState, phone: str) -> OrchestratorResult:
        """Cancelamento orgânico determinístico (impl 005). Espelha _handle_cancellation_intent.

        Detecta intenção de cancelar consulta única, fixa o evento no estado e pede confirmação.
        Defere (`handled=False`) quando há múltiplas consultas, erro de calendário, ou a mensagem
        não é cancelamento — deixando o motor atual seguir.
        """
        if FlowState.from_stage(state.stage) != FlowState.IDLE or state.intent == "reschedule":
            return _deferred()

        normalized = AppointmentOfferService._normalize(message)
        if not any(tok in normalized for tok in _ORGANIC_CANCEL_TOKENS):
            return _deferred()
        if "remarc" in normalized or "reagend" in normalized or AppointmentOfferService.has_change_request(message):
            return _deferred()  # não sequestrar remarcação

        try:
            events = self._calendar_service().find_appointments_by_phone(phone)
        except Exception as exc:  # noqa: BLE001
            logger.error("[orchestrator] cancelamento: falha ao consultar agenda: %s", exc, exc_info=True)
            return _deferred()

        if not events:
            return OrchestratorResult(
                handled=True,
                reply_text="Nao encontrei nenhuma consulta futura no seu nome para cancelar.",
                next_state=state,
                status="cancel_no_appointment",
            )
        if len(events) > 1:
            return _deferred()  # múltiplas: deixa o motor atual desambiguar

        evt = events[0]
        evt_id = str(evt.get("id", "") or "")
        start_str = str(evt.get("start", {}).get("dateTime", "") or "")
        if not evt_id or not start_str:
            return _deferred()

        try:
            label = datetime.fromisoformat(start_str).strftime("%d/%m/%Y as %H:%M")
        except ValueError:
            label = "sua consulta"

        next_state = _clone(state)
        next_state.pending_event_id = evt_id
        next_state.pending_event_label = label
        next_state.metadata[AppointmentConfirmationService.METADATA_EVENT_ID_KEY] = evt_id
        next_state.metadata[AppointmentConfirmationService.METADATA_START_KEY] = start_str
        next_state.stage = FlowState.AWAITING_CANCEL_CONFIRMATION.value
        return OrchestratorResult(
            handled=True,
            reply_text=(
                f"Encontrei sua consulta de {label}.\n\n"
                "Voce confirma o cancelamento? Responda SIM para confirmar."
            ),
            next_state=next_state,
            status="cancel_confirmation_requested",
        )

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


# Helpers replicados do app.py (parte SEGURA da seleção). O 017 consolida a duplicação.

def _slot_satisfies_state_filters(date_str: str, time_str: str, state: ConversationState) -> bool:
    if f"{date_str} {time_str}" in state.rejected_slots:
        return False
    if date_str in state.excluded_dates:
        return False
    if state.earliest_time and time_str < state.earliest_time:
        return False
    if state.requested_weekday:
        try:
            dt = datetime.strptime(date_str, "%d/%m/%Y")
        except ValueError:
            return True
        if str(dt.weekday()) != str(state.requested_weekday):
            return False
    return True


def _looks_like_slot_choice(text: str) -> bool:
    normalized = AppointmentOfferService._normalize(text)
    if not normalized:
        return False
    if AppointmentOfferService._TIME_PATTERN.search(normalized):
        return True
    if AppointmentOfferService._FIRST_OPTION_PATTERN.search(normalized):
        return True
    if AppointmentOfferService._SECOND_OPTION_PATTERN.search(normalized):
        return True
    return False


def _build_plan_request_message() -> str:
    return (
        "Antes de confirmar, qual e o seu convenio/plano odontologico?\n"
        "Se for particular, pode responder \"particular\"."
    )


def _build_current_offer_message(state: ConversationState) -> str:
    options = "\n".join(
        f"{index}. {time_str}" for index, time_str in enumerate(state.offered_times, 1)
    )
    return (
        "Esse horario nao esta entre as opcoes que eu te passei.\n"
        f"As opcoes para {state.offered_date} sao:\n{options}\n\n"
        "Qual voce prefere?"
    )


def _build_stale_confirmation_message() -> str:
    return (
        "Esse horario anterior nao segue mais o que voce pediu.\n"
        "Vou buscar uma nova opcao respeitando sua preferencia."
    )


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
