"""Testes do webhook principal."""

import os
from pathlib import Path

from fastapi.testclient import TestClient


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

    def test_message_webhook_accepts_request_without_valid_auth_header(self, monkeypatch):
        import src.main as main

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
            response = client.post("/webhook/message", json=_build_payload())

        assert response.status_code == 200
        assert response.json()["status"] == "processed"

    def test_reload_config_still_rejects_unauthenticated_request(self):
        import src.main as main

        with TestClient(main.app) as client:
            response = client.post("/webhook/reload-config")

        assert response.status_code == 401

    def test_webhook_accepts_request_without_auth_when_no_webhook_key_is_configured(self, monkeypatch):
        import src.main as main

        os.environ.pop("WEBHOOK_API_KEY", None)
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
            response = client.post("/webhook/message", json=_build_payload("no-key-1"))

        assert response.status_code == 200
        assert response.json()["status"] == "processed"

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

    def test_slot_confirmation_books_only_after_patient_confirms(self, monkeypatch):
        import src.main as main
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

        patient = get_db().execute(
            "SELECT phone FROM patients WHERE name = ?",
            ("Maria",),
        ).fetchone()
        assert patient is not None
        assert patient["phone"] == "11999999999"

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
            return event_id == "evt-old"

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
            return event_id == "evt-old"

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

        assert response.status_code == 200
        assert response.json()["status"] == "processed"
        assert call_count["process"] == 1
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
