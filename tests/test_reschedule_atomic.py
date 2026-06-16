"""Testes de Remarcacao Atomica e Idempotencia de Criacao (Impl 006).

Cobre:
- CA-001 / CA-007: guarda no _run_loop bloqueia criar_agendamento quando intent=reschedule
- CA-004: create_appointment_if_available e idempotente por (telefone, slot)
- CA-004 (regressao): sem evento existente, criacao normal ocorre
- CA-004 (degrada): excecao na busca de idempotencia nao impede criacao
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")


# ── Fixtures ──────────────────────────────────────────────────────────────────


class _DBMixin:
    def setup_method(self):
        self.db_path = Path("./data/test_reschedule_atomic.db")
        os.environ["DATABASE_PATH"] = str(self.db_path)
        os.environ["OPENAI_API_KEY"] = "test-key"
        from src.infrastructure.persistence.connection import close_db, init_db
        close_db()
        init_db()

    def teardown_method(self):
        from src.infrastructure.persistence.connection import close_db
        close_db()
        self.db_path.unlink(missing_ok=True)


class _FakeLLMBase:
    def __init__(self, **kwargs):
        pass

    def bind_tools(self, tools):
        return self


# ── Bloco 1: Guarda do LLM (RF-001, CA-001, CA-007) ─────────────────────────


class TestRescheduleGuardInRunLoop(_DBMixin):
    """Garante que criar_agendamento e bloqueado no _run_loop quando intent=reschedule."""

    def _make_service(self, monkeypatch, seq):
        from src.application.services import clean_agent_service

        class FakeLLM(_FakeLLMBase):
            def invoke(self, messages):
                return seq.pop(0)

        monkeypatch.setattr(clean_agent_service, "ChatOpenAI", FakeLLM)
        return clean_agent_service.CleanAgentService()

    def test_criar_agendamento_bloqueado_quando_intent_reschedule(self, monkeypatch):
        """CA-001: LLM tenta criar_agendamento com intent=reschedule; deve ser bloqueado."""
        PHONE = "5511991000001"

        from src.application.services.conversation_state_service import (
            ConversationState, ConversationStateService,
        )
        ConversationStateService.save(
            PHONE,
            ConversationState(
                intent="reschedule",
                reschedule_event_id="evt-old",
                offered_date="20/06/2026",
                offered_times=["09:00"],
                patient_name="Maria",
                plan_name="Particular",
            ),
        )

        create_called = {"n": 0}
        seen_tool_msg = []

        seq = [
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "criar_agendamento",
                    "args": {
                        "datetime_str": "20/06/2026 09:00",
                        "patient_name": "Maria",
                        "patient_phone": PHONE,
                    },
                    "id": "tc-reschedule-guard",
                }],
            ),
            AIMessage(content="Vou aguardar a confirmacao do horario pelo fluxo correto."),
        ]

        from src.application.services import clean_agent_service

        class FakeLLM(_FakeLLMBase):
            def invoke(self, messages):
                for m in messages:
                    if m.__class__.__name__ == "ToolMessage" and "troca atomica" in m.content.lower():
                        seen_tool_msg.append(m.content)
                return seq.pop(0)

        monkeypatch.setattr(clean_agent_service, "ChatOpenAI", FakeLLM)
        svc = clean_agent_service.CleanAgentService()

        class FakeCreateTool:
            def invoke(self, args):
                create_called["n"] += 1
                return "agendada com sucesso"

        svc._tool_map["criar_agendamento"] = FakeCreateTool()

        out = svc._run_loop(
            [SystemMessage(content="sys"), HumanMessage(content="quero o de 09:00")],
            PHONE,
        )

        assert create_called["n"] == 0, "criar_agendamento NAO deve executar quando intent=reschedule"
        assert seen_tool_msg, "deve injetar ToolMessage de bloqueio ao LLM"
        assert "troca atomica" in seen_tool_msg[0].lower() or "deterministico" in seen_tool_msg[0].lower()

    def test_criar_agendamento_executa_normalmente_quando_intent_nao_e_reschedule(self, monkeypatch):
        """Regressao: intent vazio/agendamento novo nao e bloqueado."""
        PHONE = "5511991000002"

        from src.application.services.conversation_state_service import (
            ConversationState, ConversationStateService,
        )
        ConversationStateService.save(
            PHONE,
            ConversationState(
                intent="",
                offered_date="20/06/2026",
                offered_times=["10:00"],
                patient_name="Jose",
                plan_name="Particular",
            ),
        )

        create_called = {"n": 0}

        seq = [
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "criar_agendamento",
                    "args": {
                        "datetime_str": "20/06/2026 10:00",
                        "patient_name": "Jose",
                        "patient_phone": PHONE,
                    },
                    "id": "tc-new-booking",
                }],
            ),
            AIMessage(content="Consulta agendada!"),
        ]

        from src.application.services import clean_agent_service

        class FakeLLM(_FakeLLMBase):
            def invoke(self, messages):
                return seq.pop(0)

        monkeypatch.setattr(clean_agent_service, "ChatOpenAI", FakeLLM)
        svc = clean_agent_service.CleanAgentService()

        class FakeCreateTool:
            def invoke(self, args):
                create_called["n"] += 1
                return "agendada com sucesso"

        svc._tool_map["criar_agendamento"] = FakeCreateTool()

        svc._run_loop(
            [SystemMessage(content="sys"), HumanMessage(content="quero agendar")],
            PHONE,
        )

        assert create_called["n"] == 1, "criar_agendamento DEVE executar quando intent nao e reschedule"


# ── Bloco 2: Idempotencia de criacao (RF-004, CA-004) ────────────────────────


class TestCreateAppointmentIdempotency:
    """Garante que create_appointment_if_available reutiliza evento existente por (phone, slot)."""

    def _make_service(self, monkeypatch, existing_events, create_result=None):
        """Monta CalendarService com find_appointments_by_phone e create_appointment mockados."""
        from src.infrastructure.integrations import calendar_service

        # Slot futuro em Sao Paulo (segunda-feira)
        slot = datetime(2026, 6, 23, 9, 0, tzinfo=SAO_PAULO_TZ)
        new_event = {"id": "evt-new", "summary": "Maria - 5511999999999", "start": {"dateTime": slot.isoformat()}}

        class FakeGoogleService:
            def events(self):
                return self

            def list(self, **kwargs):
                return self

            def execute(self):
                return {"items": existing_events}

            def delete(self, **kwargs):
                return self

            def insert(self, **kwargs):
                return self

        svc = calendar_service.CalendarService.__new__(calendar_service.CalendarService)
        svc.config = type("C", (), {
            "get_slot_duration": lambda s: 15,
            "get_max_days_ahead": lambda s: 60,
        })()
        svc.calendar_id = "primary"

        create_calls = {"n": 0, "result": create_result or new_event}

        real_create = calendar_service.CalendarService.create_appointment

        def fake_create(self_inner, name, phone, start):
            create_calls["n"] += 1
            return create_calls["result"]

        monkeypatch.setattr(calendar_service.CalendarService, "create_appointment", fake_create)

        def fake_get_service(self_inner):
            return FakeGoogleService()

        monkeypatch.setattr(calendar_service.CalendarService, "_get_service", fake_get_service)

        return svc, slot, create_calls

    def _patch_calendar(self, monkeypatch, find_fn, create_fn=None, slot_conflicts_fn=None):
        """Helper: monkeypatcha CalendarService para testes de idempotencia."""
        from src.infrastructure.integrations import calendar_service
        monkeypatch.setattr(calendar_service.CalendarService, "find_appointments_by_phone", find_fn)
        if create_fn:
            monkeypatch.setattr(calendar_service.CalendarService, "create_appointment", create_fn)
        if slot_conflicts_fn:
            monkeypatch.setattr(calendar_service.CalendarService, "_slot_conflicts", slot_conflicts_fn)
        monkeypatch.setattr(
            calendar_service.CalendarService,
            "_is_within_business_hours",
            lambda s, start, end: True,
        )
        monkeypatch.setattr(
            calendar_service.CalendarService,
            "_get_service",
            lambda s: (_ for _ in ()).throw(RuntimeError("nao deve chamar API")),
        )

    def _make_svc(self, monkeypatch):
        from src.infrastructure.integrations import calendar_service
        svc = calendar_service.CalendarService.__new__(calendar_service.CalendarService)
        svc.config = type("C", (), {
            "get_slot_duration": lambda s: 15,
            "get_max_days_ahead": lambda s: 60,
            "get_min_business_days_ahead": lambda s: 0,
            "get_holidays": lambda s: [],
        })()
        svc.calendar_id = "primary"
        return svc

    def test_idempotent_retorna_evento_existente_quando_mesmo_telefone_e_slot(self, monkeypatch):
        """CA-004: chamada com mesmo (phone, slot) retorna evento ja existente sem criar outro."""
        PHONE = "5511999999999"
        slot_dt = datetime(2026, 6, 23, 9, 0, tzinfo=SAO_PAULO_TZ)
        existing_event = {
            "id": "evt-existing",
            "summary": "Maria - 5511999999999",
            "start": {"dateTime": slot_dt.isoformat()},
        }
        create_calls = {"n": 0}

        self._patch_calendar(
            monkeypatch,
            find_fn=lambda s, phone: [existing_event],
            create_fn=lambda s, name, phone, start: (create_calls.__setitem__("n", create_calls["n"] + 1) or {"id": "evt-new"}),
            slot_conflicts_fn=lambda s, start, end: False,
        )
        svc = self._make_svc(monkeypatch)

        result = svc.create_appointment_if_available("Maria", PHONE, slot_dt)

        assert result["id"] == "evt-existing", "deve retornar evento existente, nao criar novo"
        assert create_calls["n"] == 0, "create_appointment NAO deve ser chamado em reuso idempotente"

    def test_idempotent_cria_quando_sem_evento_existente(self, monkeypatch):
        """Regressao CA-004: sem evento existente, criacao normal ocorre."""
        PHONE = "5511999999998"
        slot_dt = datetime(2026, 6, 23, 10, 0, tzinfo=SAO_PAULO_TZ)
        new_event = {"id": "evt-new", "summary": "Pedro - 5511999999998"}
        create_calls = {"n": 0}

        def fake_create(s, name, phone, start):
            create_calls["n"] += 1
            return new_event

        self._patch_calendar(
            monkeypatch,
            find_fn=lambda s, phone: [],
            create_fn=fake_create,
            slot_conflicts_fn=lambda s, start, end: False,
        )
        svc = self._make_svc(monkeypatch)

        result = svc.create_appointment_if_available("Pedro", PHONE, slot_dt)

        assert result["id"] == "evt-new"
        assert create_calls["n"] == 1, "create_appointment DEVE ser chamado quando nao ha evento existente"

    def test_idempotent_ignora_evento_de_horario_diferente(self, monkeypatch):
        """CA-004 borda: evento existente em horario diferente nao interfere na criacao."""
        PHONE = "5511999999997"
        slot_dt = datetime(2026, 6, 23, 9, 0, tzinfo=SAO_PAULO_TZ)
        other_slot = datetime(2026, 6, 23, 10, 0, tzinfo=SAO_PAULO_TZ)
        other_event = {
            "id": "evt-other",
            "summary": "Ana - 5511999999997",
            "start": {"dateTime": other_slot.isoformat()},
        }
        new_event = {"id": "evt-created"}
        create_calls = {"n": 0}

        def fake_create(s, name, phone, start):
            create_calls["n"] += 1
            return new_event

        self._patch_calendar(
            monkeypatch,
            find_fn=lambda s, phone: [other_event],
            create_fn=fake_create,
            slot_conflicts_fn=lambda s, start, end: False,
        )
        svc = self._make_svc(monkeypatch)

        result = svc.create_appointment_if_available("Ana", PHONE, slot_dt)

        assert result["id"] == "evt-created", "deve criar novo evento quando horario e diferente"
        assert create_calls["n"] == 1

    def test_idempotent_degrada_quando_busca_lanca_excecao(self, monkeypatch):
        """CA-004 borda (CB-4): excecao na busca de idempotencia nao impede criacao normal."""
        PHONE = "5511999999996"
        slot_dt = datetime(2026, 6, 23, 11, 0, tzinfo=SAO_PAULO_TZ)
        new_event = {"id": "evt-created-after-error"}
        create_calls = {"n": 0}

        def fake_find_raises(s, phone):
            raise RuntimeError("Simulated API error in find")

        def fake_create(s, name, phone, start):
            create_calls["n"] += 1
            return new_event

        self._patch_calendar(
            monkeypatch,
            find_fn=fake_find_raises,
            create_fn=fake_create,
            slot_conflicts_fn=lambda s, start, end: False,
        )
        svc = self._make_svc(monkeypatch)

        result = svc.create_appointment_if_available("Lucia", PHONE, slot_dt)

        assert result["id"] == "evt-created-after-error"
        assert create_calls["n"] == 1, "criacao deve ocorrer mesmo quando busca de idempotencia falha"
