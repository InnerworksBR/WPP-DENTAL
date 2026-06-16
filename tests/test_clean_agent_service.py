"""Testes do motor de conversa CleanAgentService.

Cobre:
- Resiliencia (Impl 001): AG-01 (timeout/retry LLM), AG-06 (erro de tool mascarado).
- Comportamental (Impl 002, RF-004): escolha de tool, rastreamento de slots, bloqueio
  de slot nao ofertado, resposta direta sem tool, tool de encaminhamento.
- Funcoes puras (Impl 002, T-010): _parse_offered_slots, _is_offered_slot,
  _apply_state_slot_filters.
- Regressao (Impl 002, T-011): estado limpo apos agendamento, anti-loop, tool
  inexistente, nome/plano ausente, isolamento de estado por telefone.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


# ── Helpers de mock ───────────────────────────────────────────────────────────


class _FakeLLMBase:
    """Substitui ChatOpenAI: registra kwargs e devolve a si mesmo em bind_tools."""

    def __init__(self, **kwargs):
        type(self).captured = dict(kwargs)

    def bind_tools(self, tools):
        return self


def _make_service_with_llm(monkeypatch, fake_llm_cls):
    """Instancia CleanAgentService com ChatOpenAI substituido pelo fake."""
    from src.application.services import clean_agent_service
    monkeypatch.setattr(clean_agent_service, "ChatOpenAI", fake_llm_cls)
    return clean_agent_service.CleanAgentService()


# ── Fixture de DB ─────────────────────────────────────────────────────────────


class _DBMixin:
    def setup_method(self):
        self.db_path = Path("./data/test_clean_agent.db")
        os.environ["DATABASE_PATH"] = str(self.db_path)
        os.environ["OPENAI_API_KEY"] = "test-key"
        from src.infrastructure.persistence.connection import close_db, init_db
        close_db()
        self.db_path.unlink(missing_ok=True)
        init_db()

    def teardown_method(self):
        from src.infrastructure.persistence.connection import close_db
        close_db()
        self.db_path.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Bloco 1 — Resiliencia (Impl 001, CA-001/CA-002/CA-006)
# ─────────────────────────────────────────────────────────────────────────────


class TestCleanAgentResilience(_DBMixin):
    def test_llm_client_configured_with_timeout_and_max_tokens(self, monkeypatch):
        """CA-001: ChatOpenAI criado com request_timeout (20-30s) e max_tokens."""
        from src.application.services import clean_agent_service

        captured = {}

        class FakeLLM:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def bind_tools(self, tools):
                return self

        monkeypatch.setattr(clean_agent_service, "ChatOpenAI", FakeLLM)

        clean_agent_service.CleanAgentService()

        assert "request_timeout" in captured
        assert 20 <= float(captured["request_timeout"]) <= 30
        assert captured.get("max_tokens")
        assert captured.get("temperature") == 0

    def test_run_loop_returns_friendly_message_on_llm_timeout(self, monkeypatch):
        """CA-002: timeout do LLM -> retry curto e mensagem 'tente novamente', sem excecao."""
        from src.application.services import clean_agent_service
        from openai import APITimeoutError

        calls = {"n": 0}

        class FakeLLM(_FakeLLMBase):
            def invoke(self, messages):
                calls["n"] += 1
                raise APITimeoutError(request=httpx.Request("POST", "https://api.openai.com"))

        monkeypatch.setattr(clean_agent_service, "ChatOpenAI", FakeLLM)
        monkeypatch.setattr(clean_agent_service.time, "sleep", lambda *a, **k: None)

        svc = clean_agent_service.CleanAgentService()
        out = svc._run_loop([], "5511999999999")

        assert calls["n"] == clean_agent_service._LLM_RETRY_ATTEMPTS
        assert out == clean_agent_service._LLM_BUSY_MESSAGE
        assert "instantes" in out.lower()

    def test_run_loop_masks_tool_exception(self, monkeypatch):
        """CA-006: excecao de tool -> ToolMessage seguro, sem stack trace nem detalhe tecnico."""
        from src.application.services import clean_agent_service

        responses = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "consultar_agendamento",
                        "args": {"patient_phone": "5511999999999"},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(content="Resposta final ao paciente"),
        ]
        seen_tool_messages = []

        class FakeLLM(_FakeLLMBase):
            def invoke(self, messages):
                for message in messages:
                    if message.__class__.__name__ == "ToolMessage":
                        seen_tool_messages.append(str(message.content))
                return responses.pop(0)

        monkeypatch.setattr(clean_agent_service, "ChatOpenAI", FakeLLM)

        svc = clean_agent_service.CleanAgentService()

        class BoomTool:
            def invoke(self, args):
                raise RuntimeError("Google HttpError 500 detalhe interno secreto")

        svc._tool_map["consultar_agendamento"] = BoomTool()

        out = svc._run_loop([], "5511999999999")

        assert out == "Resposta final ao paciente"
        assert seen_tool_messages, "esperava ao menos um ToolMessage capturado"
        joined = " ".join(seen_tool_messages)
        assert joined.startswith("Erro:") or "Erro:" in joined
        assert "secreto" not in joined
        assert "HttpError" not in joined


# ─────────────────────────────────────────────────────────────────────────────
# Bloco 2 — Funcoes puras (Impl 002, T-010)
# ─────────────────────────────────────────────────────────────────────────────


class TestCleanAgentPureFunctions:
    """Testes unitarios das funcoes puras — nenhum I/O, nenhum mock."""

    def _state(self, offered_date="", offered_times=None, rejected_slots=None,
               excluded_dates=None, earliest_time="", requested_weekday="",
               requested_period=""):
        from src.application.services.conversation_state_service import ConversationState
        return ConversationState(
            offered_date=offered_date,
            offered_times=offered_times or [],
            rejected_slots=rejected_slots or [],
            excluded_dates=excluded_dates or [],
            earliest_time=earliest_time,
            requested_weekday=requested_weekday,
            requested_period=requested_period,
        )

    # _parse_offered_slots

    def test_parse_slots_extrai_data_e_dois_horarios(self):
        from src.application.services.clean_agent_service import _parse_offered_slots
        result = _parse_offered_slots(
            "Encontrei o proximo dia com horarios disponiveis\n"
            "terca-feira, 15/06/2026:\n"
            "  1. 15/06/2026 08:00\n"
            "  2. 15/06/2026 14:00\n"
        )
        assert result is not None
        date, times = result
        assert date == "15/06/2026"
        assert "08:00" in times
        assert "14:00" in times

    def test_parse_slots_retorna_none_sem_data(self):
        from src.application.services.clean_agent_service import _parse_offered_slots
        assert _parse_offered_slots("Sem data no texto") is None

    def test_parse_slots_retorna_none_sem_horario(self):
        from src.application.services.clean_agent_service import _parse_offered_slots
        assert _parse_offered_slots("Apenas a data 15/06/2026 sem horario") is None

    # _is_offered_slot

    def test_is_offered_true_para_slot_correto(self):
        from src.application.services.clean_agent_service import _is_offered_slot
        state = self._state(offered_date="15/06/2026", offered_times=["08:00", "14:00"])
        assert _is_offered_slot("15/06/2026 08:00", state) is True
        assert _is_offered_slot("15/06/2026 14:00", state) is True

    def test_is_offered_false_para_horario_nao_ofertado(self):
        from src.application.services.clean_agent_service import _is_offered_slot
        state = self._state(offered_date="15/06/2026", offered_times=["08:00", "14:00"])
        assert _is_offered_slot("15/06/2026 09:00", state) is False

    def test_is_offered_false_para_data_diferente(self):
        from src.application.services.clean_agent_service import _is_offered_slot
        state = self._state(offered_date="15/06/2026", offered_times=["08:00"])
        assert _is_offered_slot("16/06/2026 08:00", state) is False

    def test_is_offered_false_sem_oferta_previa(self):
        """AG-03: sem offered_date/offered_times o slot deve ser negado (fail-closed)."""
        from src.application.services.clean_agent_service import _is_offered_slot
        state = self._state()
        assert _is_offered_slot("15/06/2026 08:00", state) is False

    def test_is_offered_false_para_slot_rejeitado(self):
        from src.application.services.clean_agent_service import _is_offered_slot
        state = self._state(
            offered_date="15/06/2026",
            offered_times=["08:00"],
            rejected_slots=["15/06/2026 08:00"],
        )
        assert _is_offered_slot("15/06/2026 08:00", state) is False

    def test_is_offered_false_para_data_excluida(self):
        from src.application.services.clean_agent_service import _is_offered_slot
        state = self._state(
            offered_date="15/06/2026",
            offered_times=["08:00"],
            excluded_dates=["15/06/2026"],
        )
        assert _is_offered_slot("15/06/2026 08:00", state) is False

    def test_is_offered_false_para_horario_antes_de_earliest(self):
        from src.application.services.clean_agent_service import _is_offered_slot
        state = self._state(
            offered_date="15/06/2026",
            offered_times=["08:00"],
            earliest_time="13:00",
        )
        assert _is_offered_slot("15/06/2026 08:00", state) is False

    # _apply_state_slot_filters

    def test_apply_filters_injeta_periodo_do_estado(self):
        from src.application.services.clean_agent_service import _apply_state_slot_filters
        state = self._state(requested_period="manha")
        result = _apply_state_slot_filters({}, state)
        assert result.get("period") == "manha"

    def test_apply_filters_nao_sobreescreve_campo_existente(self):
        from src.application.services.clean_agent_service import _apply_state_slot_filters
        state = self._state(requested_period="manha")
        result = _apply_state_slot_filters({"period": "tarde"}, state)
        assert result.get("period") == "tarde"

    def test_apply_filters_injeta_earliest_time(self):
        from src.application.services.clean_agent_service import _apply_state_slot_filters
        state = self._state(earliest_time="13:00")
        result = _apply_state_slot_filters({}, state)
        assert result.get("earliest_time") == "13:00"

    def test_apply_filters_sem_estado_nao_altera_args(self):
        from src.application.services.clean_agent_service import _apply_state_slot_filters
        state = self._state()
        result = _apply_state_slot_filters({"date": "15/06/2026"}, state)
        assert result.get("date") == "15/06/2026"
        assert "period" not in result
        assert "earliest_time" not in result


# ─────────────────────────────────────────────────────────────────────────────
# Bloco 3 — Comportamental (Impl 002, RF-004)
# ─────────────────────────────────────────────────────────────────────────────


class TestCleanAgentBehavior(_DBMixin):
    """Testes comportamentais do motor — LLM e tools de calendario mockados."""

    def _svc_with_responses(self, monkeypatch, responses: list):
        """Helper: instancia CleanAgentService com LLM retornando 'responses' em sequencia."""
        from src.application.services import clean_agent_service
        seq = list(responses)

        class FakeLLM(_FakeLLMBase):
            def invoke(self, messages):
                return seq.pop(0)

        monkeypatch.setattr(clean_agent_service, "ChatOpenAI", FakeLLM)
        return clean_agent_service.CleanAgentService()

    # RF-004a: Tool selection — buscar_paciente chamada e processada

    def test_buscar_paciente_e_chamada_e_resultado_passado_ao_llm(self, monkeypatch):
        """RF-004a: LLM chama buscar_paciente; tool executa; resultado chega ao proximo invoke."""
        PHONE = "5511111110001"
        seen_tool_content = []

        from src.application.services import clean_agent_service
        seq = [
            AIMessage(
                content="",
                tool_calls=[{"name": "buscar_paciente", "args": {"phone": PHONE}, "id": "c1"}],
            ),
            AIMessage(content="Qual seu nome completo?"),
        ]

        class FakeLLM(_FakeLLMBase):
            def invoke(self, messages):
                for m in messages:
                    if m.__class__.__name__ == "ToolMessage":
                        seen_tool_content.append(m.content)
                return seq.pop(0)

        monkeypatch.setattr(clean_agent_service, "ChatOpenAI", FakeLLM)
        svc = clean_agent_service.CleanAgentService()

        out = svc._run_loop(
            [SystemMessage(content="sys"), HumanMessage(content="Oi")], PHONE
        )

        assert out == "Qual seu nome completo?"
        assert seen_tool_content, "LLM deve receber ToolMessage de buscar_paciente"
        assert "paciente" in seen_tool_content[0].lower()

    # RF-004b: Slot tracking — offered_date/times salvos no estado

    def test_slots_ofertados_sao_salvos_no_estado(self, monkeypatch):
        """RF-004b: apos buscar slots, estado.offered_date e offered_times sao persistidos."""
        PHONE = "5511111110002"
        SLOT_RESPONSE = (
            "Encontrei o proximo dia com horarios disponiveis\n"
            "terca-feira, 17/06/2026 - periodo da manha:\n"
            "  1. 17/06/2026 08:00\n"
            "  2. 17/06/2026 08:15\n"
        )

        svc = self._svc_with_responses(monkeypatch, [
            AIMessage(
                content="",
                tool_calls=[{"name": "buscar_proximo_dia_disponivel", "args": {"period": "manha"}, "id": "c1"}],
            ),
            AIMessage(content="Aqui estao os horarios disponiveis."),
        ])

        class FakeSlotTool:
            def invoke(self, args):
                return SLOT_RESPONSE

        svc._tool_map["buscar_proximo_dia_disponivel"] = FakeSlotTool()

        svc._run_loop(
            [SystemMessage(content="sys"), HumanMessage(content="Quero horario de manha")], PHONE
        )

        from src.application.services.conversation_state_service import ConversationStateService
        state = ConversationStateService.get(PHONE)
        assert state.offered_date == "17/06/2026"
        assert "08:00" in state.offered_times
        assert "08:15" in state.offered_times

    # RF-004c: Resposta direta (sem tool) — LLM pode retornar sem chamar ferramenta

    def test_resposta_sem_tool_retorna_conteudo_direto(self, monkeypatch):
        """RF-004c: LLM pode responder sem chamar nenhuma tool (ex.: recusa de procedimento)."""
        PHONE = "5511111110003"
        svc = self._svc_with_responses(monkeypatch, [
            AIMessage(content="Nao realizamos canal em molar. Posso ajudar com outra coisa?"),
        ])

        out = svc._run_loop(
            [SystemMessage(content="sys"), HumanMessage(content="Fazem canal de molar?")], PHONE
        )

        assert "nao realizamos canal" in out.lower()

    # RF-004d: Referral — verificar_convenio com ENCAMINHAMENTO

    def test_verificar_convenio_encaminhamento_chega_ao_llm(self, monkeypatch):
        """RF-004d: verificar_convenio retorna ENCAMINHAMENTO; LLM recebe e responde."""
        PHONE = "5511111110004"
        seen_tool = []

        from src.application.services import clean_agent_service
        seq = [
            AIMessage(
                content="",
                tool_calls=[{"name": "verificar_convenio", "args": {"plan_name": "Caixa de Saude"}, "id": "c1"}],
            ),
            AIMessage(content="Esse convenio e atendido pela Dra. Tarcilia, nossa parceira."),
        ]

        class FakeLLM(_FakeLLMBase):
            def invoke(self, messages):
                for m in messages:
                    if m.__class__.__name__ == "ToolMessage":
                        seen_tool.append(m.content)
                return seq.pop(0)

        monkeypatch.setattr(clean_agent_service, "ChatOpenAI", FakeLLM)
        svc = clean_agent_service.CleanAgentService()

        class FakeEncaminhamentoTool:
            def invoke(self, args):
                return "ENCAMINHAMENTO: Caixa de Saude -> Dra. Tarcilia"

        svc._tool_map["verificar_convenio"] = FakeEncaminhamentoTool()

        out = svc._run_loop(
            [SystemMessage(content="sys"), HumanMessage(content="Tenho Caixa de Saude")], PHONE
        )

        assert "tarcilia" in out.lower()
        assert seen_tool, "LLM deve receber resultado de verificar_convenio"
        assert "ENCAMINHAMENTO" in seen_tool[0]

    # RF-004e: Slot validation — criar_agendamento fora da oferta e bloqueado

    def test_criar_agendamento_fora_de_oferta_e_bloqueado(self, monkeypatch):
        """RF-004e: criar_agendamento com datetime fora de offered_times injeta erro e nao executa."""
        PHONE = "5511111110005"

        from src.application.services.conversation_state_service import (
            ConversationState, ConversationStateService,
        )
        ConversationStateService.save(
            PHONE,
            ConversationState(offered_date="17/06/2026", offered_times=["08:00", "08:15"]),
        )

        create_called = {"n": 0}
        seen_tool_error = []

        from src.application.services import clean_agent_service
        seq = [
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "criar_agendamento",
                    "args": {
                        "datetime_str": "17/06/2026 09:00",
                        "patient_name": "Maria Silva",
                        "patient_phone": PHONE,
                    },
                    "id": "c1",
                }],
            ),
            AIMessage(content="Esse horario nao estava disponivel. Escolha um dos ofertados."),
        ]

        class FakeLLM(_FakeLLMBase):
            def invoke(self, messages):
                for m in messages:
                    if m.__class__.__name__ == "ToolMessage" and "ofertados" in m.content:
                        seen_tool_error.append(m.content)
                return seq.pop(0)

        monkeypatch.setattr(clean_agent_service, "ChatOpenAI", FakeLLM)
        svc = clean_agent_service.CleanAgentService()

        class FakeCreateTool:
            def invoke(self, args):
                create_called["n"] += 1
                return "agendada com sucesso"

        svc._tool_map["criar_agendamento"] = FakeCreateTool()

        out = svc._run_loop(
            [SystemMessage(content="sys"), HumanMessage(content="Quero o de 09:00")], PHONE
        )

        assert create_called["n"] == 0, "criar_agendamento NAO deve executar para slot nao ofertado"
        assert seen_tool_error, "deve injetar ToolMessage de erro ao LLM"
        assert "ofertados" in seen_tool_error[0].lower()
        assert "nao estava" in out.lower() or "disponiv" in out.lower()

    # RF-004e extra: nome ausente tambem bloqueia criar_agendamento

    def test_criar_agendamento_com_nome_vazio_e_bloqueado(self, monkeypatch):
        """RF-004e extra: patient_name vazio bloqueia criar_agendamento."""
        PHONE = "5511111110006"

        from src.application.services.conversation_state_service import (
            ConversationState, ConversationStateService,
        )
        ConversationStateService.save(
            PHONE,
            ConversationState(
                offered_date="17/06/2026",
                offered_times=["08:00"],
                plan_name="OdontoPrev",
            ),
        )

        create_called = {"n": 0}
        seen_name_error = []

        from src.application.services import clean_agent_service
        seq = [
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "criar_agendamento",
                    "args": {
                        "datetime_str": "17/06/2026 08:00",
                        "patient_name": "",
                        "patient_phone": PHONE,
                    },
                    "id": "c1",
                }],
            ),
            AIMessage(content="Preciso do seu nome completo antes de finalizar."),
        ]

        class FakeLLM(_FakeLLMBase):
            def invoke(self, messages):
                for m in messages:
                    if m.__class__.__name__ == "ToolMessage" and "nome" in m.content.lower():
                        seen_name_error.append(m.content)
                return seq.pop(0)

        monkeypatch.setattr(clean_agent_service, "ChatOpenAI", FakeLLM)
        svc = clean_agent_service.CleanAgentService()

        class FakeCreateTool:
            def invoke(self, args):
                create_called["n"] += 1
                return "agendada com sucesso"

        svc._tool_map["criar_agendamento"] = FakeCreateTool()

        svc._run_loop(
            [SystemMessage(content="sys"), HumanMessage(content="Pode marcar!")], PHONE
        )

        assert create_called["n"] == 0, "criar_agendamento nao deve executar sem nome"
        assert seen_name_error, "deve injetar ToolMessage de erro de nome"


# ─────────────────────────────────────────────────────────────────────────────
# Bloco 4 — Casos de borda do _run_loop (Impl 002, T-010)
# ─────────────────────────────────────────────────────────────────────────────


class TestCleanAgentEdgeCases(_DBMixin):
    """Casos de borda: anti-loop, tool inexistente, resposta vazia."""

    def _svc(self, monkeypatch, responses: list):
        from src.application.services import clean_agent_service
        seq = list(responses)

        class FakeLLM(_FakeLLMBase):
            def invoke(self, messages):
                return seq.pop(0)

        monkeypatch.setattr(clean_agent_service, "ChatOpenAI", FakeLLM)
        return clean_agent_service.CleanAgentService()

    def test_anti_loop_mesma_tool_mesmo_args_retorna_mensagem_interna(self, monkeypatch):
        """Anti-loop (l.367): mesma tool com mesmos args repetida -> mensagem de dificuldade."""
        PHONE = "5511111120001"

        from src.application.services import clean_agent_service

        call_count = {"n": 0}

        class FakeLLM(_FakeLLMBase):
            def invoke(self, messages):
                # Sempre retorna o mesmo tool call com os mesmos args
                call_count["n"] += 1
                return AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "buscar_paciente",
                        "args": {"phone": PHONE},
                        "id": f"c{call_count['n']}",
                    }],
                )

        monkeypatch.setattr(clean_agent_service, "ChatOpenAI", FakeLLM)
        svc = clean_agent_service.CleanAgentService()

        class FakeTool:
            def invoke(self, args):
                return "Paciente nao encontrado no sistema."

        svc._tool_map["buscar_paciente"] = FakeTool()

        out = svc._run_loop([SystemMessage(content="sys"), HumanMessage(content="Oi")], PHONE)

        assert "dificuldade interna" in out.lower()

    def test_tool_inexistente_retorna_mensagem_de_erro(self, monkeypatch):
        """Tool com nome desconhecido (l.404) -> 'ferramenta nao encontrada'."""
        PHONE = "5511111120002"
        svc = self._svc(monkeypatch, [
            AIMessage(
                content="",
                tool_calls=[{"name": "ferramenta_fantasma", "args": {}, "id": "c1"}],
            ),
            AIMessage(content="Nao consegui executar isso."),
        ])

        out = svc._run_loop(
            [SystemMessage(content="sys"), HumanMessage(content="Teste")], PHONE
        )

        assert out == "Nao consegui executar isso."

    def test_process_message_raise_on_empty_response(self, monkeypatch):
        """_run_loop retornando string vazia -> process_message levanta RuntimeError (l.485-486)."""
        PHONE = "5511111120003"

        from src.application.services import clean_agent_service

        class FakeLLM(_FakeLLMBase):
            def invoke(self, messages):
                return AIMessage(content="")

        monkeypatch.setattr(clean_agent_service, "ChatOpenAI", FakeLLM)

        class FakePatientService:
            @staticmethod
            def find_by_phone(phone):
                return None

        monkeypatch.setattr(clean_agent_service, "PatientService", FakePatientService)

        svc = clean_agent_service.CleanAgentService()

        with pytest.raises(RuntimeError, match="CleanAgent"):
            svc.process_message(
                patient_phone=PHONE,
                patient_message="Oi",
            )

    def test_process_message_happy_path_nao_levanta(self, monkeypatch):
        """Caminho feliz (resposta nao vazia) nao levanta RuntimeError."""
        PHONE = "5511111120004"

        from src.application.services import clean_agent_service

        class FakeLLM(_FakeLLMBase):
            def invoke(self, messages):
                return AIMessage(content="Ola! Como posso ajudar?")

        monkeypatch.setattr(clean_agent_service, "ChatOpenAI", FakeLLM)

        class FakePatientService:
            @staticmethod
            def find_by_phone(phone):
                return None

        monkeypatch.setattr(clean_agent_service, "PatientService", FakePatientService)

        svc = clean_agent_service.CleanAgentService()
        out = svc.process_message(patient_phone=PHONE, patient_message="Oi")

        assert out == "Ola! Como posso ajudar?"


# ─────────────────────────────────────────────────────────────────────────────
# Bloco 5 — Regressao bugs #0002..#0005 (Impl 002, T-011)
# ─────────────────────────────────────────────────────────────────────────────


class TestCleanAgentRegression(_DBMixin):
    """Testes de regressao para bugs corrigidos nos commits #0002..#0005."""

    def _svc(self, monkeypatch, responses: list):
        from src.application.services import clean_agent_service
        seq = list(responses)

        class FakeLLM(_FakeLLMBase):
            def invoke(self, messages):
                return seq.pop(0)

        monkeypatch.setattr(clean_agent_service, "ChatOpenAI", FakeLLM)
        return clean_agent_service.CleanAgentService()

    def test_offered_state_limpo_apos_agendamento_com_sucesso(self, monkeypatch):
        """Bug #0002/#0003: offered_date/times sao zerados apos criar_agendamento com sucesso.

        Garante que um slot ja confirmado nao fica disponivel para ser re-ofertado
        em mensagens subsequentes, evitando double-booking.
        """
        PHONE = "5511111130001"

        from src.application.services.conversation_state_service import (
            ConversationState, ConversationStateService,
        )
        ConversationStateService.save(
            PHONE,
            ConversationState(
                offered_date="17/06/2026",
                offered_times=["08:00"],
                plan_name="OdontoPrev",
            ),
        )

        svc = self._svc(monkeypatch, [
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "criar_agendamento",
                    "args": {
                        "datetime_str": "17/06/2026 08:00",
                        "patient_name": "Maria Silva",
                        "patient_phone": PHONE,
                    },
                    "id": "c1",
                }],
            ),
            AIMessage(content="Consulta agendada com sucesso para 17/06/2026 as 08:00!"),
        ])

        class FakeCreateTool:
            def invoke(self, args):
                return "Consulta agendada com sucesso! ID: evt-001"

        svc._tool_map["criar_agendamento"] = FakeCreateTool()

        svc._run_loop(
            [SystemMessage(content="sys"), HumanMessage(content="Confirmo o 08:00")], PHONE
        )

        state = ConversationStateService.get(PHONE)
        assert state.offered_date == "", "offered_date deve ser limpo apos agendamento"
        assert state.offered_times == [], "offered_times deve ser limpo apos agendamento"

    def test_slot_nao_ofertado_nunca_cria_evento(self, monkeypatch):
        """Bug #0003: slot nao previamente ofertado deve ser bloqueado pelo codigo, nao pelo LLM."""
        PHONE = "5511111130002"

        from src.application.services.conversation_state_service import (
            ConversationState, ConversationStateService,
        )
        ConversationStateService.save(
            PHONE,
            ConversationState(offered_date="17/06/2026", offered_times=["08:00"]),
        )

        create_calls = []

        from src.application.services import clean_agent_service

        seq = [
            # LLM tenta agendar horario nao ofertado
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "criar_agendamento",
                    "args": {
                        "datetime_str": "17/06/2026 10:00",
                        "patient_name": "Joao",
                        "patient_phone": PHONE,
                    },
                    "id": "c1",
                }],
            ),
            AIMessage(content="Nao consigo agendar esse horario."),
        ]

        class FakeLLM(_FakeLLMBase):
            def invoke(self, messages):
                return seq.pop(0)

        monkeypatch.setattr(clean_agent_service, "ChatOpenAI", FakeLLM)
        svc = clean_agent_service.CleanAgentService()

        class FakeCreateTool:
            def invoke(self, args):
                create_calls.append(args)
                return "agendada com sucesso"

        svc._tool_map["criar_agendamento"] = FakeCreateTool()

        svc._run_loop([SystemMessage(content="sys"), HumanMessage(content="Quero 10:00")], PHONE)

        assert len(create_calls) == 0, "tool criar_agendamento NAO deve executar"

    def test_estado_isolado_por_telefone(self, monkeypatch):
        """Bug #0004 (hand-off): estado de conversa de um telefone nao afeta o de outro.

        Criamos estado para PHONE_A e verificamos que PHONE_B comeca sem offered_date.
        """
        PHONE_A = "5511111130003"
        PHONE_B = "5511111130004"

        from src.application.services.conversation_state_service import (
            ConversationState, ConversationStateService,
        )
        ConversationStateService.save(
            PHONE_A,
            ConversationState(offered_date="17/06/2026", offered_times=["08:00"]),
        )

        state_b = ConversationStateService.get(PHONE_B)
        assert state_b.offered_date == ""
        assert state_b.offered_times == []

    def test_slots_ofertados_sao_especificos_ao_telefone(self, monkeypatch):
        """Bug #0005 (LID): offered_times salvos para PHONE_A nao aparecem para PHONE_B.

        Garante que o rastreamento de slot nao vaza entre conversas diferentes.
        """
        PHONE_A = "5511111130005"
        PHONE_B = "5511111130006"

        SLOT_RESPONSE = (
            "Encontrei o proximo dia com horarios disponiveis\n"
            "terca-feira, 17/06/2026 - periodo da manha:\n"
            "  1. 17/06/2026 08:00\n"
            "  2. 17/06/2026 08:15\n"
        )

        from src.application.services import clean_agent_service

        seq_a = [
            AIMessage(
                content="",
                tool_calls=[{"name": "buscar_proximo_dia_disponivel", "args": {"period": "manha"}, "id": "c1"}],
            ),
            AIMessage(content="Aqui estao os horarios."),
        ]

        class FakeLLM_A(_FakeLLMBase):
            def invoke(self, messages):
                return seq_a.pop(0)

        monkeypatch.setattr(clean_agent_service, "ChatOpenAI", FakeLLM_A)
        svc_a = clean_agent_service.CleanAgentService()

        class FakeSlotTool:
            def invoke(self, args):
                return SLOT_RESPONSE

        svc_a._tool_map["buscar_proximo_dia_disponivel"] = FakeSlotTool()
        svc_a._run_loop([SystemMessage(content="sys"), HumanMessage(content="Horario")], PHONE_A)

        from src.application.services.conversation_state_service import ConversationStateService
        state_a = ConversationStateService.get(PHONE_A)
        state_b = ConversationStateService.get(PHONE_B)

        assert state_a.offered_date == "17/06/2026"
        assert state_b.offered_date == "", "offered_date de A nao deve vazar para B"
        assert state_b.offered_times == []
