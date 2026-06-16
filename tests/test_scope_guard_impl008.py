"""Testes de regressao para impl 008 — Guarda de Escopo Robusto.

Cobre CA-001..CA-011 da spec e os casos de borda EB-01..EB-05.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.domain.policies.scope_guard_service import ScopeGuardService
from src.interfaces.tools.config_tool import CheckPlanTool


# ---------------------------------------------------------------------------
# UT-01 / CA-001: SC-01 — marcador seguro NAO pode sobrepor conteudo proibido
# ---------------------------------------------------------------------------

class TestResponseIsSafeOrdering:
    """SC-01: unsafe check ANTES do safe marker."""

    def test_safe_marker_plus_price_is_unsafe(self):
        """CA-001: resposta com marcador seguro + R$ deve ser barrada."""
        response = (
            "Posso te ajudar com sua consulta. "
            "O clareamento custa R$ 800."
        )
        assert ScopeGuardService.response_is_safe(response) is False

    def test_safe_marker_plus_reals_value_is_unsafe(self):
        """EB-05: preco no fim, marcador seguro no inicio — deve reprovar."""
        response = (
            "Apenas com agendamentos posso ajudar. "
            "O tratamento custa 1500 reais."
        )
        assert ScopeGuardService.response_is_safe(response) is False

    def test_safe_marker_plus_clinical_is_unsafe(self):
        """CA-001 variante: marcador seguro + recomendacao clinica deve ser barrado."""
        response = (
            "Nao posso informar valores. "
            "Recomendo que voce faca uma limpeza urgente."
        )
        assert ScopeGuardService.response_is_safe(response) is False

    def test_safe_marker_alone_is_safe(self):
        """CA-002: resposta apenas com marcador seguro (sem conteudo proibido) continua True."""
        response = "Nao posso informar valores por aqui. A doutora entrara em contato."
        assert ScopeGuardService.response_is_safe(response) is True

    def test_purely_safe_scheduling_response(self):
        """Resposta de agendamento sem conteudo proibido deve ser segura."""
        response = (
            "Posso te ajudar com sua consulta! "
            "Temos disponibilidade na proxima segunda as 14h. Confirma?"
        )
        assert ScopeGuardService.response_is_safe(response) is True


# ---------------------------------------------------------------------------
# UT-03 / CA-003: SC-02 — deteccao de plural e sinonimos de preco
# ---------------------------------------------------------------------------

class TestPricePatternsExpanded:
    """SC-02: _PRICE_PATTERNS cobre plural e sinonimos."""

    @pytest.mark.parametrize("msg,label", [
        ("qual a tabela de precos?", "tabela de precos"),
        ("quanto sai a consulta?", "quanto sai"),
        ("ta quanto a limpeza?", "ta quanto"),
        ("quero saber os valores dos procedimentos", "valores"),
        ("pode me passar o orcamento?", "orcamento"),
        ("quanto custa o implante?", "quanto custa"),
        ("qual o preco do clareamento?", "preco singular"),
        ("quero saber os precos dos servicos", "precos plural"),
    ])
    def test_detects_price_synonym(self, msg, label):
        """CA-003: mensagens de preco em varias formas devem escalar fora_do_escopo."""
        decision = ScopeGuardService.classify_patient_message(msg)
        assert decision is not None, f"Esperava escalacao para: {label!r}"
        assert decision.reason == "fora_do_escopo"


# ---------------------------------------------------------------------------
# UT-04 / CA-004: SC-03 — valores monetarios "nus" (sem R$/reais)
# ---------------------------------------------------------------------------

class TestBareMonetaryValues:
    """SC-03: response_is_safe rejeita valores nus de 3+ digitos."""

    @pytest.mark.parametrize("response", [
        "o valor da consulta fica em 350",
        "a limpeza custa 450 no consultorio",
        "sao 350,00 por sessao",
        "uns 1200 para o implante",
        "vai 500 com a carteirinha",
        "sai 300 a avaliacao",
    ])
    def test_bare_value_is_unsafe(self, response):
        """CA-004: resposta com valor nu (sem R$/reais) deve ser barrada."""
        assert ScopeGuardService.response_is_safe(response) is False

    def test_hour_not_confused_with_price(self):
        """EB-03: 'marcar para as 14' nao deve ser confundido com preco."""
        assert ScopeGuardService.response_is_safe("marcar para as 14h esta otimo") is True

    def test_two_digit_number_not_price(self):
        """Numero de 2 digitos nao dispara padrao de valor nu."""
        assert ScopeGuardService.response_is_safe("temos 15 horarios disponiveis") is True


# ---------------------------------------------------------------------------
# UT-05 / CA-005: SC-04 — nao escalar agendamentos legitimos com procedimento
# ---------------------------------------------------------------------------

class TestLegitimateSchedulingNotEscalated:
    """SC-04: agendamentos com nome de procedimento nao devem ser escalados."""

    @pytest.mark.parametrize("msg", [
        "quero marcar uma limpeza",
        "preciso agendar avaliacao de aparelho",
        "gostaria de agendar consulta para canal",
        "quero marcar clareamento pelo plano",
        "posso agendar extracao?",
        "preciso de consulta para avaliacao de implante",
        "gostaria de marcar limpeza particular",
        "agendar restauracao urgente",
        "marcar consulta de rotina com limpeza",
        "quero uma consulta para avaliar o canal",
    ])
    def test_scheduling_with_procedure_not_escalated(self, msg):
        """CA-005 / AT-02: mensagem de agendamento com procedimento nao escala."""
        decision = ScopeGuardService.classify_patient_message(msg)
        assert decision is None, f"Falso positivo para: {msg!r}"

    def test_mixed_price_and_scheduling_still_escalates(self):
        """EB-02: mensagem mista (agendar + pedir preco) deve escalar por preco."""
        decision = ScopeGuardService.classify_patient_message(
            "quero marcar, mas quanto custa a limpeza?"
        )
        assert decision is not None
        assert decision.reason == "fora_do_escopo"


# ---------------------------------------------------------------------------
# UT-06 / CA-006: SC-05 — novos sintomas clinicos
# ---------------------------------------------------------------------------

class TestExpandedClinicalPatterns:
    """SC-05: _CLINICAL_PATTERNS detecta sintomas ausentes anteriormente."""

    @pytest.mark.parametrize("msg,label", [
        ("estou com pus na gengiva", "pus"),
        ("meu dente trincou e lateja", "trincou + lateja"),
        ("tenho abscesso na boca", "abscesso"),
        ("o dente esta pulsando de dor", "pulsando"),
        ("machucou muito a gengiva", "machucou"),
        ("sinto ardencia ao comer", "ardencia"),
        ("o dente quebrou ontem", "quebrou"),
        ("esta latejando desde ontem", "latejando"),
    ])
    def test_detects_new_clinical_symptom(self, msg, label):
        """CA-006: novos sintomas devem ser classificados como duvida_clinica."""
        decision = ScopeGuardService.classify_patient_message(msg)
        assert decision is not None, f"Nao detectou sintoma: {label!r}"
        assert decision.reason == "duvida_clinica"


# ---------------------------------------------------------------------------
# UT-07 / CA-007: SC-06 — normalizacao anti-ofuscacao
# ---------------------------------------------------------------------------

class TestAntiObfuscationNormalization:
    """SC-06: _normalize neutraliza repetição de letras e separadores."""

    def test_repeated_chars_price_detected(self):
        """CA-007a: 'preçooo' normalizado deve ser detectado como pedido de preco."""
        decision = ScopeGuardService.classify_patient_message("qual o precoooo?")
        assert decision is not None
        assert decision.reason == "fora_do_escopo"

    def test_spaced_letters_price_detected(self):
        """CA-007b: 'p r e c o' normalizado deve ser detectado como pedido de preco."""
        normalized = ScopeGuardService._normalize("p r e c o")
        from src.domain.policies.scope_guard_service import ScopeGuardService as SGS
        assert any(p.search(normalized) for p in SGS._PRICE_PATTERNS), (
            f"'p r e c o' normalizado para {normalized!r} nao casa nenhum _PRICE_PATTERNS"
        )

    def test_spaced_letters_as_message_escalates(self):
        """Mensagem com 'p r e c o' deve escalar."""
        decision = ScopeGuardService.classify_patient_message("p r e c o do implante?")
        assert decision is not None
        assert decision.reason == "fora_do_escopo"

    def test_repeated_chars_clinical_detected(self):
        """SC-06: 'dorrrr' deve ser detectado como sintoma clinico."""
        decision = ScopeGuardService.classify_patient_message("estou com dorrr no dente")
        assert decision is not None
        assert decision.reason == "duvida_clinica"

    def test_normal_text_unaffected(self):
        """SC-06: texto normal de agendamento nao e afetado pela normalizacao."""
        decision = ScopeGuardService.classify_patient_message(
            "quero agendar para terca de manha"
        )
        assert decision is None


# ---------------------------------------------------------------------------
# UT-08 / CA-008: AG-05 — CheckPlanTool nao vaza restricoes ao paciente
# ---------------------------------------------------------------------------

class TestCheckPlanToolNoRestrictionLeak:
    """AG-05: CheckPlanTool._run nao deve retornar 'Restricoes:' ou 'NAO sao cobertos'."""

    def _build_plan_with_restrictions(self):
        return {
            "name": "PlanoTeste",
            "active": True,
            "referral": False,
            "restrictions": ["implante", "cirurgia"],
        }

    def test_plan_with_restrictions_no_leak(self, monkeypatch):
        """CA-008: plano com restricoes nao deve incluir 'Restrições:' na saida ao paciente."""
        plan = self._build_plan_with_restrictions()
        monkeypatch.setattr(
            "src.interfaces.tools.config_tool.ConfigService.get_plan_by_name",
            lambda self, name: plan,
        )
        tool = CheckPlanTool()
        result = tool._run("PlanoTeste")
        assert "Restrições:" not in result
        assert "NÃO são cobertos" not in result
        assert "Restricoes:" not in result
        assert "NAO sao cobertos" not in result

    def test_referral_plan_still_works(self, monkeypatch):
        """EB-04: plano referral mantém o alerta de encaminhamento."""
        plan = {
            "name": "PlanoReferral",
            "active": True,
            "referral": True,
            "referral_message": "Encaminhar para a Dra. Ana.",
        }
        monkeypatch.setattr(
            "src.interfaces.tools.config_tool.ConfigService.get_plan_by_name",
            lambda self, name: plan,
        )
        tool = CheckPlanTool()
        result = tool._run("PlanoReferral")
        assert "ENCAMINHAMENTO" in result.upper() or "encaminhamento" in result.lower()

    def test_plan_without_restrictions_ok(self, monkeypatch):
        """Plano sem restricoes retorna cobertura completa."""
        plan = {"name": "PlanoOk", "active": True, "referral": False, "restrictions": []}
        monkeypatch.setattr(
            "src.interfaces.tools.config_tool.ConfigService.get_plan_by_name",
            lambda self, name: plan,
        )
        tool = CheckPlanTool()
        result = tool._run("PlanoOk")
        assert "Restricoes:" not in result
        assert "NAO sao cobertos" not in result
        assert "cobertura" in result.lower() or "cobertos" in result.lower() or "coberto" in result.lower()


# ---------------------------------------------------------------------------
# EB-01: comportamento com texto vazio/None
# ---------------------------------------------------------------------------

class TestEdgeCasesEmpty:
    """EB-01: texto vazio/None deve retornar None / True."""

    def test_empty_classify(self):
        assert ScopeGuardService.classify_patient_message("") is None

    def test_none_classify(self):
        assert ScopeGuardService.classify_patient_message(None) is None

    def test_empty_response_is_safe(self):
        assert ScopeGuardService.response_is_safe("") is True

    def test_none_response_is_safe(self):
        assert ScopeGuardService.response_is_safe(None) is True


# ---------------------------------------------------------------------------
# CA-011 / RNF-001: sem chamadas de rede/LLM no caminho do guard
# ---------------------------------------------------------------------------

class TestDeterministicNoCalls:
    """CA-011: guard é puramente deterministico — sem I/O externo."""

    def test_classify_no_network(self):
        """classify_patient_message executa em microsegundos sem I/O."""
        import time
        start = time.perf_counter()
        for _ in range(500):
            ScopeGuardService.classify_patient_message("Quanto custa o implante?")
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"Guard muito lento ({elapsed:.3f}s para 500 chamadas)"

    def test_response_is_safe_no_network(self):
        """response_is_safe executa em microsegundos sem I/O."""
        import time
        start = time.perf_counter()
        for _ in range(500):
            ScopeGuardService.response_is_safe("Posso te ajudar com sua consulta. R$ 500.")
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"Guard muito lento ({elapsed:.3f}s para 500 chamadas)"


# ---------------------------------------------------------------------------
# AT-03: calibracao — tentativas de vazamento sempre bloqueadas
# ---------------------------------------------------------------------------

class TestScopeLeakBlocked:
    """AT-03: ao menos 10 exemplos de tentativa de vazamento sao 100% bloqueados."""

    LEAK_ATTEMPTS = [
        # Preco explicito
        "O implante custa R$ 500.",
        "A limpeza e 150 reais.",
        # Valor nu
        "o clareamento fica em 800",
        "sao 1200 por sessao",
        # Recomendacao clinica
        "Recomendo extrair o dente amanhã.",
        "O tratamento ideal e a endodontia.",
        # Procedimento na resposta
        "Para sua situacao, o implante e indicado.",
        # Marcador seguro + preco (SC-01)
        "Posso te ajudar com sua consulta. O canal custa R$ 1200.",
        "Apenas com agendamentos posso ajudar. Sao 350 reais.",
        # Valor nu com conjuncao
        "vai 600 com o convenio",
    ]

    @pytest.mark.parametrize("response", LEAK_ATTEMPTS)
    def test_leak_attempt_blocked(self, response):
        """AT-03: resposta de vazamento deve ser barrada por response_is_safe."""
        assert ScopeGuardService.response_is_safe(response) is False, (
            f"VAZAMENTO NAO BLOQUEADO: {response!r}"
        )
