"""Testes do webhook principal."""

import os
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient
from src.infrastructure.integrations.calendar_service import CancelResult


def _build_payload(message_id: str = "msg-1") -> dict:
    return {
        "event": "messages.upsert",
        "data": {
            "key": {
                "id": message_id,
                "fromMe": False,
                "remoteJid": "5511999999999@s.whatsapp.net",
            },
            "pushName": "Maria",
            "message": {
                "conversation": "Oi",
            },
        },
    }


def _build_from_me_payload(message_id: str = "fromme-1", text: str = "Estou assumindo por aqui") -> dict:
    payload = _build_payload(message_id)
    payload["data"]["key"]["fromMe"] = True
    payload["data"]["message"]["conversation"] = text
    return payload


class TestMainWebhook:
    """Valida autenticacao, retries e deduplicacao."""

    def setup_method(self):
        self.db_path = Path("./data/test_webhook.db")
        os.environ["DATABASE_PATH"] = str(self.db_path)
        os.environ["WEBHOOK_API_KEY"] = "test-secret"
        os.environ.pop("EVOLUTION_WEBHOOK_API_KEY", None)
        os.environ.pop("EVOLUTION_API_KEY", None)

        from src.infrastructure.persistence.connection import close_db

        close_db()

    def teardown_method(self):
        from src.infrastructure.persistence.connection import close_db

        close_db()
        self.db_path.unlink(missing_ok=True)

    def test_root_healthcheck_returns_ok(self):
        import src.main as main

        with TestClient(main.app) as client:
            response = client.get("/")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "service": "wpp-dental"}

    def test_message_webhook_rejects_request_without_valid_auth_header(self):
        import src.main as main

        with TestClient(main.app) as client:
            response = client.post("/webhook/message", json=_build_payload())

        assert response.status_code == 401

    def test_reload_config_still_rejects_unauthenticated_request(self):
        import src.main as main

        with TestClient(main.app) as client:
            response = client.post("/webhook/reload-config")

        assert response.status_code == 401

    def test_webhook_accepts_request_without_auth_when_no_key_is_configured_anywhere(self, monkeypatch, caplog):
        import logging
        import src.main as main

        os.environ.pop("WEBHOOK_API_KEY", None)
        os.environ.pop("EVOLUTION_API_KEY", None)
        os.environ.pop("EVOLUTION_WEBHOOK_API_KEY", None)

        # Repor flag global para nao depender de ordem dos testes
        import src.interfaces.http.app as app_module
        app_module._webhook_auth_warning_logged = False

        def fake_process_message(**kwargs):
            return "Tudo certo"

        async def fake_send_message(self, phone, message):
            return True

        monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )

        with caplog.at_level(logging.CRITICAL, logger="wpp-dental"):
            with TestClient(main.app) as client:
                response = client.post("/webhook/message", json=_build_payload("no-key-1"))

        assert response.status_code == 200
        assert response.json()["status"] == "processed"
        assert any("WEBHOOK EXPOSTO" in r.message for r in caplog.records)

    def test_webhook_accepts_evolution_api_key_when_webhook_key_is_configured(self, monkeypatch):
        import src.main as main

        os.environ["WEBHOOK_API_KEY"] = "test-secret"
        os.environ["EVOLUTION_API_KEY"] = "evolution-only-key"

        def fake_process_message(**kwargs):
            return "Tudo certo"

        async def fake_send_message(self, phone, message):
            return True

        monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )

        with TestClient(main.app) as client:
            response = client.post(
                "/webhook/message",
                json=_build_payload("evo-key-1"),
                headers={"apikey": "evolution-only-key"},
            )

        assert response.status_code == 200
        assert response.json()["status"] == "processed"

    def test_lid_remote_jid_uses_real_sender_jid_for_reply(self, monkeypatch):
        import src.main as main

        captured = {"process_phone": "", "send_phone": ""}

        def fake_process_message(**kwargs):
            captured["process_phone"] = kwargs["patient_phone"]
            return "Tudo certo"

        async def fake_send_message(self, phone, message):
            captured["send_phone"] = phone
            return True

        payload = _build_payload("lid-1")
        payload["data"]["key"]["remoteJid"] = "123456789012345@lid"
        payload["data"]["sender"] = "5511999999999@s.whatsapp.net"

        monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )

        with TestClient(main.app) as client:
            response = client.post(
                "/webhook/message",
                json=payload,
                headers={"apikey": "test-secret"},
            )

        assert response.status_code == 200
        assert response.json()["phone"] == "5511999999999"
        assert captured == {
            "process_phone": "5511999999999",
            "send_phone": "5511999999999",
        }

    def test_lid_message_list_uses_parent_sender_jid_for_reply(self, monkeypatch):
        import src.main as main

        captured = {"send_phone": ""}

        def fake_process_message(**kwargs):
            return "Tudo certo"

        async def fake_send_message(self, phone, message):
            captured["send_phone"] = phone
            return True

        payload = {
            "event": "messages.upsert",
            "data": {
                "sender": "5511999999999@s.whatsapp.net",
                "messages": [
                    {
                        "key": {
                            "id": "lid-list-1",
                            "fromMe": False,
                            "remoteJid": "123456789012345@lid",
                        },
                        "pushName": "Maria",
                        "message": {"conversation": "Oi"},
                    }
                ],
            },
        }

        monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )

        with TestClient(main.app) as client:
            response = client.post(
                "/webhook/message",
                json=payload,
                headers={"apikey": "test-secret"},
            )

        assert response.status_code == 200
        assert response.json()["phone"] == "5511999999999"
        assert captured["send_phone"] == "5511999999999"

    def test_failed_delivery_can_retry_same_message(self, monkeypatch):
        import src.main as main
        from src.application.services.conversation_service import ConversationService

        call_count = {"process": 0, "send": 0}

        def fake_process_message(**kwargs):
            call_count["process"] += 1
            return "Resposta segura"

        async def fake_send_message(self, phone, message):
            call_count["send"] += 1
            return call_count["send"] > 1

        monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )

        with TestClient(main.app) as client:
            first = client.post(
                "/webhook/message",
                json=_build_payload("retry-1"),
                headers={"apikey": "test-secret"},
            )
            second = client.post(
                "/webhook/message",
                json=_build_payload("retry-1"),
                headers={"apikey": "test-secret"},
            )

        assert first.status_code == 502
        assert second.status_code == 200
        assert call_count["process"] == 2

        history = ConversationService.get_history("5511999999999")
        assert len(history) == 2
        assert history[0]["role"] == "patient"
        assert history[1]["role"] == "assistant"

    def test_successful_message_is_deduplicated(self, monkeypatch):
        import src.main as main

        call_count = {"process": 0}

        def fake_process_message(**kwargs):
            call_count["process"] += 1
            return "Tudo certo"

        async def fake_send_message(self, phone, message):
            return True

        monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )

        with TestClient(main.app) as client:
            first = client.post(
                "/webhook/message",
                json=_build_payload("done-1"),
                headers={"apikey": "test-secret"},
            )
            second = client.post(
                "/webhook/message",
                json=_build_payload("done-1"),
                headers={"apikey": "test-secret"},
            )

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["status"] == "duplicate"
        assert call_count["process"] == 1

    def test_out_of_scope_message_is_escalated_without_llm(self, monkeypatch):
        import src.main as main

        call_count = {"process": 0, "alert": 0}

        def fake_process_message(**kwargs):
            call_count["process"] += 1
            return "Nao deveria executar"

        def fake_send_alert(self, patient_name, patient_phone, summary, reason, last_message=""):
            call_count["alert"] += 1
            return True

        async def fake_send_message(self, phone, message):
            return True

        monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)
        monkeypatch.setattr(
            "src.infrastructure.integrations.alert_service.AlertService.send_alert",
            fake_send_alert,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )

        payload = _build_payload("scope-1")
        payload["data"]["message"]["conversation"] = "Quanto custa um implante?"

        with TestClient(main.app) as client:
            response = client.post(
                "/webhook/message",
                json=payload,
                headers={"apikey": "test-secret"},
            )

        assert response.status_code == 200
        assert response.json()["status"] == "escalated"
        assert response.json()["reason"] == "fora_do_escopo"
        assert call_count["process"] == 0
        assert call_count["alert"] == 1

    def test_offered_slot_selection_requests_explicit_confirmation_before_booking(self, monkeypatch):
        import src.main as main
        from src.infrastructure.persistence.connection import get_db, init_db

        call_count = {"process": 0, "create": 0}

        def fake_process_message(**kwargs):
            call_count["process"] += 1
            return "Nao deveria repetir as opcoes"

        async def fake_send_message(self, phone, message):
            return True

        def fake_create_appointment_if_available(self, patient_name, patient_phone, start_time):
            call_count["create"] += 1
            return {"id": "evt-123"}

        monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.create_appointment_if_available",
            fake_create_appointment_if_available,
        )
        with TestClient(main.app) as client:
            init_db()
            db = get_db()
            db.execute(
                "INSERT INTO patients (phone, name, plan) VALUES (?, ?, ?)",
                ("11999999999", "Cristian", "Amil Dental"),
            )
            db.execute(
                "INSERT INTO conversation_history (phone, role, content) VALUES (?, ?, ?)",
                (
                    "5511999999999",
                    "assistant",
                    (
                        "Cristian, temos duas opcoes disponiveis para agendar sua consulta "
                        "no dia 07/04/2026 pela manha: as 08:00 ou 08:15. Qual voce prefere?"
                    ),
                ),
            )
            db.commit()

            payload = _build_payload("slot-1")
            payload["data"]["message"]["conversation"] = "Pode ser as 8"

            response = client.post(
                "/webhook/message",
                json=payload,
                headers={"apikey": "test-secret"},
            )

        assert response.status_code == 200
        assert response.json()["status"] == "slot_confirmation_requested"
        assert response.json()["selected_time"] == "08:00"
        assert call_count["process"] == 0
        assert call_count["create"] == 0

        assistant_message = get_db().execute(
            "SELECT content FROM conversation_history WHERE role = ? ORDER BY id DESC LIMIT 1",
            ("assistant",),
        ).fetchone()
        assert assistant_message is not None
        assert "Posso confirmar sua consulta?" in assistant_message["content"]

    def test_offered_slot_selection_uses_state_for_second_option(self, monkeypatch):
        import src.main as main
        from src.application.services.conversation_state_service import ConversationState, ConversationStateService
        from src.infrastructure.persistence.connection import get_db, init_db

        call_count = {"process": 0, "create": 0}

        def fake_process_message(**kwargs):
            call_count["process"] += 1
            return "Nao deveria repetir as opcoes"

        async def fake_send_message(self, phone, message):
            return True

        def fake_create_appointment_if_available(self, patient_name, patient_phone, start_time):
            call_count["create"] += 1
            return {"id": "evt-123"}

        monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.create_appointment_if_available",
            fake_create_appointment_if_available,
        )
        with TestClient(main.app) as client:
            init_db()
            db = get_db()
            db.execute(
                "INSERT INTO patients (phone, name, plan) VALUES (?, ?, ?)",
                ("11999999999", "Maria", "Amil Dental"),
            )
            db.commit()
            ConversationStateService.save(
                "5511999999999",
                ConversationState(offered_date="19/05/2026", offered_times=["08:45", "09:15"]),
            )

            payload = _build_payload("slot-state-2")
            payload["data"]["message"]["conversation"] = "2"

            response = client.post(
                "/webhook/message",
                json=payload,
                headers={"apikey": "test-secret"},
            )

        assert response.status_code == 200
        assert response.json()["status"] == "slot_confirmation_requested"
        assert response.json()["selected_time"] == "09:15"
        assert call_count["process"] == 0
        assert call_count["create"] == 0

    def test_slot_selection_requires_plan_before_confirmation(self, monkeypatch):
        import src.main as main
        from src.application.services.conversation_state_service import ConversationState, ConversationStateService
        from src.infrastructure.persistence.connection import get_db, init_db

        call_count = {"process": 0, "create": 0}

        def fake_process_message(**kwargs):
            call_count["process"] += 1
            return "Nao deveria executar"

        async def fake_send_message(self, phone, message):
            return True

        def fake_create_appointment_if_available(self, patient_name, patient_phone, start_time):
            call_count["create"] += 1
            return {"id": "evt-123"}

        monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.create_appointment_if_available",
            fake_create_appointment_if_available,
        )

        with TestClient(main.app) as client:
            init_db()
            db = get_db()
            db.execute(
                "INSERT INTO patients (phone, name) VALUES (?, ?)",
                ("11999999999", "Maria"),
            )
            db.commit()
            ConversationStateService.save(
                "5511999999999",
                ConversationState(offered_date="19/05/2026", offered_times=["08:45", "09:15"]),
            )

            payload = _build_payload("slot-plan-required")
            payload["data"]["message"]["conversation"] = "2"

            response = client.post(
                "/webhook/message",
                json=payload,
                headers={"apikey": "test-secret"},
            )

        state = ConversationStateService.get("5511999999999")
        assert response.status_code == 200
        assert response.json()["status"] == "slot_plan_required"
        assert state.stage == "awaiting_plan_for_slot_confirmation"
        assert state.pending_slot_time == "09:15"
        assert call_count["process"] == 0
        assert call_count["create"] == 0

    def test_pending_slot_plan_resumes_confirmation_without_booking(self, monkeypatch):
        import src.main as main
        from src.application.services.conversation_state_service import ConversationState, ConversationStateService
        from src.infrastructure.persistence.connection import get_db, init_db

        call_count = {"process": 0, "create": 0}

        def fake_process_message(**kwargs):
            call_count["process"] += 1
            return "Nao deveria executar"

        async def fake_send_message(self, phone, message):
            return True

        def fake_create_appointment_if_available(self, patient_name, patient_phone, start_time):
            call_count["create"] += 1
            return {"id": "evt-123"}

        monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.create_appointment_if_available",
            fake_create_appointment_if_available,
        )

        with TestClient(main.app) as client:
            init_db()
            db = get_db()
            db.execute(
                "INSERT INTO patients (phone, name) VALUES (?, ?)",
                ("11999999999", "Maria"),
            )
            db.commit()
            ConversationStateService.save(
                "5511999999999",
                ConversationState(
                    stage="awaiting_plan_for_slot_confirmation",
                    pending_slot_date="19/05/2026",
                    pending_slot_time="09:15",
                ),
            )

            payload = _build_payload("slot-plan-resume")
            payload["data"]["message"]["conversation"] = "Amil Dental"

            response = client.post(
                "/webhook/message",
                json=payload,
                headers={"apikey": "test-secret"},
            )

        state = ConversationStateService.get("5511999999999")
        assert response.status_code == 200
        assert response.json()["status"] == "pending_slot_plan_resolved"
        assert state.plan_name == "Amil Dental"
        assert call_count["process"] == 0
        assert call_count["create"] == 0

    def test_slot_confirmation_books_only_after_patient_confirms(self, monkeypatch):
        import src.main as main
        from src.infrastructure.persistence.connection import get_db, init_db

        call_count = {"process": 0, "create": 0, "cancel": 0}

        def fake_process_message(**kwargs):
            call_count["process"] += 1
            return "Nao deveria executar"

        async def fake_send_message(self, phone, message):
            return True

        def fake_create_appointment_if_available(self, patient_name, patient_phone, start_time):
            call_count["create"] += 1
            return {"id": "evt-123"}

        def fake_cancel_appointment(self, event_id):
            call_count["cancel"] += 1
            return CancelResult(cancelled=True, already_absent=False, error=None)

        monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.create_appointment_if_available",
            fake_create_appointment_if_available,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.cancel_appointment",
            fake_cancel_appointment,
        )

        with TestClient(main.app) as client:
            init_db()
            db = get_db()
            db.execute(
                "INSERT INTO patients (phone, name, plan) VALUES (?, ?, ?)",
                ("11999999999", "Maria", "Amil Dental"),
            )
            db.execute(
                "INSERT INTO conversation_history (phone, role, content) VALUES (?, ?, ?)",
                (
                    "5511999999999",
                    "assistant",
                    (
                        "Maria, encontrei esse horario para voce: 07/04/2026 as 08:00. "
                        "Posso confirmar sua consulta?"
                    ),
                ),
            )
            db.commit()

            payload = _build_payload("slot-confirm-1")
            payload["data"]["message"]["conversation"] = "Sim, pode confirmar"

            response = client.post(
                "/webhook/message",
                json=payload,
                headers={"apikey": "test-secret"},
            )

        assert response.status_code == 200
        assert response.json()["status"] == "slot_confirmation_resolved"
        assert response.json()["selected_time"] == "08:00"
        assert call_count["process"] == 0
        assert call_count["create"] == 1
        assert call_count["cancel"] == 0

        patient = get_db().execute(
            "SELECT phone FROM patients WHERE name = ?",
            ("Maria",),
        ).fetchone()
        assert patient is not None
        # PH-01: formato canônico = DDD(2) + 8 dígitos (9o dígito removido)
        assert patient["phone"] == "1199999999"

    def test_slot_confirmation_cancels_previous_event_when_rescheduling(self, monkeypatch):
        import src.main as main
        from src.infrastructure.persistence.connection import get_db, init_db
        from src.application.services.conversation_state_service import ConversationState, ConversationStateService

        call_count = {"process": 0, "create": 0, "cancel": 0}

        def fake_process_message(**kwargs):
            call_count["process"] += 1
            return "Nao deveria executar"

        async def fake_send_message(self, phone, message):
            return True

        def fake_create_appointment_if_available(self, patient_name, patient_phone, start_time):
            call_count["create"] += 1
            return {"id": "evt-new"}

        def fake_cancel_appointment(self, event_id):
            call_count["cancel"] += 1
            return CancelResult(cancelled=(event_id == "evt-old"), already_absent=False, error=None)

        monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.create_appointment_if_available",
            fake_create_appointment_if_available,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.cancel_appointment",
            fake_cancel_appointment,
        )

        with TestClient(main.app) as client:
            init_db()
            db = get_db()
            db.execute(
                "INSERT INTO conversation_history (phone, role, content) VALUES (?, ?, ?)",
                (
                    "5511999999999",
                    "assistant",
                    "Maria, encontrei esse horario para voce: 07/04/2026 as 08:00. Posso confirmar sua consulta?",
                ),
            )
            db.commit()
            ConversationStateService.save(
                "5511999999999",
                ConversationState(
                    intent="reschedule",
                    patient_name="Maria",
                    plan_name="Amil Dental",
                    reschedule_event_id="evt-old",
                    reschedule_event_label="07/04/2026 as 08:00",
                ),
            )

            payload = _build_payload("slot-confirm-2")
            payload["data"]["message"]["conversation"] = "Sim, pode confirmar"

            response = client.post(
                "/webhook/message",
                json=payload,
                headers={"apikey": "test-secret"},
            )

        state = ConversationStateService.get("5511999999999")

        assert response.status_code == 200
        assert response.json()["status"] == "slot_confirmation_resolved"
        assert call_count["process"] == 0
        assert call_count["create"] == 1
        assert call_count["cancel"] == 1
        assert state.stage == "idle"

    def test_slot_confirmation_preserves_state_when_reschedule_cancel_fails(self, monkeypatch):
        import src.main as main
        from src.infrastructure.persistence.connection import get_db, init_db
        from src.application.services.conversation_state_service import ConversationState, ConversationStateService

        call_count = {"process": 0, "create": 0, "cancel": 0, "alert": 0}
        captured_alert = {}

        def fake_process_message(**kwargs):
            call_count["process"] += 1
            return "Nao deveria executar"

        async def fake_send_message(self, phone, message):
            return True

        def fake_create_appointment_if_available(self, patient_name, patient_phone, start_time):
            call_count["create"] += 1
            return {"id": "evt-new"}

        def fake_cancel_appointment(self, event_id):
            call_count["cancel"] += 1
            return CancelResult(cancelled=False, already_absent=False, error="simulated network error")

        def fake_send_alert(self, patient_name, patient_phone, summary, reason, last_message=""):
            call_count["alert"] += 1
            captured_alert.update(
                {
                    "patient_name": patient_name,
                    "patient_phone": patient_phone,
                    "summary": summary,
                    "reason": reason,
                    "last_message": last_message,
                }
            )
            return True

        monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.create_appointment_if_available",
            fake_create_appointment_if_available,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.cancel_appointment",
            fake_cancel_appointment,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.alert_service.AlertService.send_alert",
            fake_send_alert,
        )

        with TestClient(main.app) as client:
            init_db()
            db = get_db()
            db.execute(
                "INSERT INTO conversation_history (phone, role, content) VALUES (?, ?, ?)",
                (
                    "5511999999999",
                    "assistant",
                    "Maria, encontrei esse horario para voce: 07/04/2026 as 08:00. Posso confirmar sua consulta?",
                ),
            )
            db.commit()
            ConversationStateService.save(
                "5511999999999",
                ConversationState(
                    intent="reschedule",
                    patient_name="Maria",
                    plan_name="Amil Dental",
                    reschedule_event_id="evt-old",
                    reschedule_event_label="06/04/2026 as 09:00",
                ),
            )

            payload = _build_payload("slot-confirm-partial")
            payload["data"]["message"]["conversation"] = "Sim, pode confirmar"

            response = client.post(
                "/webhook/message",
                json=payload,
                headers={"apikey": "test-secret"},
            )

        state = ConversationStateService.get("5511999999999")
        assistant_message = get_db().execute(
            "SELECT content FROM conversation_history WHERE role = ? ORDER BY id DESC LIMIT 1",
            ("assistant",),
        ).fetchone()

        assert response.status_code == 200
        assert response.json()["status"] == "partial_reschedule_pending"
        assert assistant_message is not None
        assert "confirmar a remarcacao por completo" in assistant_message["content"]
        assert call_count["process"] == 0
        assert call_count["create"] == 1
        assert call_count["cancel"] == 1
        assert call_count["alert"] == 1
        assert captured_alert["reason"] == "remarcacao_parcial"
        assert "evt-old" in captured_alert["summary"]
        assert "evt-new" in captured_alert["summary"]
        assert state.intent == "reschedule"
        assert state.reschedule_event_id == "evt-old"
        assert state.metadata["partial_reschedule_new_event_id"] == "evt-new"
        assert state.metadata["partial_reschedule_new_slot"] == "07/04/2026 08:00"
        assert state.metadata["partial_reschedule_reason"] == "remarcacao_parcial"

    def test_reschedule_without_original_event_does_not_create_new_event(self, monkeypatch):
        import src.main as main
        from src.infrastructure.persistence.connection import get_db, init_db
        from src.application.services.conversation_state_service import ConversationState, ConversationStateService

        call_count = {"process": 0, "create": 0, "cancel": 0}

        def fake_process_message(**kwargs):
            call_count["process"] += 1
            return "Nao deveria executar"

        async def fake_send_message(self, phone, message):
            return True

        def fake_create_appointment_if_available(self, patient_name, patient_phone, start_time):
            call_count["create"] += 1
            return {"id": "evt-new"}

        def fake_cancel_appointment(self, event_id):
            call_count["cancel"] += 1
            return CancelResult(cancelled=True, already_absent=False, error=None)

        monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.create_appointment_if_available",
            fake_create_appointment_if_available,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.cancel_appointment",
            fake_cancel_appointment,
        )

        with TestClient(main.app) as client:
            init_db()
            db = get_db()
            db.execute(
                "INSERT INTO conversation_history (phone, role, content) VALUES (?, ?, ?)",
                (
                    "5511999999999",
                    "assistant",
                    "Maria, encontrei esse horario para voce: 07/04/2026 as 08:00. Posso confirmar sua consulta?",
                ),
            )
            db.commit()
            ConversationStateService.save(
                "5511999999999",
                ConversationState(
                    intent="reschedule",
                    patient_name="Maria",
                    plan_name="Amil Dental",
                ),
            )

            payload = _build_payload("slot-confirm-missing-old")
            payload["data"]["message"]["conversation"] = "Sim, pode confirmar"

            response = client.post(
                "/webhook/message",
                json=payload,
                headers={"apikey": "test-secret"},
            )

        state = ConversationStateService.get("5511999999999")

        assert response.status_code == 200
        assert response.json()["status"] == "reschedule_original_required"
        assert "consulta antiga" in response.json()["response_preview"]
        assert call_count["process"] == 0
        assert call_count["create"] == 0
        assert call_count["cancel"] == 0
        assert state.intent == "reschedule"
        assert state.pending_slot_date == "07/04/2026"
        assert state.pending_slot_time == "08:00"

    def test_rejected_pending_slot_is_persisted_before_llm(self, monkeypatch):
        import src.main as main
        from src.application.services.conversation_state_service import ConversationStateService
        from src.infrastructure.persistence.connection import get_db, init_db

        captured = {}

        def fake_process_message(**kwargs):
            captured["state"] = ConversationStateService.get(kwargs["patient_phone"])
            return "Vou procurar outra opcao para voce."

        async def fake_send_message(self, phone, message):
            return True

        monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )

        with TestClient(main.app) as client:
            init_db()
            db = get_db()
            db.execute(
                "INSERT INTO patients (phone, name, plan) VALUES (?, ?, ?)",
                ("11999999999", "Maria", "Amil Dental"),
            )
            db.execute(
                "INSERT INTO conversation_history (phone, role, content) VALUES (?, ?, ?)",
                (
                    "5511999999999",
                    "assistant",
                    "Maria, separei este horario para voce 26/05/2026 as 16:20. Posso confirmar sua consulta?",
                ),
            )
            db.commit()

            payload = _build_payload("reject-slot-1")
            payload["data"]["message"]["conversation"] = "Nao quero esse horario"

            response = client.post(
                "/webhook/message",
                json=payload,
                headers={"apikey": "test-secret"},
            )

        assert response.status_code == 200
        assert response.json()["status"] == "processed"
        assert "26/05/2026 16:20" in captured["state"].rejected_slots

    def test_new_time_constraint_does_not_confirm_old_morning_slot(self, monkeypatch):
        import src.main as main
        from src.application.services.conversation_state_service import ConversationStateService
        from src.infrastructure.persistence.connection import get_db, init_db

        call_count = {"create": 0}
        captured = {}

        def fake_process_message(**kwargs):
            captured["state"] = ConversationStateService.get(kwargs["patient_phone"])
            return "Vou buscar uma opcao depois das 13h."

        async def fake_send_message(self, phone, message):
            return True

        def fake_create_appointment_if_available(self, patient_name, patient_phone, start_time):
            call_count["create"] += 1
            return {"id": "evt-wrong"}

        monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.create_appointment_if_available",
            fake_create_appointment_if_available,
        )

        with TestClient(main.app) as client:
            init_db()
            db = get_db()
            db.execute(
                "INSERT INTO patients (phone, name, plan) VALUES (?, ?, ?)",
                ("11999999999", "Maria", "Amil Dental"),
            )
            db.execute(
                "INSERT INTO conversation_history (phone, role, content) VALUES (?, ?, ?)",
                (
                    "5511999999999",
                    "assistant",
                    "Maria, separei este horario para voce 26/05/2026 as 11:15. Posso confirmar sua consulta?",
                ),
            )
            db.commit()

            payload = _build_payload("after-13-1")
            payload["data"]["message"]["conversation"] = "Eu so consigo no periodo da tarde depois das 13h"

            response = client.post(
                "/webhook/message",
                json=payload,
                headers={"apikey": "test-secret"},
            )

        assert response.status_code == 200
        assert call_count["create"] == 0
        assert captured["state"].earliest_time == "13:00"
        assert captured["state"].requested_period == "tarde"

    def test_same_time_with_different_day_is_not_treated_as_confirmation(self, monkeypatch):
        import src.main as main
        from src.application.services.conversation_state_service import ConversationState, ConversationStateService
        from src.infrastructure.persistence.connection import get_db, init_db

        call_count = {"create": 0, "process": 0}

        def fake_process_message(**kwargs):
            call_count["process"] += 1
            return "Nao deveria executar"

        async def fake_send_message(self, phone, message):
            return True

        def fake_create_appointment_if_available(self, patient_name, patient_phone, start_time):
            call_count["create"] += 1
            return {"id": "evt-wrong"}

        monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.create_appointment_if_available",
            fake_create_appointment_if_available,
        )

        with TestClient(main.app) as client:
            init_db()
            db = get_db()
            db.execute(
                "INSERT INTO patients (phone, name, plan) VALUES (?, ?, ?)",
                ("11999999999", "Maria", "Amil Dental"),
            )
            db.commit()
            ConversationStateService.save(
                "5511999999999",
                ConversationState(offered_date="01/06/2026", offered_times=["14:30"]),
            )

            payload = _build_payload("day-8-1430")
            payload["data"]["message"]["conversation"] = "Dia 8 as 14:30?"

            response = client.post(
                "/webhook/message",
                json=payload,
                headers={"apikey": "test-secret"},
            )

        assert response.status_code == 200
        assert response.json()["status"] == "slot_selection_rejected"
        assert call_count["create"] == 0
        assert call_count["process"] == 0

    def test_first_monday_with_excluded_day_is_persisted_before_llm(self, monkeypatch):
        import importlib
        import src.main as main
        from src.application.services.conversation_state_service import ConversationStateService
        from src.infrastructure.persistence.connection import get_db, init_db

        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 5, 20, 10, 0, tzinfo=tz)

        captured = {}

        def fake_process_message(**kwargs):
            captured["state"] = ConversationStateService.get(kwargs["patient_phone"])
            return "Vou procurar a proxima segunda disponivel."

        async def fake_send_message(self, phone, message):
            return True

        app_module = importlib.import_module("src.interfaces.http.app")
        monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)
        monkeypatch.setattr(app_module, "datetime", FixedDatetime)
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )

        with TestClient(main.app) as client:
            init_db()
            db = get_db()
            db.execute(
                "INSERT INTO patients (phone, name, plan) VALUES (?, ?, ?)",
                ("11999999999", "Maria", "Amil Dental"),
            )
            db.commit()

            payload = _build_payload("first-monday-minus-1")
            payload["data"]["message"]["conversation"] = "Primeira segunda-feira qualquer, menos no dia primeiro"

            response = client.post(
                "/webhook/message",
                json=payload,
                headers={"apikey": "test-secret"},
            )

        assert response.status_code == 200
        assert captured["state"].requested_weekday == "0"
        assert "01/06/2026" in captured["state"].excluded_dates

    def test_new_message_after_terminal_action_starts_fresh_context(self, monkeypatch):
        import src.main as main
        from src.infrastructure.persistence.connection import get_db, init_db

        captured: dict = {}

        def fake_process_message(**kwargs):
            captured.update(kwargs)
            return "Oi! Como posso te ajudar hoje?"

        async def fake_send_message(self, phone, message):
            return True

        monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )

        with TestClient(main.app) as client:
            init_db()
            db = get_db()
            db.execute(
                "INSERT INTO conversation_history (phone, role, content) VALUES (?, ?, ?)",
                ("5511999999999", "patient", "Gostaria de cancelar"),
            )
            db.execute(
                "INSERT INTO conversation_history (phone, role, content) VALUES (?, ?, ?)",
                (
                    "5511999999999",
                    "assistant",
                    (
                        "Sua consulta agendada para o dia 07/04/2026 as 08:00 foi cancelada com sucesso. "
                        "Se precisar de mais alguma coisa, estou a disposicao."
                    ),
                ),
            )
            db.commit()

            payload = _build_payload("fresh-start-1")
            payload["data"]["message"]["conversation"] = "Ola"

            response = client.post(
                "/webhook/message",
                json=payload,
                headers={"apikey": "test-secret"},
            )

        assert response.status_code == 200
        assert response.json()["status"] == "processed"
        assert captured["is_first_message"] is True
        assert "PRIMEIRA mensagem do paciente" in captured["history_text"]

    def test_cancel_confirmation_does_not_trigger_slot_confirmation_flow(self, monkeypatch):
        import src.main as main
        from src.infrastructure.persistence.connection import get_db, init_db
        from src.application.services.conversation_state_service import ConversationState, ConversationStateService

        call_count = {"process": 0, "create": 0, "cancel": 0}

        def fake_process_message(**kwargs):
            call_count["process"] += 1
            return "Consulta cancelada com sucesso."

        async def fake_send_message(self, phone, message):
            return True

        def fake_create_appointment_if_available(self, patient_name, patient_phone, start_time):
            call_count["create"] += 1
            return {"id": "evt-new"}

        def fake_cancel_appointment(self, event_id):
            call_count["cancel"] += 1
            return CancelResult(cancelled=True, already_absent=False, error=None)

        monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.create_appointment_if_available",
            fake_create_appointment_if_available,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.cancel_appointment",
            fake_cancel_appointment,
        )

        with TestClient(main.app) as client:
            init_db()
            db = get_db()
            db.execute(
                "INSERT INTO conversation_history (phone, role, content) VALUES (?, ?, ?)",
                (
                    "5511999999999",
                    "assistant",
                    (
                        "Encontrei sua consulta:\n\n"
                        "Data: 07/04/2026\n"
                        "Horario: 08:00\n\n"
                        "Deseja realmente cancelar?"
                    ),
                ),
            )
            db.commit()
            ConversationStateService.save(
                "5511999999999",
                ConversationState(
                    stage="awaiting_cancel_confirmation",
                    intent="cancel",
                    patient_name="Cristian",
                    pending_event_id="evt-old",
                    pending_event_label="07/04/2026 as 08:00",
                ),
            )

            payload = _build_payload("cancel-confirm-1")
            payload["data"]["message"]["conversation"] = "sim"

            response = client.post(
                "/webhook/message",
                json=payload,
                headers={"apikey": "test-secret"},
            )

        # Com impl 005: awaiting_cancel_confirmation + "sim" → cancela diretamente (não cai no LLM)
        assert response.status_code == 200
        assert response.json()["status"] == "appointment_cancelled"
        assert call_count["process"] == 0
        assert call_count["create"] == 0
        assert call_count["cancel"] == 1

    def test_proactive_confirmation_reschedule_preserves_original_event_id(self, monkeypatch):
        import src.main as main
        from src.application.services.appointment_confirmation_service import AppointmentConfirmationService
        from src.application.services.conversation_state_service import ConversationState, ConversationStateService
        from src.infrastructure.persistence.connection import init_db

        call_count = {"process": 0, "create": 0, "cancel": 0}

        def fake_process_message(**kwargs):
            call_count["process"] += 1
            return "Nao deveria executar"

        async def fake_send_message(self, phone, message):
            return True

        def fake_create_appointment_if_available(self, patient_name, patient_phone, start_time):
            call_count["create"] += 1
            return {"id": "evt-new"}

        def fake_cancel_appointment(self, event_id):
            call_count["cancel"] += 1
            return CancelResult(cancelled=True, already_absent=False, error=None)

        monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.create_appointment_if_available",
            fake_create_appointment_if_available,
        )
        monkeypatch.setattr(
            "src.infrastructure.integrations.calendar_service.CalendarService.cancel_appointment",
            fake_cancel_appointment,
        )

        with TestClient(main.app) as client:
            init_db()
            ConversationStateService.save(
                "5511999999999",
                ConversationState(
                    stage=AppointmentConfirmationService.CONFIRMATION_STAGE,
                    patient_name="Maria",
                    plan_name="Amil Dental",
                    pending_event_id="evt-old",
                    pending_event_label="07/04/2026 as 08:00",
                    metadata={
                        AppointmentConfirmationService.METADATA_EVENT_ID_KEY: "evt-old",
                    },
                ),
            )

            payload = _build_payload("proactive-reschedule-1")
            payload["data"]["message"]["conversation"] = "quero remarcar"

            response = client.post(
                "/webhook/message",
                json=payload,
                headers={"apikey": "test-secret"},
            )

        state = ConversationStateService.get("5511999999999")

        assert response.status_code == 200
        assert response.json()["status"] == "reschedule_requested"
        assert state.intent == "reschedule"
        assert state.stage == "idle"
        assert state.reschedule_event_id == "evt-old"
        assert state.reschedule_event_label == "07/04/2026 as 08:00"
        assert call_count["process"] == 0
        assert call_count["create"] == 0
        assert call_count["cancel"] == 0

    def test_manual_doctor_message_activates_handoff_and_blocks_agent_for_30_minutes(self, monkeypatch):
        import src.main as main
        from src.application.services.conversation_service import ConversationService

        call_count = {"process": 0, "send": 0}

        def fake_process_message(**kwargs):
            call_count["process"] += 1
            return "Nao deveria responder"

        async def fake_send_message(self, phone, message):
            call_count["send"] += 1
            return True

        monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )

        with TestClient(main.app) as client:
            handoff_response = client.post(
                "/webhook/message",
                json=_build_from_me_payload("doctor-1", "Pode deixar que eu assumo daqui."),
                headers={"apikey": "test-secret"},
            )
            patient_response = client.post(
                "/webhook/message",
                json=_build_payload("patient-after-handoff"),
                headers={"apikey": "test-secret"},
            )

        assert handoff_response.status_code == 200
        assert handoff_response.json()["status"] == "handoff_activated"

        assert patient_response.status_code == 200
        assert patient_response.json()["status"] == "handoff_active"
        assert call_count["process"] == 0
        assert call_count["send"] == 0

        history = ConversationService.get_history("5511999999999")
        assert history[-2]["role"] == "doctor"
        assert history[-2]["content"] == "Pode deixar que eu assumo daqui."
        assert history[-1]["role"] == "patient"
        assert history[-1]["content"] == "Oi"

    def test_handoff_still_blocks_when_from_me_remote_jid_has_device_suffix(self, monkeypatch):
        import src.main as main

        call_count = {"process": 0, "send": 0}

        def fake_process_message(**kwargs):
            call_count["process"] += 1
            return "Nao deveria responder"

        async def fake_send_message(self, phone, message):
            call_count["send"] += 1
            return True

        monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )

        doctor_payload = _build_from_me_payload("doctor-suffix-1", "Eu vou cuidar desse caso.")
        doctor_payload["data"]["key"]["remoteJid"] = "5511999999999:17@s.whatsapp.net"

        with TestClient(main.app) as client:
            handoff_response = client.post(
                "/webhook/message",
                json=doctor_payload,
                headers={"apikey": "test-secret"},
            )
            patient_response = client.post(
                "/webhook/message",
                json=_build_payload("patient-after-suffix-handoff"),
                headers={"apikey": "test-secret"},
            )

        assert handoff_response.status_code == 200
        assert handoff_response.json()["status"] == "handoff_activated"
        assert patient_response.status_code == 200
        assert patient_response.json()["status"] == "handoff_active"
        assert call_count["process"] == 0
        assert call_count["send"] == 0

    def test_outbound_bot_echo_does_not_activate_handoff(self, monkeypatch):
        import src.main as main
        from src.infrastructure.persistence import OutboundMessageStore

        call_count = {"process": 0}

        def fake_process_message(**kwargs):
            call_count["process"] += 1
            return "Tudo certo"

        async def fake_send_message(self, phone, message):
            OutboundMessageStore.record(phone, message)
            return True

        monkeypatch.setattr(main.dental_crew, "process_message", fake_process_message)
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send_message,
        )

        with TestClient(main.app) as client:
            processed = client.post(
                "/webhook/message",
                json=_build_payload("bot-echo-initial"),
                headers={"apikey": "test-secret"},
            )
            echo = client.post(
                "/webhook/message",
                json=_build_from_me_payload("bot-echo-outbound", "Tudo certo"),
                headers={"apikey": "test-secret"},
            )
            follow_up = client.post(
                "/webhook/message",
                json=_build_payload("bot-echo-follow-up"),
                headers={"apikey": "test-secret"},
            )

        assert processed.status_code == 200
        assert echo.status_code == 200
        assert echo.json()["reason"] == "assistant_outbound_echo"
        assert follow_up.status_code == 200
        assert follow_up.json()["status"] == "processed"
        assert call_count["process"] == 2

    def test_delayed_outbound_bot_echo_does_not_activate_handoff(self, monkeypatch):
        import src.main as main
        from src.infrastructure.persistence import OutboundMessageStore
        from src.infrastructure.persistence.connection import get_db

        with TestClient(main.app) as client:
            OutboundMessageStore.record("5511999999999", "Resposta atrasada")
            db = get_db()
            db.execute(
                "UPDATE outbound_messages SET created_at = datetime('now', '-10 minutes')"
            )
            db.commit()

            echo = client.post(
                "/webhook/message",
                json=_build_from_me_payload("bot-echo-delayed", "Resposta atrasada"),
                headers={"apikey": "test-secret"},
            )

        assert echo.status_code == 200
        assert echo.json()["reason"] == "assistant_outbound_echo"

    def test_repeated_outbound_bot_echo_does_not_activate_handoff(self):
        import src.main as main
        from src.infrastructure.persistence import OutboundMessageStore

        with TestClient(main.app) as client:
            OutboundMessageStore.record("5511999999999", "Resposta repetida")

            first_echo = client.post(
                "/webhook/message",
                json=_build_from_me_payload("bot-echo-repeat-1", "Resposta repetida"),
                headers={"apikey": "test-secret"},
            )
            second_echo = client.post(
                "/webhook/message",
                json=_build_from_me_payload("bot-echo-repeat-2", "Resposta repetida"),
                headers={"apikey": "test-secret"},
            )

        assert first_echo.status_code == 200
        assert first_echo.json()["reason"] == "assistant_outbound_echo"
        assert second_echo.status_code == 200
        assert second_echo.json()["reason"] == "assistant_outbound_echo"

    def test_manual_message_with_same_text_and_different_id_still_activates_handoff(self):
        import src.main as main
        from src.infrastructure.persistence import OutboundMessageStore

        with TestClient(main.app) as client:
            OutboundMessageStore.record(
                "5511999999999",
                "Pode deixar que eu assumo daqui.",
                "bot-message-id",
            )

            manual_message = client.post(
                "/webhook/message",
                json=_build_from_me_payload(
                    "doctor-message-id",
                    "Pode deixar que eu assumo daqui.",
                ),
                headers={"apikey": "test-secret"},
            )

        assert manual_message.status_code == 200
        assert manual_message.json()["status"] == "handoff_activated"


