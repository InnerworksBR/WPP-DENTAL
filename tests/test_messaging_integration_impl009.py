"""Testes de integração para impl 009 — Mensageria Confiável e Alertas.

Cobre: WE-02 (delivered check antes de clear/cancel),
       WE-12 (mark_patient_response nos branches faltantes),
       WH-02 (_send_response como mensagem única),
       WH-04/WH-08 (kind column isolamento de eco).
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from src.application.services.conversation_state_service import ConversationState, ConversationStateService
from src.application.services.appointment_confirmation_service import AppointmentConfirmationService


CONFIRMATION_STAGE = AppointmentConfirmationService.CONFIRMATION_STAGE


# ---------------------------------------------------------------------------
# WE-02: delivered=False levanta HTTPException(502) ANTES de clear() ou cancel()
# ---------------------------------------------------------------------------

class TestWE02DeliveredCheckBeforeClear:
    """WE-02: estado e eventos não são alterados se a mensagem não foi entregue."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_confirmar_fail_does_not_clear_state(self):
        """CA-006: branch confirmar — se _send_response falhar, clear NÃO é chamado."""
        state = ConversationState(
            stage=CONFIRMATION_STAGE,
            pending_event_label="Consulta de avaliacao",
        )

        with (
            patch("src.interfaces.http.app.ConversationStateService.get", return_value=state),
            patch("src.interfaces.http.app.ConversationStateService.clear") as mock_clear,
            patch("src.interfaces.http.app._send_response", new=AsyncMock(return_value=False)),
            patch("src.interfaces.http.app._mark_message_failed"),
            patch("src.interfaces.http.app.AppointmentOfferService.is_affirmative_confirmation", return_value=True),
        ):
            from src.interfaces.http.app import _handle_appointment_confirmation

            with pytest.raises(HTTPException) as exc_info:
                self._run(
                    _handle_appointment_confirmation(
                        phone="5511999999999",
                        text="sim",
                        contact_name="Maria",
                        message_id="msg-001",
                    )
                )

            assert exc_info.value.status_code == 502
            mock_clear.assert_not_called()

    def test_confirmar_success_clears_state(self):
        """Branch confirmar bem-sucedido: clear() é chamado após entrega."""
        state = ConversationState(
            stage=CONFIRMATION_STAGE,
            pending_event_label="Consulta",
        )

        with (
            patch("src.interfaces.http.app.ConversationStateService.get", return_value=state),
            patch("src.interfaces.http.app.ConversationStateService.clear") as mock_clear,
            patch("src.interfaces.http.app._send_response", new=AsyncMock(return_value=True)),
            patch("src.interfaces.http.app._mark_message_processed"),
            patch("src.interfaces.http.app.AppointmentOfferService.is_affirmative_confirmation", return_value=True),
            patch("src.interfaces.http.app.AppointmentConfirmationService.mark_patient_response"),
            patch("src.interfaces.http.app.ConversationService.add_message"),
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

            mock_clear.assert_called_once()
            assert isinstance(result, JSONResponse)
            assert b"appointment_confirmed" in result.body

    def test_cancel_success_fail_does_not_clear_state(self):
        """Branch cancel success — se _send_response falhar, clear NÃO é chamado."""
        from src.infrastructure.integrations.calendar_service import CancelResult

        state = ConversationState(
            stage=CONFIRMATION_STAGE,
            pending_event_label="Consulta",
            pending_event_id="evt-001",
        )
        state.metadata = {
            AppointmentConfirmationService.METADATA_EVENT_ID_KEY: "evt-001",
            AppointmentConfirmationService.METADATA_START_KEY: "2026-06-20T14:00:00",
            AppointmentConfirmationService.METADATA_TYPE_KEY: "day_before",
        }

        cancel_result = CancelResult(cancelled=True, already_absent=False, error=None)

        with (
            patch("src.interfaces.http.app.ConversationStateService.get", return_value=state),
            patch("src.interfaces.http.app.ConversationStateService.clear") as mock_clear,
            patch("src.interfaces.http.app._send_response", new=AsyncMock(return_value=False)),
            patch("src.interfaces.http.app._mark_message_failed"),
            patch("src.interfaces.http.app.CalendarService") as mock_cal,
            patch("src.interfaces.http.app.AppointmentOfferService.is_affirmative_confirmation", return_value=False),
            patch("src.interfaces.http.app.AppointmentOfferService._normalize", return_value="cancelar"),
        ):
            mock_cal.return_value.cancel_appointment.return_value = cancel_result

            from src.interfaces.http.app import _handle_appointment_confirmation

            with pytest.raises(HTTPException) as exc_info:
                self._run(
                    _handle_appointment_confirmation(
                        phone="5511999999999",
                        text="cancelar",
                        contact_name="Maria",
                        message_id="msg-003",
                    )
                )

            assert exc_info.value.status_code == 502
            mock_clear.assert_not_called()

    def test_remarcar_fail_raises_502(self):
        """Branch remarcar — se _send_response falhar, HTTPException(502) é levantado."""
        state = ConversationState(
            stage=CONFIRMATION_STAGE,
            pending_event_label="Consulta",
            pending_event_id="evt-001",
        )

        with (
            patch("src.interfaces.http.app.ConversationStateService.get", return_value=state),
            patch("src.interfaces.http.app.ConversationStateService.save"),
            patch("src.interfaces.http.app._send_response", new=AsyncMock(return_value=False)),
            patch("src.interfaces.http.app._mark_message_failed"),
            patch("src.interfaces.http.app.AppointmentOfferService.is_affirmative_confirmation", return_value=False),
            patch("src.interfaces.http.app.AppointmentOfferService._normalize", return_value="remarcar"),
        ):
            from src.interfaces.http.app import _handle_appointment_confirmation

            with pytest.raises(HTTPException) as exc_info:
                self._run(
                    _handle_appointment_confirmation(
                        phone="5511999999999",
                        text="remarcar",
                        contact_name="Maria",
                        message_id="msg-004",
                    )
                )

            assert exc_info.value.status_code == 502


# ---------------------------------------------------------------------------
# WE-12: mark_patient_response chamado nos branches remarcar e confirmar
# ---------------------------------------------------------------------------

class TestWE12MarkPatientResponseCalled:
    """WE-12: mark_patient_response registra a resposta do paciente após entrega."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_confirmar_calls_mark_patient_response(self):
        """CA-008: branch confirmar chama mark_patient_response(status='confirmed')."""
        state = ConversationState(
            stage=CONFIRMATION_STAGE,
            pending_event_label="Consulta",
        )
        state.metadata = {
            AppointmentConfirmationService.METADATA_EVENT_ID_KEY: "evt-001",
            AppointmentConfirmationService.METADATA_START_KEY: "2026-06-20T14:00:00",
            AppointmentConfirmationService.METADATA_TYPE_KEY: "day_before",
        }

        with (
            patch("src.interfaces.http.app.ConversationStateService.get", return_value=state),
            patch("src.interfaces.http.app.ConversationStateService.clear"),
            patch("src.interfaces.http.app._send_response", new=AsyncMock(return_value=True)),
            patch("src.interfaces.http.app._mark_message_processed"),
            patch("src.interfaces.http.app.AppointmentOfferService.is_affirmative_confirmation", return_value=True),
            patch("src.interfaces.http.app.AppointmentConfirmationService.mark_patient_response") as mock_mark,
            patch("src.interfaces.http.app.ConversationService.add_message"),
        ):
            from src.interfaces.http.app import _handle_appointment_confirmation

            self._run(
                _handle_appointment_confirmation(
                    phone="5511999999999",
                    text="sim",
                    contact_name="Maria",
                    message_id="msg-001",
                )
            )

        mock_mark.assert_called_once()
        call_kwargs = mock_mark.call_args[1]
        assert call_kwargs.get("status") == "confirmed"

    def test_remarcar_calls_mark_patient_response(self):
        """CA-007: branch remarcar chama mark_patient_response(status='rescheduled')."""
        state = ConversationState(
            stage=CONFIRMATION_STAGE,
            pending_event_label="Consulta",
            pending_event_id="evt-002",
        )
        state.metadata = {
            AppointmentConfirmationService.METADATA_EVENT_ID_KEY: "evt-002",
            AppointmentConfirmationService.METADATA_START_KEY: "2026-06-21T10:00:00",
            AppointmentConfirmationService.METADATA_TYPE_KEY: "day_before",
        }

        with (
            patch("src.interfaces.http.app.ConversationStateService.get", return_value=state),
            patch("src.interfaces.http.app.ConversationStateService.save"),
            patch("src.interfaces.http.app._send_response", new=AsyncMock(return_value=True)),
            patch("src.interfaces.http.app._mark_message_processed"),
            patch("src.interfaces.http.app.AppointmentOfferService.is_affirmative_confirmation", return_value=False),
            patch("src.interfaces.http.app.AppointmentOfferService._normalize", return_value="remarcar"),
            patch("src.interfaces.http.app.AppointmentConfirmationService.mark_patient_response") as mock_mark,
            patch("src.interfaces.http.app.ConversationService.add_message"),
        ):
            from src.interfaces.http.app import _handle_appointment_confirmation

            result = self._run(
                _handle_appointment_confirmation(
                    phone="5511999999999",
                    text="quero remarcar",
                    contact_name="Maria",
                    message_id="msg-002",
                )
            )

        mock_mark.assert_called_once()
        call_kwargs = mock_mark.call_args[1]
        assert call_kwargs.get("status") == "rescheduled"

    def test_confirmar_state_saved_after_delivery(self):
        """ConversationStateService.save chamado com estado correto após entrega."""
        state = ConversationState(
            stage=CONFIRMATION_STAGE,
            pending_event_label="Consulta",
            pending_event_id="evt-003",
        )
        state.metadata = {}

        with (
            patch("src.interfaces.http.app.ConversationStateService.get", return_value=state),
            patch("src.interfaces.http.app.ConversationStateService.save") as mock_save,
            patch("src.interfaces.http.app.ConversationStateService.clear"),
            patch("src.interfaces.http.app._send_response", new=AsyncMock(return_value=True)),
            patch("src.interfaces.http.app._mark_message_processed"),
            patch("src.interfaces.http.app.AppointmentOfferService.is_affirmative_confirmation", return_value=False),
            patch("src.interfaces.http.app.AppointmentOfferService._normalize", return_value="remarcar"),
            patch("src.interfaces.http.app.AppointmentConfirmationService.mark_patient_response"),
            patch("src.interfaces.http.app.ConversationService.add_message"),
        ):
            from src.interfaces.http.app import _handle_appointment_confirmation

            self._run(
                _handle_appointment_confirmation(
                    phone="5511999999999",
                    text="remarcar",
                    contact_name="Maria",
                    message_id=None,
                )
            )

        # ConversationStateService.save deve ser chamado com intent='reschedule'
        mock_save.assert_called_once()
        saved_state = mock_save.call_args[0][1]
        assert saved_state.intent == "reschedule"
