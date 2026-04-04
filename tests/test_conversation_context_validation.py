"""Bateria de validacao conversacional com 10 cenarios humanos."""

import os
import unicodedata
from datetime import datetime
from pathlib import Path

import pytest

from src.infrastructure.integrations.calendar_service import SAO_PAULO_TZ


def _slot(day: int, hour: int, minute: int) -> dict:
    start = datetime(2026, 4, day, hour, minute, tzinfo=SAO_PAULO_TZ)
    return {
        "start": start,
        "end": start,
        "formatted": start.strftime("%d/%m/%Y as %H:%M"),
    }


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    normalized = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(normalized.split())


CONTEXT_CASES = [
    {
        "id": "01-first-message-with-typo",
        "messages": ["Oii, qria agnda uma consulta"],
        "expected": [["nome completo"]],
    },
    {
        "id": "02-name-then-intent-with-natural-language",
        "messages": ["Oi", "me chamo ana paula"],
        "expected": [["nome completo"], ["agendar", "remarcar", "cancelar"]],
    },
    {
        "id": "03-private-plan-and-period-with-typos",
        "seed_patient": {"name": "Mariana Lima", "plan": ""},
        "messages": ["qro marca consulta", "particlar", "manhaa"],
        "expected": [
            ["convenio/plano"],
            ["qual periodo"],
            ["08/04/2026", "08:00", "08:15"],
        ],
    },
    {
        "id": "04-specific-date-slots-with-shortened-period",
        "seed_patient": {"name": "Carlos Souza", "plan": "Amil Dental"},
        "messages": ["quero agendar consulta", "10/04 de tard"],
        "expected": [
            ["qual periodo"],
            ["10/04/2026", "13:00", "13:15"],
        ],
    },
    {
        "id": "05-plan-name-with-typo-is-accepted",
        "messages": ["Oi", "sou Juliana Castro", "quero agendar", "bradesco dentl", "de noite"],
        "expected": [
            ["nome completo"],
            ["agendar", "remarcar", "cancelar"],
            ["convenio/plano"],
            ["qual periodo"],
            ["08/04/2026", "19:00", "19:15"],
        ],
    },
    {
        "id": "06-invalid-plan-then-correction-keeps-context",
        "messages": ["Oi", "meu nome e Pedro Henrique", "agendar consulta", "plano mega smile", "amil dental", "tarde"],
        "expected": [
            ["nome completo"],
            ["agendar", "remarcar", "cancelar"],
            ["convenio/plano"],
            ["convenios que atendemos"],
            ["qual periodo"],
            ["08/04/2026", "13:00", "13:15"],
        ],
    },
    {
        "id": "07-query-with-typo-finds-upcoming-appointment",
        "seed_patient": {"name": "Bianca Melo", "plan": "Particular"},
        "messages": ["quand eh minha proxma consulta?"],
        "expected": [["09/04/2026", "08:00"]],
        "events": [
            {
                "id": "evt-qry",
                "start": {"dateTime": "2026-04-09T08:00:00-03:00"},
                "summary": "Bianca Melo - 11999999999",
            }
        ],
    },
    {
        "id": "08-cancel-with-typos-and-affirmative-confirmation",
        "seed_patient": {"name": "Fernanda Rocha", "plan": "Amil Dental"},
        "messages": ["precso cancela minha cnsulta", "ssim"],
        "expected": [
            ["deseja realmente cancelar"],
            ["cancelada com sucesso"],
        ],
        "events": [
            {
                "id": "evt-cancel",
                "start": {"dateTime": "2026-04-09T09:00:00-03:00"},
                "summary": "Fernanda Rocha - 11999999999",
            }
        ],
    },
    {
        "id": "09-cancel-with-negative-response-keeps-appointment",
        "seed_patient": {"name": "Rafaela Nunes", "plan": "Amil Dental"},
        "messages": ["quero descmarcar minha consulta", "naum"],
        "expected": [
            ["deseja realmente cancelar"],
            ["mantive sua consulta"],
        ],
        "events": [
            {
                "id": "evt-keep",
                "start": {"dateTime": "2026-04-10T11:00:00-03:00"},
                "summary": "Rafaela Nunes - 11999999999",
            }
        ],
    },
    {
        "id": "10-reschedule-with-human-phrasing-keeps-context",
        "seed_patient": {"name": "Patricia Alves", "plan": "Particular"},
        "messages": ["precso remarca meu horario", "noit"],
        "expected": [
            ["qual periodo"],
            ["08/04/2026", "19:00", "19:15"],
        ],
        "events": [
            {
                "id": "evt-rebook",
                "start": {"dateTime": "2026-04-11T10:00:00-03:00"},
                "summary": "Patricia Alves - 11999999999",
            }
        ],
        "state_assertions": {"reschedule_event_id": "evt-rebook"},
    },
]