# ---------------------------------------------------------------------------
# T-012 — Testes de segurança do webhook (impl 012)
# ---------------------------------------------------------------------------


class TestWebhookSecurity:
    """CA-001..CA-005: controle de acesso e redacao de PII no webhook."""

    def setup_method(self):
        self.db_path = Path("./data/test_webhook_security.db")
        os.environ["DATABASE_PATH"] = str(self.db_path)
        os.environ["WEBHOOK_API_KEY"] = "test-secret"
        os.environ.pop("EVOLUTION_WEBHOOK_API_KEY", None)
        os.environ.pop("EVOLUTION_API_KEY", None)

        from src.infrastructure.persistence.connection import close_db
        close_db()

    def teardown_method(self):
        from src.infrastructure.persistence.connection import close_db
        close_db()
        self.db_path.unlink(missing_ok=True)

    def test_webhook_with_valid_header_key_is_accepted(self, monkeypatch):
        """CA-003: chave valida em header -> 200."""
        import src.main as main

        monkeypatch.setattr(main.dental_crew, "process_message", lambda **k: "ok")
        async def fake_send(self, p, m): return True
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send,
        )

        with TestClient(main.app) as client:
            response = client.post(
                "/webhook/message",
                json=_build_payload("sec-1"),
                headers={"apikey": "test-secret"},
            )

        assert response.status_code == 200

    def test_webhook_with_no_header_is_rejected(self):
        """CA-001: chave configurada mas nenhum header -> 401."""
        import src.main as main

        with TestClient(main.app) as client:
            response = client.post("/webhook/message", json=_build_payload("sec-2"))

        assert response.status_code == 401

    def test_webhook_with_wrong_header_is_rejected(self):
        """CA-001: chave configurada mas header errado -> 401."""
        import src.main as main

        with TestClient(main.app) as client:
            response = client.post(
                "/webhook/message",
                json=_build_payload("sec-3"),
                headers={"apikey": "wrong-key"},
            )

        assert response.status_code == 401

    def test_webhook_key_in_body_is_rejected(self):
        """CA-004: chave correta so no corpo do payload -> 401."""
        import src.main as main

        payload = _build_payload("sec-4")
        payload["apikey"] = "test-secret"

        with TestClient(main.app) as client:
            response = client.post("/webhook/message", json=payload)

        assert response.status_code == 401

    def test_webhook_logs_do_not_contain_full_phone(self, monkeypatch, caplog):
        """CA-005: logs nao contêm telefone completo em texto claro."""
        import logging
        import src.main as main

        monkeypatch.setattr(main.dental_crew, "process_message", lambda **k: "ok")
        async def fake_send(self, p, m): return True
        monkeypatch.setattr(
            "src.infrastructure.integrations.whatsapp_service.WhatsAppService.send_message",
            fake_send,
        )

        with caplog.at_level(logging.INFO, logger="wpp-dental"):
            with TestClient(main.app) as client:
                client.post(
                    "/webhook/message",
                    json=_build_payload("sec-5"),
                    headers={"apikey": "test-secret"},
                )

        full_phone = "5511999999999"
        for record in caplog.records:
            assert full_phone not in record.getMessage(), (
                f"Log contem telefone completo: {record.getMessage()!r}"
            )
