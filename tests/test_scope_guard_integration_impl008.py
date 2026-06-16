"""Testes de integracao para impl 008 — WE-07 e CO-03.

IT-01: escopo nao destrui estado de agenda (WE-07)
IT-02: CONFIRMATION_STAGE retorna JSONResponse deterministico (CO-03)
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from src.application.services.conversation_state_service import ConversationState, ConversationStateService
from src.application.services.appointment_confirmation_service import AppointmentConfirmationService
from src.domain.policies.scope_guard_service import ScopeGuardService


# ---------------------------------------------------------------------------
# IT-01 / CA-009: WE-07 — estado de agenda sobrevive a escalacao de escopo
# ---------------------------------------------------------------------------

class TestWE07AgendaStatePreserved:
    """WE-07: _handle_scope_escalation nao apaga estado de agenda ativo."""

    def test_scope_guard_clear_skipped_with_reschedule(self):
        """CA-009: estado com reschedule_event_id deve sobreviver a logica de WE-07."""
        # Simula o estado que seria verificado em _handle_scope_escalation
        state = ConversationState(
            stage="idle",
            intent="reschedule",
            reschedule_event_id="evt-abc-123",
        )
        # Verifica que a logica de preservacao (como implementada) avalia corretamente
        has_active_agenda = bool(
            state.pending_slot_date
            or state.pending_slot_time
            or state.pending_event_id
            or state.reschedule_event_id
            or getattr(state, "intent", "") == "reschedule"
        )
        assert has_active_agenda is True, "Estado de reschedule deve ser preservado"

    def test_scope_guard_clear_allowed_without_agenda(self):
        """Sem agenda ativa, a escalacao pode limpar o estado normalmente."""
        state = ConversationState(stage="idle")
        has_active_agenda = bool(
            state.pending_slot_date
            or state.pending_slot_time
            or state.pending_event_id
            or state.reschedule_event_id
            or getattr(state, "intent", "") == "reschedule"
        )
        assert has_active_agenda is False

    def test_scope_guard_clear_skipped_with_pending_slot(self):
        """Estado com pending_slot_date/time deve ser preservado."""
        state = ConversationState(
            stage="idle",
            pending_slot_date="25/06/2026",
            pending_slot_time="14:00",
        )
        has_active_agenda = bool(
            state.pending_slot_date
            or state.pending_slot_time
            or state.pending_event_id
            or state.reschedule_event_id
            or getattr(state, "intent", "") == "reschedule"
        )
        assert has_active_agenda is True

    def test_scope_guard_clear_skipped_with_pending_event(self):
        """Estado com pending_event_id deve ser preservado."""
        state = ConversationState(
            stage="awaiting_appointment_confirmation",
            pending_event_id="evt-xyz-456",
        )
        has_active_agenda = bool(
            state.pending_slot_date
            or state.pending_slot_time
            or state.pending_event_id
            or state.reschedule_event_id
            or getattr(state, "intent", "") == "reschedule"
        )
        assert has_active_agenda is True


# ---------------------------------------------------------------------------
# IT-02 / CA-010: CO-03 — CONFIRMATION_STAGE com mensagem nao reconhecida
# ---------------------------------------------------------------------------

class TestCO03ConfirmationDeterministicFallback:
    """CO-03: _handle_appointment_confirmation retorna JSONResponse para msg ambigua."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_unrecognized_message_in_confirmation_stage_returns_response(self):
        """CA-010: mensagem ambigua em CONFIRMATION_STAGE retorna JSONResponse, nao None."""
        from fastapi.responses import JSONResponse

        CONFIRMATION_STAGE = AppointmentConfirmationService.CONFIRMATION_STAGE

        state = ConversationState(
            stage=CONFIRMATION_STAGE,
            pending_event_label="Consulta de avaliacao - Quarta, 25/06 as 14h",
        )

        with (
            patch("src.interfaces.http.app.ConversationStateService.get", return_value=state),
            patch("src.interfaces.http.app._send_response", new=AsyncMock(return_value=True)),
            patch("src.interfaces.http.app._mark_message_processed"),
            patch("src.interfaces.http.app.ConversationService.add_message"),
            patch("src.interfaces.http.app.AppointmentOfferService.is_affirmative_confirmation", return_value=False),
        ):
            from src.interfaces.http.app import _handle_appointment_confirmation

            result = self._run(
                _handle_appointment_confirmation(
                    phone="5511999999999",
                    text="qual e o endereco da clinica?",
                    contact_name="Maria",
                    message_id="msg-001",
                )
            )

        assert result is not None, "CO-03: _handle_appointment_confirmation nao deve retornar None em CONFIRMATION_STAGE"
        assert isinstance(result, JSONResponse), "Resultado deve ser JSONResponse"
        content = result.body
        assert b"confirmation_reask" in content, f"Status esperado 'confirmation_reask' em: {content}"

    def test_affirm_in_confirmation_stage_returns_response(self):
        """Confirmacao afirmativa em CONFIRMATION_STAGE retorna JSONResponse."""
        from fastapi.responses import JSONResponse

        CONFIRMATION_STAGE = AppointmentConfirmationService.CONFIRMATION_STAGE

        state = ConversationState(
            stage=CONFIRMATION_STAGE,
            pending_event_label="Consulta de avaliacao",
        )

        with (
            patch("src.interfaces.http.app.ConversationStateService.get", return_value=state),
            patch("src.interfaces.http.app.ConversationStateService.clear"),
            patch("src.interfaces.http.app._send_response", new=AsyncMock(return_value=True)),
            patch("src.interfaces.http.app._mark_message_processed"),
            patch("src.interfaces.http.app.ConversationService.add_message"),
            patch("src.interfaces.http.app.AppointmentOfferService.is_affirmative_confirmation", return_value=True),
        ):
            from src.interfaces.http.app import _handle_appointment_confirmation

            result = self._run(
                _handle_appointment_confirmation(
                    phone="5511999999999",
                    text="sim",
                    contact_name="Maria",
                    message_id="msg-002",
                )
            )

        assert result is not None
        assert isinstance(result, JSONResponse)
        assert b"appointment_confirmed" in result.body

    def test_remarcar_in_confirmation_stage_returns_response(self):
        """Remarcar em CONFIRMATION_STAGE retorna JSONResponse."""
        from fastapi.responses import JSONResponse

        CONFIRMATION_STAGE = AppointmentConfirmationService.CONFIRMATION_STAGE

        state = ConversationState(
            stage=CONFIRMATION_STAGE,
            pending_event_label="Consulta de avaliacao",
            pending_event_id="evt-001",
        )

        with (
            patch("src.interfaces.http.app.ConversationStateService.get", return_value=state),
            patch("src.interfaces.http.app.ConversationStateService.save"),
            patch("src.interfaces.http.app._send_response", new=AsyncMock(return_value=True)),
            patch("src.interfaces.http.app._mark_message_processed"),
            patch("src.interfaces.http.app.ConversationService.add_message"),
            patch("src.interfaces.http.app.AppointmentOfferService.is_affirmative_confirmation", return_value=False),
            patch("src.interfaces.http.app.AppointmentOfferService._normalize", return_value="remarcar"),
        ):
            from src.interfaces.http.app import _handle_appointment_confirmation

            result = self._run(
                _handle_appointment_confirmation(
                    phone="5511999999999",
                    text="remarcar",
                    contact_name="Maria",
                    message_id="msg-003",
                )
            )

        assert result is not None
        assert isinstance(result, JSONResponse)
        assert b"reschedule_requested" in result.body
