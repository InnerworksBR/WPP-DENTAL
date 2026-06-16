"""Testes unitários e de integração para impl 010 — Confirmação Proativa, Cron e Handoff.

Cobre: WE-08/CA-11 (is_affirmative word-boundary + has_change_request),
       WE-13 (handoff negation check),
       HO-02 (HandoffService.extend),
       CO-06 (dedup by phone+event_id),
       CO-05 (_try_claim_reminder_send recovers processing),
       CO-07 (no clear on expired state),
       CO-04 (run_catchup_if_missed),
       AG-07 (loop abort threshold),
       AG-10 (_convert_history DENTISTA prefix).
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

os.environ.setdefault("ENABLE_APPOINTMENT_CONFIRMATION_SCHEDULER", "0")


# ── helpers ───────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


# =============================================================================
# T-012 — Testes unitários
# =============================================================================


# ---------------------------------------------------------------------------
# WE-08/CA-11 — is_affirmative_confirmation (word-boundary)
# ---------------------------------------------------------------------------

class TestAffirmativeConfirmationWordBoundary:
    """WE-08/CA-11: tokens curtos devem exigir fronteira de palavra."""

    def setup_method(self):
        from src.domain.policies.appointment_offer_service import AppointmentOfferService
        self.svc = AppointmentOfferService

    def test_assim_does_not_trigger_sim(self):
        """'assim' nao deve ativar 'sim'."""
        assert self.svc.is_affirmative_confirmation("assim") is False

    def test_sim_standalone_triggers(self):
        """'sim' isolado retorna True."""
        assert self.svc.is_affirmative_confirmation("sim") is True

    def test_sim_with_exclamation_triggers(self):
        """'sim!' retorna True (! e fronteira de palavra)."""
        assert self.svc.is_affirmative_confirmation("sim!") is True

    def test_ok_standalone_triggers(self):
        """'ok' isolado retorna True."""
        assert self.svc.is_affirmative_confirmation("ok") is True

    def test_ok_in_word_does_not_trigger(self):
        """'okdoutora' nao deve ativar 'ok'."""
        assert self.svc.is_affirmative_confirmation("okdoutora") is False

    def test_okay_standalone_triggers(self):
        """'okay' isolado retorna True."""
        assert self.svc.is_affirmative_confirmation("okay") is True

    def test_negation_blocks_affirmative(self):
        """Presenca de 'nao' bloqueia mesmo que 'sim' esteja presente."""
        assert self.svc.is_affirmative_confirmation("nao sim") is False

    def test_confirmo_triggers(self):
        """'confirmo' retorna True."""
        assert self.svc.is_affirmative_confirmation("confirmo a consulta") is True

    def test_pode_confirmar_triggers(self):
        """'pode confirmar' retorna True."""
        assert self.svc.is_affirmative_confirmation("pode confirmar sim") is True

    def test_empty_message_returns_false(self):
        """Mensagem vazia retorna False."""
        assert self.svc.is_affirmative_confirmation("") is False


# ---------------------------------------------------------------------------
# WE-08/CA-11 — has_change_request
# ---------------------------------------------------------------------------

class TestHasChangeRequest:
    """WE-08: has_change_request detecta pedido de mudança/troca."""

    def setup_method(self):
        from src.domain.policies.appointment_offer_service import AppointmentOfferService
        self.svc = AppointmentOfferService

    def test_remarcar_triggers(self):
        assert self.svc.has_change_request("quero remarcar") is True

    def test_reagendar_triggers(self):
        assert self.svc.has_change_request("preciso reagendar") is True

    def test_outro_dia_triggers(self):
        assert self.svc.has_change_request("prefiro outro dia") is True

    def test_outra_data_triggers(self):
        assert self.svc.has_change_request("escolheria outra data") is True

    def test_trocar_triggers(self):
        assert self.svc.has_change_request("precisaria trocar o horario") is True

    def test_mudar_triggers(self):
        assert self.svc.has_change_request("gostaria de mudar") is True

    def test_simple_affirmative_no_change(self):
        assert self.svc.has_change_request("sim confirmo") is False

    def test_empty_returns_false(self):
        assert self.svc.has_change_request("") is False


# ---------------------------------------------------------------------------
# WE-13 — _response_triggers_handoff (negation check)
# ---------------------------------------------------------------------------

class TestResponseTriggersHandoff:
    """WE-13: handoff auto-ativacao so ocorre sem negacao proxima."""

    def setup_method(self):
        from src.interfaces.http.app import _response_triggers_handoff
        self.fn = _response_triggers_handoff

    def _norm(self, text: str) -> str:
        import unicodedata
        return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()

    def test_vou_encaminhar_triggers(self):
        assert self.fn(self._norm("vou encaminhar para a doutora")) is True

    def test_nao_vou_encaminhar_no_trigger(self):
        assert self.fn(self._norm("nao vou encaminhar para ninguem")) is False

    def test_entrara_em_contato_triggers(self):
        assert self.fn(self._norm("ela entrara em contato com voce em breve")) is True

    def test_nao_vai_trigger_blocks(self):
        assert self.fn(self._norm("nao vai encaminhar para ninguem")) is False

    def test_sera_notificada_triggers(self):
        assert self.fn(self._norm("a doutora sera notificada")) is True

    def test_no_marker_returns_false(self):
        assert self.fn(self._norm("pode marcar segunda as 10h")) is False

    def test_vai_conferir_triggers(self):
        assert self.fn(self._norm("ela vai conferir e te orientar")) is True

    def test_nao_vamos_encaminhar_blocks(self):
        assert self.fn(self._norm("nao vamos encaminhar para ninguem")) is False


# ---------------------------------------------------------------------------
# HO-02 — HandoffService.extend
# ---------------------------------------------------------------------------

class TestHandoffServiceExtend:
    """HO-02: extend() aumenta a janela sem reduzir e respeita o teto."""

    def setup_method(self):
        from src.application.services.handoff_service import HandoffService
        from src.application.services.conversation_state_service import (
            ConversationState,
            ConversationStateService,
        )
        self.HandoffService = HandoffService
        self.ConversationState = ConversationState
        self.ConversationStateService = ConversationStateService

    def _activate(self, phone: str, duration_minutes: int | None = None):
        return self.HandoffService.activate(phone, duration_minutes)

    def test_extend_increases_window(self):
        """extend() aumenta o prazo quando a janela atual e pequena."""
        phone = "5511900000001"
        with (
            patch.object(self.HandoffService, "_parse_datetime") as mock_parse,
            patch.object(self.ConversationStateService, "get") as mock_get,
            patch.object(self.ConversationStateService, "save") as mock_save,
        ):
            now = datetime(2026, 6, 16, 10, 0, 0)
            expires = now + timedelta(minutes=5)  # janela pequena
            state_obj = MagicMock()
            state_obj.stage = self.HandoffService.STAGE
            state_obj.metadata = {self.HandoffService.METADATA_UNTIL_KEY: expires.isoformat()}
            mock_get.return_value = state_obj
            mock_parse.return_value = expires

            with patch("src.application.services.handoff_service.datetime") as mock_dt:
                mock_dt.utcnow.return_value = now
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

                result = self.HandoffService.extend(phone)

            assert result is not None
            assert result > expires
            mock_save.assert_called_once()

    def test_extend_respects_ceiling(self):
        """extend() nao ultrapassa MAX_WINDOW_MINUTES a partir de agora."""
        phone = "5511900000002"
        with (
            patch.object(self.HandoffService, "_parse_datetime") as mock_parse,
            patch.object(self.ConversationStateService, "get") as mock_get,
            patch.object(self.ConversationStateService, "save"),
        ):
            now = datetime(2026, 6, 16, 10, 0, 0)
            expires = now + timedelta(minutes=5)
            state_obj = MagicMock()
            state_obj.stage = self.HandoffService.STAGE
            state_obj.metadata = {self.HandoffService.METADATA_UNTIL_KEY: expires.isoformat()}
            mock_get.return_value = state_obj
            mock_parse.return_value = expires

            with patch("src.application.services.handoff_service.datetime") as mock_dt:
                mock_dt.utcnow.return_value = now
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

                result = self.HandoffService.extend(phone, duration_minutes=999)

            ceiling = now + timedelta(minutes=self.HandoffService.MAX_WINDOW_MINUTES)
            assert result <= ceiling

    def test_extend_inactive_returns_none(self):
        """extend() retorna None quando handoff nao esta ativo."""
        phone = "5511900000003"
        with patch.object(self.ConversationStateService, "get") as mock_get:
            state_obj = MagicMock()
            state_obj.stage = "idle"
            mock_get.return_value = state_obj
            result = self.HandoffService.extend(phone)
        assert result is None

    def test_extend_does_not_reduce_window(self):
        """extend() com janela grande nao reduz o prazo existente."""
        phone = "5511900000004"
        with (
            patch.object(self.HandoffService, "_parse_datetime") as mock_parse,
            patch.object(self.ConversationStateService, "get") as mock_get,
            patch.object(self.ConversationStateService, "save") as mock_save,
        ):
            now = datetime(2026, 6, 16, 10, 0, 0)
            # Janela grande: 100 minutos a partir de agora
            large_expires = now + timedelta(minutes=100)
            state_obj = MagicMock()
            state_obj.stage = self.HandoffService.STAGE
            state_obj.metadata = {self.HandoffService.METADATA_UNTIL_KEY: large_expires.isoformat()}
            mock_get.return_value = state_obj
            mock_parse.return_value = large_expires

            with patch("src.application.services.handoff_service.datetime") as mock_dt:
                mock_dt.utcnow.return_value = now
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

                result = self.HandoffService.extend(phone, duration_minutes=30)

            # Retorna a janela existente, nao salva novamente
            assert result == large_expires
            mock_save.assert_not_called()


# ---------------------------------------------------------------------------
# CO-06 — _select_unique_appointments por (phone, event_id)
# ---------------------------------------------------------------------------

class TestSelectUniqueAppointmentsByPhoneEvent:
    """CO-06: dedup deve ser por (phone, event_id), nao apenas por phone."""

    def setup_method(self):
        from src.application.services.appointment_confirmation_service import AppointmentConfirmationService
        self.svc = AppointmentConfirmationService()

    def _apt(self, phone, event_id, hour=8):
        from src.infrastructure.integrations.calendar_service import SAO_PAULO_TZ
        return {
            "patient_phone": phone,
            "event_id": event_id,
            "start_time": datetime(2026, 6, 17, hour, 0, tzinfo=SAO_PAULO_TZ),
        }

    def test_same_phone_different_events_both_kept(self):
        """Mesmo telefone, event_id diferentes → ambos devem ser mantidos."""
        appointments = [
            self._apt("11999999999", "evt-A", hour=8),
            self._apt("11999999999", "evt-B", hour=14),
        ]
        result = self.svc._select_unique_appointments(appointments)
        assert len(result) == 2

    def test_same_phone_same_event_deduplicated(self):
        """Mesmo (phone, event_id) → apenas um registro."""
        appointments = [
            self._apt("11999999999", "evt-A", hour=8),
            self._apt("11999999999", "evt-A", hour=8),
        ]
        result = self.svc._select_unique_appointments(appointments)
        assert len(result) == 1

    def test_empty_event_id_skipped(self):
        """Appointment sem event_id e descartado."""
        appointments = [{"patient_phone": "11999999999", "event_id": "", "start_time": datetime(2026, 6, 17, 8, 0)}]
        result = self.svc._select_unique_appointments(appointments)
        assert len(result) == 0

    def test_different_phones_both_kept(self):
        """Phones diferentes mantidos independentemente."""
        appointments = [
            self._apt("11999999999", "evt-A"),
            self._apt("11888888888", "evt-A"),
        ]
        result = self.svc._select_unique_appointments(appointments)
        assert len(result) == 2

    def test_results_sorted_by_start_time(self):
        """Resultado ordenado por start_time."""
        appointments = [
            self._apt("11999999999", "evt-B", hour=14),
            self._apt("11888888888", "evt-A", hour=8),
        ]
        result = self.svc._select_unique_appointments(appointments)
        assert result[0]["event_id"] == "evt-A"
        assert result[1]["event_id"] == "evt-B"


# ---------------------------------------------------------------------------
# CO-05 — _try_claim_reminder_send recupera status 'processing'
# ---------------------------------------------------------------------------

class TestTryClaimReminderSendRecovery:
    """CO-05: _try_claim_reminder_send deve recuperar linhas com status 'processing'."""

    def setup_method(self):
        import tempfile
        from src.infrastructure.persistence.connection import init_db
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        os.environ["DATABASE_PATH"] = self._tmp.name
        init_db()

    def teardown_method(self):
        from src.infrastructure.persistence.connection import close_db
        close_db()
        self._tmp.close()
        try:
            os.unlink(self._tmp.name)
        except OSError:
            pass

    def test_recovers_processing_status(self):
        """Linhas com status='processing' devem ser recuperadas (retorna True)."""
        from src.application.services.appointment_confirmation_service import AppointmentConfirmationService
        from src.infrastructure.persistence.connection import get_db
        from src.infrastructure.integrations.calendar_service import SAO_PAULO_TZ

        start = datetime(2026, 6, 17, 10, 0, tzinfo=SAO_PAULO_TZ)
        serialized = AppointmentConfirmationService.serialize_appointment_start(start)

        db = get_db()
        db.execute(
            "INSERT INTO appointment_confirmations "
            "(event_id, phone, patient_name, reminder_type, appointment_start, status, sent_at) "
            "VALUES ('evt-1', '5511999999999', 'Maria', 'day_before', ?, 'processing', CURRENT_TIMESTAMP)",
            (serialized,),
        )
        db.commit()

        result = AppointmentConfirmationService._try_claim_reminder_send(
            event_id="evt-1",
            phone="5511999999999",
            patient_name="Maria",
            appointment_start=start,
        )
        assert result is True

        row = db.execute(
            "SELECT status FROM appointment_confirmations WHERE event_id='evt-1'"
        ).fetchone()
        assert row["status"] == "processing"

    def test_does_not_recover_sent_status(self):
        """Linhas com status='sent' nao devem ser re-enviadas (retorna False)."""
        from src.application.services.appointment_confirmation_service import AppointmentConfirmationService
        from src.infrastructure.persistence.connection import get_db
        from src.infrastructure.integrations.calendar_service import SAO_PAULO_TZ

        start = datetime(2026, 6, 17, 11, 0, tzinfo=SAO_PAULO_TZ)
        serialized = AppointmentConfirmationService.serialize_appointment_start(start)

        db = get_db()
        db.execute(
            "INSERT INTO appointment_confirmations "
            "(event_id, phone, patient_name, reminder_type, appointment_start, status, sent_at) "
            "VALUES ('evt-2', '5511999999999', 'Maria', 'day_before', ?, 'sent', CURRENT_TIMESTAMP)",
            (serialized,),
        )
        db.commit()

        result = AppointmentConfirmationService._try_claim_reminder_send(
            event_id="evt-2",
            phone="5511999999999",
            patient_name="Maria",
            appointment_start=start,
        )
        assert result is False


# ---------------------------------------------------------------------------
# AG-10 — _convert_history reconhece prefixo DENTISTA:
# ---------------------------------------------------------------------------

class TestConvertHistoryDentistaPrefix:
    """AG-10: prefixo DENTISTA: deve ser reconhecido no historico."""

    def setup_method(self):
        from src.application.services.clean_agent_service import _convert_history
        from langchain_core.messages import AIMessage, HumanMessage
        self.convert = _convert_history
        self.HumanMessage = HumanMessage
        self.AIMessage = AIMessage

    def test_dentista_prefix_included(self):
        """Linha DENTISTA: resulta em HumanMessage."""
        msgs = self.convert("DENTISTA: Oi Maria, pode marcar para quinta")
        assert len(msgs) == 1
        assert isinstance(msgs[0], self.HumanMessage)

    def test_dentista_content_wrapped(self):
        """Conteudo de DENTISTA: comeca com [DENTISTA]."""
        msgs = self.convert("DENTISTA: Marque para quinta")
        assert msgs[0].content.startswith("[DENTISTA]")
        assert "Marque para quinta" in msgs[0].content

    def test_dentista_missing_before_fix_would_discard(self):
        """DENTISTA: linha nao era descartada antes — verificando que agora aparece."""
        history = "PACIENTE: ola\nDENTISTA: oi\nASSISTENTE: como posso ajudar"
        msgs = self.convert(history)
        assert len(msgs) == 3

    def test_mixed_prefixes_all_recognized(self):
        """Mistura de PACIENTE, ASSISTENTE e DENTISTA."""
        history = (
            "PACIENTE: quero agendar\n"
            "ASSISTENTE: claro, qual dia?\n"
            "DENTISTA: ja agendei manualmente\n"
            "PACIENTE: obrigada"
        )
        msgs = self.convert(history)
        assert len(msgs) == 4
        roles = [type(m).__name__ for m in msgs]
        assert roles == ["HumanMessage", "AIMessage", "HumanMessage", "HumanMessage"]

    def test_dentista_empty_content_skipped(self):
        """DENTISTA: sem conteudo e ignorado."""
        msgs = self.convert("DENTISTA:   ")
        assert len(msgs) == 0


# ---------------------------------------------------------------------------
# AG-07 — seen_call_counts — aborta na 3a ocorrencia
# ---------------------------------------------------------------------------

class TestLoopAbortThreshold:
    """AG-07: loop abortado apos N+1 ocorrencias (threshold=2 → aborta na 3a)."""

    def _make_service(self):
        with (
            patch("src.application.services.clean_agent_service.ConfigService"),
            patch("src.application.services.clean_agent_service.ChatOpenAI"),
            patch("src.application.services.clean_agent_service._build_tools", return_value=[]),
        ):
            from src.application.services.clean_agent_service import CleanAgentService
            svc = CleanAgentService.__new__(CleanAgentService)
            svc._tools = []
            svc._tool_map = {}
            return svc

    def _make_response(self, call_name, call_args):
        from langchain_core.messages import AIMessage
        resp = MagicMock(spec=AIMessage)
        resp.content = ""
        resp.tool_calls = [{"name": call_name, "args": call_args, "id": "tc-001"}]
        return resp

    def test_first_and_second_occurrences_allowed(self):
        """Primeira e segunda ocorrencias da mesma chamada nao abortam."""
        from src.application.services.clean_agent_service import _LOOP_ABORT_THRESHOLD
        assert _LOOP_ABORT_THRESHOLD == 2

        svc = self._make_service()
        final_resp = MagicMock()
        final_resp.content = "resposta final"
        final_resp.tool_calls = []

        repeated_call = self._make_response("buscar_horarios_disponiveis", {"date": "17/06/2026"})
        responses = [repeated_call, repeated_call, final_resp]

        with (
            patch.object(svc, "_invoke_llm", side_effect=responses),
            patch("src.application.services.clean_agent_service.ConversationStateService.get"),
        ):
            result = svc._run_loop([], "5511999999999")

        assert "dificuldade interna" not in result.lower()
        assert result == "resposta final"

    def test_third_occurrence_aborts(self):
        """Terceira ocorrencia da mesma chamada aborta o loop."""
        svc = self._make_service()
        repeated_call = self._make_response("buscar_horarios_disponiveis", {"date": "17/06/2026"})

        with (
            patch.object(svc, "_invoke_llm", return_value=repeated_call),
            patch("src.application.services.clean_agent_service.ConversationStateService.get"),
        ):
            result = svc._run_loop([], "5511999999999")

        assert "dificuldade interna" in result.lower()


# =============================================================================
# T-013 — Testes de integração
# =============================================================================


# ---------------------------------------------------------------------------
# CO-07 — estado expirado deve ser pulado, nao apagado
# ---------------------------------------------------------------------------

class TestCO07NoClearOnExpiredState:
    """CO-07: cron nao deve apagar conversa em andamento, apenas pular."""

    def setup_method(self):
        import tempfile
        from src.infrastructure.persistence.connection import init_db
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        os.environ["DATABASE_PATH"] = self._tmp.name
        init_db()

    def teardown_method(self):
        from src.infrastructure.persistence.connection import close_db
        close_db()
        self._tmp.close()
        try:
            os.unlink(self._tmp.name)
        except OSError:
            pass

    def test_expired_state_is_skipped_not_cleared(self, monkeypatch):
        """Quando o estado e nao-idle e expirado (> 2h), o paciente e pulado sem clear()."""
        from src.application.services.appointment_confirmation_service import AppointmentConfirmationService
        from src.application.services.conversation_state_service import ConversationState, ConversationStateService
        from src.infrastructure.integrations.calendar_service import SAO_PAULO_TZ

        phone = "5511777777777"
        # Salvar estado nao-idle
        ConversationStateService.save(
            phone,
            ConversationState(stage="awaiting_cancel_confirmation"),
        )

        tomorrow = {
            "event_id": "evt-expired",
            "patient_name": "Joao",
            "patient_phone": "11777777777",
            "start_time": datetime(2026, 6, 17, 9, 0, tzinfo=SAO_PAULO_TZ),
        }

        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.find_patient_appointments_for_date",
            lambda self, date: [tomorrow],
        )

        clear_calls = []
        original_clear = ConversationStateService.clear

        def tracking_clear(p):
            clear_calls.append(p)
            return original_clear(p)

        monkeypatch.setattr(ConversationStateService, "clear", staticmethod(tracking_clear))

        # Simular updated_at > 7200s para que o estado seja considerado expirado
        long_ago = datetime.utcnow() - timedelta(seconds=7300)
        monkeypatch.setattr(
            ConversationStateService, "get_updated_at",
            staticmethod(lambda p: long_ago),
        )

        service = AppointmentConfirmationService()
        stats = _run(service.send_next_day_confirmations(
            reference_time=datetime(2026, 6, 16, 20, 0, tzinfo=SAO_PAULO_TZ)
        ))

        # Deve pular (skipped_busy) sem chamar clear()
        assert stats["skipped_busy"] >= 1
        assert phone not in clear_calls


# ---------------------------------------------------------------------------
# CO-04 — run_catchup_if_missed
# ---------------------------------------------------------------------------

class TestCO04CatchupIfMissed:
    """CO-04: run_catchup_if_missed executa confirmacoes ao reiniciar apos as 20h."""

    def setup_method(self):
        import tempfile
        from src.infrastructure.persistence.connection import init_db
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        os.environ["DATABASE_PATH"] = self._tmp.name
        init_db()

    def teardown_method(self):
        from src.infrastructure.persistence.connection import close_db
        close_db()
        self._tmp.close()
        try:
            os.unlink(self._tmp.name)
        except OSError:
            pass

    def test_catchup_returns_none_before_20h(self):
        """Antes das 20h, catchup retorna None sem enviar nada."""
        from src.application.services.appointment_confirmation_service import AppointmentConfirmationService
        from src.infrastructure.integrations.calendar_service import SAO_PAULO_TZ

        service = AppointmentConfirmationService()
        result = _run(service.run_catchup_if_missed(
            now=datetime(2026, 6, 16, 14, 0, tzinfo=SAO_PAULO_TZ)
        ))
        assert result is None

    def test_catchup_returns_none_if_already_sent(self):
        """Se ja ha registros 'sent' para amanha, catchup retorna None."""
        from src.application.services.appointment_confirmation_service import AppointmentConfirmationService
        from src.infrastructure.persistence.connection import get_db
        from src.infrastructure.integrations.calendar_service import SAO_PAULO_TZ

        db = get_db()
        tomorrow = (datetime(2026, 6, 16, 20, 0, tzinfo=SAO_PAULO_TZ) + timedelta(days=1)).date()
        db.execute(
            "INSERT INTO appointment_confirmations "
            "(event_id, phone, patient_name, reminder_type, appointment_start, status, sent_at) "
            "VALUES ('evt-done', '5511999999999', 'Maria', 'day_before', ?, 'sent', CURRENT_TIMESTAMP)",
            (f"{tomorrow.isoformat()}T09:00:00",),
        )
        db.commit()

        service = AppointmentConfirmationService()
        result = _run(service.run_catchup_if_missed(
            now=datetime(2026, 6, 16, 21, 0, tzinfo=SAO_PAULO_TZ)
        ))
        assert result is None

    def test_catchup_sends_if_no_records_after_20h(self, monkeypatch):
        """Apos as 20h sem registros, catchup envia os lembretes."""
        from src.application.services.appointment_confirmation_service import AppointmentConfirmationService
        from src.infrastructure.integrations.calendar_service import SAO_PAULO_TZ

        sent = []

        async def fake_send(self, phone, message):
            sent.append(message)
            return True

        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.find_patient_appointments_for_date",
            lambda self, date: [{
                "event_id": "evt-catch",
                "patient_name": "Ana",
                "patient_phone": "11888888888",
                "start_time": datetime(2026, 6, 17, 9, 0, tzinfo=SAO_PAULO_TZ),
            }],
        )

        service = AppointmentConfirmationService()
        result = _run(service.run_catchup_if_missed(
            now=datetime(2026, 6, 16, 21, 30, tzinfo=SAO_PAULO_TZ)
        ))
        assert result is not None
        assert result["sent"] == 1


# ---------------------------------------------------------------------------
# CO-05 — excecao por paciente nao aborta os outros
# ---------------------------------------------------------------------------

class TestCO05TryExceptPerAppointment:
    """CO-05: excecao ao processar um paciente nao deve interromper os outros."""

    def setup_method(self):
        import tempfile
        from src.infrastructure.persistence.connection import init_db
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        os.environ["DATABASE_PATH"] = self._tmp.name
        init_db()

    def teardown_method(self):
        from src.infrastructure.persistence.connection import close_db
        close_db()
        self._tmp.close()
        try:
            os.unlink(self._tmp.name)
        except OSError:
            pass

    def test_exception_per_appointment_does_not_abort_others(self, monkeypatch):
        """Se o primeiro paciente lanca excecao, o segundo ainda deve ser processado."""
        from src.application.services.appointment_confirmation_service import AppointmentConfirmationService
        from src.infrastructure.integrations.calendar_service import SAO_PAULO_TZ

        sent = []
        call_count = [0]

        async def selective_send(self, phone, message):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("simulated send failure")
            sent.append(phone)
            return True

        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            selective_send,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.find_patient_appointments_for_date",
            lambda self, date: [
                {
                    "event_id": "evt-fail",
                    "patient_name": "Joao",
                    "patient_phone": "11111111111",
                    "start_time": datetime(2026, 6, 17, 8, 0, tzinfo=SAO_PAULO_TZ),
                },
                {
                    "event_id": "evt-ok",
                    "patient_name": "Maria",
                    "patient_phone": "22222222222",
                    "start_time": datetime(2026, 6, 17, 9, 0, tzinfo=SAO_PAULO_TZ),
                },
            ],
        )

        service = AppointmentConfirmationService()
        stats = _run(service.send_next_day_confirmations(
            reference_time=datetime(2026, 6, 16, 20, 0, tzinfo=SAO_PAULO_TZ)
        ))

        # O segundo paciente foi enviado mesmo com a excecao do primeiro
        assert stats["sent"] == 1
        assert stats["failed"] == 1
        assert "5522222222222" in sent