class TestConversationContextValidation:
    """Valida 10 conversas realistas. A bateria so esta aprovada se os 10 cenarios passarem."""

    def setup_method(self):
        self.db_path = Path("./data/test_context_validation.db")
        os.environ["DATABASE_PATH"] = str(self.db_path)

        from src.infrastructure.persistence.connection import close_db

        close_db()

    def teardown_method(self):
        from src.infrastructure.persistence.connection import close_db

        close_db()
        self.db_path.unlink(missing_ok=True)

    @staticmethod
    def _build_workflow(monkeypatch, events):
        from src.application.services.conversation_workflow_service import ConversationWorkflowService

        workflow = ConversationWorkflowService()

        monkeypatch.setattr(
            workflow,
            "_find_next_available_slots",
            lambda period: {
                "manha": [_slot(8, 8, 0), _slot(8, 8, 15)],
                "tarde": [_slot(8, 13, 0), _slot(8, 13, 15)],
                "noite": [_slot(8, 19, 0), _slot(8, 19, 15)],
            }.get(period, []),
        )

        monkeypatch.setattr(
            workflow.calendar,
            "get_available_slots",
            lambda target_date, period=None: {
                ("10/04/2026", "tarde"): [_slot(10, 13, 0), _slot(10, 13, 15)],
                ("10/04/2026", "manha"): [_slot(10, 8, 0), _slot(10, 8, 15)],
                ("10/04/2026", "noite"): [_slot(10, 19, 0), _slot(10, 19, 15)],
            }.get((target_date.strftime("%d/%m/%Y"), period), []),
        )

        monkeypatch.setattr(
            workflow.calendar,
            "find_appointments_by_phone",
            lambda phone: events or [],
        )
        monkeypatch.setattr(workflow.calendar, "cancel_appointment", lambda event_id: True)
        return workflow

    @pytest.mark.parametrize("case", CONTEXT_CASES, ids=[case["id"] for case in CONTEXT_CASES])
    def test_context_suite_has_10_human_conversations(self, monkeypatch, case):
        from src.infrastructure.persistence.connection import init_db
        from src.application.services.conversation_state_service import ConversationStateService
        from src.application.services.patient_service import PatientService

        init_db()

        seed_patient = case.get("seed_patient")
        if seed_patient:
            PatientService.upsert(
                "5511999999999",
                seed_patient["name"],
                seed_patient.get("plan") or None,
            )

        workflow = self._build_workflow(monkeypatch, case.get("events"))

        responses = []
        for index, message in enumerate(case["messages"]):
            responses.append(
                workflow.process_message(
                    patient_phone="5511999999999",
                    patient_message=message,
                    patient_name="",
                    is_first_message=index == 0,
                )
            )

        for response, expected_tokens in zip(responses, case["expected"]):
            normalized_response = _normalize_text(response)
            for token in expected_tokens:
                assert _normalize_text(token) in normalized_response

        state_assertions = case.get("state_assertions", {})
        if state_assertions:
            state = ConversationStateService.get("5511999999999")
            for field_name, expected_value in state_assertions.items():
                assert getattr(state, field_name) == expected_value

    def test_context_suite_has_exactly_10_cases(self):
        assert len(CONTEXT_CASES) == 10
