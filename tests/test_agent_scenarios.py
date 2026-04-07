"""
Testes de cenários do AgentConversationService.

Mocka Calendar, SQLite e ConversationState para rodar offline.
Cada cenário define: descrição, phone, mensagem, histórico (opcional),
stage (opcional), e critérios de validação (keywords esperadas / proibidas
na resposta).

Rodar:
    cd c:/Apps/WPP-DENTAL
    python -m pytest tests/test_agent_scenarios.py -v --tb=short 2>&1

Ou diretamente:
    python tests/test_agent_scenarios.py
"""

from __future__ import annotations

import json
import os
import sys

# Força UTF-8 no stdout/stderr do Windows para suportar emojis e caracteres especiais
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import textwrap
import time
import unittest
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional
from unittest.mock import MagicMock, patch

# ─── Garante que o src está no path ──────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ─── Carrega .env e variáveis mínimas para o agent funcionar ─────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
os.environ["CONVERSATION_ENGINE"] = "agent"

# ─── Cores ANSI ──────────────────────────────────────────────────────────────
GREEN   = "\033[32m"
RED     = "\033[31m"
YELLOW  = "\033[33m"
CYAN    = "\033[36m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
RESET   = "\033[0m"

# ─── Helpers de mock ─────────────────────────────────────────────────────────

def _future_date(days_ahead: int = 3) -> str:
    """Retorna uma data futura no formato DD/MM/YYYY (pula finais de semana)."""
    dt = datetime.now()
    added = 0
    while added < days_ahead:
        dt += timedelta(days=1)
        if dt.weekday() < 5:
            added += 1
    return dt.strftime("%d/%m/%Y")


def _make_slots(date_str: str, times: list[str]) -> list[dict]:
    return [{"formatted": f"{date_str} {t}"} for t in times]


def _mock_calendar(date_str: Optional[str] = None, times: Optional[list[str]] = None):
    """Retorna um mock do CalendarService."""
    if date_str is None:
        date_str = _future_date(3)
    if times is None:
        times = ["08:00", "08:15", "14:00"]

    slots = _make_slots(date_str, times)
    m = MagicMock()
    m.get_available_slots.return_value = slots
    m.find_appointments_by_phone.return_value = [
        {
            "id": "evt_abc123",
            "summary": "Consulta paciente",
            "start": {"dateTime": f"{datetime.now().replace(hour=10, minute=0).isoformat()}"},
        }
    ]
    m.create_appointment_if_available.return_value = {"id": "evt_new999"}
    m.cancel_appointment.return_value = True
    return m


def _mock_db(patient_row: Optional[dict] = None):
    """Retorna um mock do sqlite3 connection."""
    db = MagicMock()
    cursor = MagicMock()

    if patient_row:
        row = MagicMock()
        row.__getitem__ = lambda self, key: patient_row.get(key)
        row.get = lambda key, default=None: patient_row.get(key, default)
        cursor.fetchone.return_value = row
    else:
        cursor.fetchone.return_value = None

    db.execute.return_value = cursor
    db.commit.return_value = None
    return db


# ─── Definição dos cenários ───────────────────────────────────────────────────

@dataclass
class Scenario:
    id: str
    description: str
    message: str
    phone: str = "5513999990001"
    patient_name: str = "Paciente Teste"
    history: str = ""
    stage: str = "idle"
    pending_event_label: str = ""
    pending_event_id: str = ""
    patient_in_db: Optional[dict] = None          # None = paciente novo
    expect_any: list[str] = field(default_factory=list)   # pelo menos uma dessas
    expect_none: list[str] = field(default_factory=list)  # nenhuma dessas
    category: str = "geral"
    should_fail: bool = False   # cenário que testa rejeição / erro esperado


SCENARIOS: list[Scenario] = [
    # ── Saudações ────────────────────────────────────────────────────────────
    Scenario(
        id="G01",
        category="saudacao",
        description="Boa noite simples — NÃO deve agendar nem cancelar",
        message="Boa noite!",
        expect_any=["boa noite", "olá", "como posso", "ajudar", "melody"],
        expect_none=["cancelar", "agend", "erro"],
    ),
    Scenario(
        id="G02",
        category="saudacao",
        description="Bom dia com nome — deve cumprimentar de volta",
        message="Bom dia, tudo bem?",
        expect_any=["bom dia", "olá", "como posso", "ajudar"],
        expect_none=["cancelar", "erro"],
    ),

    # ── Dúvidas sobre convênios ───────────────────────────────────────────────
    Scenario(
        id="P01",
        category="plano",
        description="Pergunta quais convênios são aceitos",
        message="Quais convênios vocês aceitam?",
        expect_any=["odontoprev", "bradesco", "sulamerica", "unimed", "convenio", "plano"],
        expect_none=["erro"],
    ),
    Scenario(
        id="P02",
        category="plano",
        description="Informa OdontoPrev — deve confirmar atendimento DIRETO (sem encaminhamento)",
        message="Meu plano é OdontoPrev",
        expect_any=["odontoprev", "atend", "ativo", "sem restrições", "ajudar"],
        expect_none=["não encontrado", "hapvida", "tarcilia", "encaminh"],
    ),
    Scenario(
        id="P03",
        category="plano",
        description="HapVida — convênio inexistente, deve informar que não atende",
        message="Tenho HapVida, vocês atendem?",
        expect_any=["não", "nao", "hapvida", "não encontrado", "verificar", "planos aceitos", "aceit"],
        expect_none=["confirmado", "pode vir"],
    ),
    Scenario(
        id="P04",
        category="plano",
        description="Caixa de Saúde — deve encaminhar para Dra. Tarcilia",
        message="Tenho Caixa de Saúde de São Vicente",
        expect_any=["tarcilia", "encaminh", "parceira", "outro profissional"],
        expect_none=["confirmar", "pode agendar"],
    ),
    Scenario(
        id="P05",
        category="plano",
        description="Particular — deve confirmar atendimento",
        message="Não tenho plano, seria particular",
        expect_any=["particular", "atend", "sim"],
        expect_none=["não encontrado", "erro"],
    ),
    Scenario(
        id="P06",
        category="plano",
        description="Typo no nome do plano: 'bradesco dental' minúsculo — atendimento DIRETO",
        message="tenho bradesco dental",
        expect_any=["bradesco", "atend", "ativo", "ajudar"],
        expect_none=["não encontrado", "erro", "tarcilia", "encaminh"],
    ),

    # ── Dúvidas sobre procedimentos ───────────────────────────────────────────
    Scenario(
        id="PR01",
        category="procedimento",
        description="Canal em molar — NÃO realizamos",
        message="Vocês fazem canal de molar?",
        expect_any=["não", "nao", "molar", "canal", "realizar", "não realizamos"],
        expect_none=["sim, realizamos", "pode agendar"],
    ),
    Scenario(
        id="PR02",
        category="procedimento",
        description="Extração de siso — somente particular",
        message="Preciso tirar um siso, vocês fazem?",
        expect_any=["particular", "siso", "particular", "plano"],
        expect_none=["todos os planos", "erro"],
    ),
    Scenario(
        id="PR03",
        category="procedimento",
        description="Ortodontia/aparelho — exige foto da carteirinha",
        message="Quero fazer aparelho, aceito OdontoPrev",
        expect_any=["carteirinha", "foto", "aparelho", "odontoprev"],
        expect_none=["sem restrição", "erro"],
    ),
    Scenario(
        id="PR04",
        category="procedimento",
        description="Consulta de rotina — sem restrição",
        message="Quero fazer uma consulta de rotina com a doutora",
        expect_any=["agendar", "agend", "horário", "quando", "data", "período"],
        expect_none=["não realizamos", "erro"],
    ),

    # ── Idade mínima ──────────────────────────────────────────────────────────
    Scenario(
        id="ID01",
        category="idade",
        description="Criança de 6 anos — abaixo da idade mínima",
        message="Quero agendar para meu filho de 6 anos",
        expect_any=["8 anos", "mínimo", "minimo", "partir de", "idade"],
        expect_none=["pode agendar", "horário disponível"],
    ),
    Scenario(
        id="ID02",
        category="idade",
        description="Criança de 10 anos — dentro da faixa etária",
        message="Minha filha tem 10 anos, podem atender?",
        # O agente pode mencionar "a partir de 8 anos" como contexto positivo, tudo bem
        expect_any=["agendar", "sim", "pode", "horário", "atend", "nome", "convenio", "convênio"],
        expect_none=["não atendemos", "infelizmente não"],
    ),

    # ── Agendamento ───────────────────────────────────────────────────────────
    Scenario(
        id="A01",
        category="agendamento",
        description="Pede horário sem data — deve oferecer próximo disponível",
        message="Quero marcar uma consulta",
        expect_any=["horário", "data", "quando", "período", "disponível", "nome"],
        expect_none=["erro", "não posso"],
    ),
    Scenario(
        id="A02",
        category="agendamento",
        description="Pede horário de manhã — agent coleta dados antes de mostrar slots",
        message="Quero consulta de manhã",
        # Correto: agent pede nome/convênio antes de buscar horários
        expect_any=["manhã", "horário", "disponível", "data", "nome", "convênio", "convenio", "plano"],
        expect_none=["erro interno"],
    ),
    Scenario(
        id="A03",
        category="agendamento",
        description="Data com caractere estranho '15/04??' — deve entender como pedido de data",
        message="Tem horário no dia 15/04??",
        expect_any=["15/04", "horário", "disponível", "data"],
        expect_none=["não entendi", "erro"],
    ),
    Scenario(
        id="A04",
        category="agendamento",
        description="Fluxo de agendamento com histórico — confirmação da data",
        message="Pode ser o primeiro horário",
        history=(
            f"ASSISTENTE: Encontrei horários disponíveis em {_future_date(3)}:\n"
            f"  1. {_future_date(3)} 08:00\n"
            f"  2. {_future_date(3)} 08:15\n"
            "PACIENTE: Pode ser de manhã"
        ),
        patient_in_db={"name": "Maria Silva", "phone": "5513999990001", "plan": "OdontoPrev"},
        expect_any=["confirmar", "confirmo", "agendar", "horário", "08:00", "08:15"],
        expect_none=["erro"],
    ),

    # ── Cancelamento ─────────────────────────────────────────────────────────
    Scenario(
        id="C01",
        category="cancelamento",
        description="Pede para cancelar consulta (tem agendamento no mock)",
        message="Vou precisar cancelar minha consulta",
        patient_in_db={"name": "João Silva", "phone": "5513999990001", "plan": "OdontoPrev"},
        expect_any=["cancelar", "cancel", "consulta", "confirmar", "certeza", "prontinho", "cancelad"],
        expect_none=["erro interno"],
    ),
    Scenario(
        id="C02",
        category="cancelamento",
        description="Cancelamento com contexto de confirmação de outro assunto",
        message="quero cancelar",
        history=(
            "ASSISTENTE: Olá! Como posso ajudar?\n"
            "PACIENTE: quero cancelar"
        ),
        expect_any=["cancelar", "consulta", "confirmar"],
        expect_none=["erro interno"],
    ),

    # ── Confirmação de consulta (CONFIRMATION_STAGE) ──────────────────────────
    Scenario(
        id="CF01",
        category="confirmacao",
        description="Confirma consulta com 'sim' — deve confirmar sem criar nem cancelar",
        message="sim",
        stage="awaiting_appointment_confirmation",
        pending_event_label=f"{_future_date(1)} as 10:00",
        pending_event_id="evt_abc123",
        expect_any=["confirmad", "confirmo", "confirmada", "até amanhã", "ok"],
        expect_none=["cancelar", "criar_agendamento", "erro"],
    ),
    Scenario(
        id="CF02",
        category="confirmacao",
        description="'Boa noite doutora' em CONFIRMATION_STAGE — NÃO deve remarcar",
        message="Boa noite doutora",
        stage="awaiting_appointment_confirmation",
        pending_event_label=f"{_future_date(1)} as 10:00",
        pending_event_id="evt_abc123",
        expect_any=["boa noite", "confirmar", "consulta", "amanhã"],
        expect_none=["remarcad", "cancelad", "agendamento criado"],
    ),
    Scenario(
        id="CF03",
        category="confirmacao",
        description="Quer remarcar em CONFIRMATION_STAGE",
        message="quero remarcar para outra data",
        stage="awaiting_appointment_confirmation",
        pending_event_label=f"{_future_date(1)} as 10:00",
        pending_event_id="evt_abc123",
        patient_in_db={"name": "Ana Costa", "phone": "5513999990001", "plan": "Bradesco Dental"},
        expect_any=["remarcar", "horário", "data", "cancelar", "novo", "disponível"],
        expect_none=["confirmad", "erro interno"],
    ),
    Scenario(
        id="CF04",
        category="confirmacao",
        description="Cancela em CONFIRMATION_STAGE",
        message="não vou poder ir, cancela",
        stage="awaiting_appointment_confirmation",
        pending_event_label=f"{_future_date(1)} as 10:00",
        pending_event_id="evt_abc123",
        patient_in_db={"name": "Ana Costa", "phone": "5513999990001", "plan": "Bradesco Dental"},
        expect_any=["cancelad", "cancelar", "cancel", "prontinho", "ok", "cancelada"],
        expect_none=["erro interno"],
    ),

    # ── Dúvidas clínicas (não devem ser respondidas) ─────────────────────────
    Scenario(
        id="CL01",
        category="clinico",
        description="Dor de dente — deve encaminhar para doutora",
        message="Estou com muita dor de dente, o que faço?",
        expect_any=["doutora", "encaminhar", "dor", "urgência", "entrar em contato"],
        expect_none=["tome analgésico", "aplique gelo", "diagnóstico"],
    ),
    Scenario(
        id="CL02",
        category="clinico",
        description="Pergunta sobre preço — não informa valores",
        message="Quanto custa uma consulta?",
        expect_any=["doutora", "valores", "contato", "informar", "não inform"],
        expect_none=["R$", "reais", "grátis", "gratuito"],
    ),

    # ── Consultar agendamento ─────────────────────────────────────────────────
    Scenario(
        id="Q01",
        category="consulta",
        description="Paciente pergunta sobre próxima consulta",
        message="Qual é minha próxima consulta?",
        expect_any=["consulta", "data", "horário", "encontrei", "agendad"],
        expect_none=["erro interno"],
    ),

    # ── Borda / erro de digitação ─────────────────────────────────────────────
    Scenario(
        id="E01",
        category="erro_digitacao",
        description="Mensagem com typo grave — deve entender a intenção",
        message="qero marcr uma conslta",
        expect_any=["agendar", "consulta", "horário", "data", "quando"],
        expect_none=["não entendi", "erro"],
    ),
    Scenario(
        id="E02",
        category="erro_digitacao",
        description="Mensagem em maiúsculas — deve responder normalmente",
        message="QUERO CANCELAR MINHA CONSULTA",
        expect_any=["cancelar", "consulta"],
        expect_none=["erro interno"],
    ),
    Scenario(
        id="E03",
        category="erro_digitacao",
        description="'Confirmar' sem stage — não deve entrar em loop, deve pedir contexto",
        message="quero confirmar",
        # Correto: agent pergunta o que o paciente quer confirmar (agendamento? nome? plano?)
        expect_any=["confirmar", "consulta", "agend", "ajudar", "o que", "plano", "convenio", "convênio", "nome"],
        expect_none=["erro interno", "problema interno"],
    ),
]


# ─── Runner de testes ─────────────────────────────────────────────────────────

@dataclass
class TestResult:
    scenario: Scenario
    response: str
    passed: bool
    failures: list[str]
    elapsed: float
    error: Optional[str] = None


def _check(response: str, scenario: Scenario) -> tuple[bool, list[str]]:
    resp_lower = response.lower()
    failures = []

    if scenario.expect_any:
        if not any(kw.lower() in resp_lower for kw in scenario.expect_any):
            failures.append(
                f"expect_any FALHOU — nenhuma das palavras encontradas: {scenario.expect_any}"
            )

    for kw in scenario.expect_none:
        if kw.lower() in resp_lower:
            failures.append(f"expect_none FALHOU — palavra proibida encontrada: '{kw}'")

    return len(failures) == 0, failures


def run_scenario(scenario: Scenario) -> TestResult:
    from src.application.services.agent_conversation_service import AgentConversationService
    from src.application.services.conversation_state_service import ConversationState

    # Mock do estado da conversa
    state = ConversationState(
        stage=scenario.stage,
        pending_event_label=scenario.pending_event_label,
        pending_event_id=scenario.pending_event_id,
        metadata={
            "appointment_confirmation_event_id": scenario.pending_event_id,
        } if scenario.pending_event_id else {},
    )

    future_date = _future_date(3)
    mock_cal = _mock_calendar(future_date)
    mock_db_obj = _mock_db(scenario.patient_in_db)

    patches = [
        # Patcha onde a classe é USADA (não onde é definida)
        # CalendarService: requer credenciais Google — sempre mockado
        patch("src.interfaces.tools.calendar_tool.CalendarService", return_value=mock_cal),
        # SQLite: requer banco inicializado — mockado para testes
        patch("src.interfaces.tools.patient_tool.get_db", return_value=mock_db_obj),
        patch("src.application.services.conversation_state_service.get_db", return_value=mock_db_obj),
        # ConversationStateService: mockado para controlar o stage nos testes
        patch(
            "src.application.services.conversation_state_service.ConversationStateService.get",
            return_value=state,
        ),
        patch("src.application.services.conversation_state_service.ConversationStateService.clear"),
        patch("src.application.services.conversation_state_service.ConversationStateService.save"),
        # ConfigService NÃO é mockado — lê os YAMLs reais do projeto (plans.yaml, settings.yaml, etc.)
    ]

    try:
        for p in patches:
            p.start()

        svc = AgentConversationService()
        t0 = time.time()
        response = svc.process_message(
            patient_phone=scenario.phone,
            patient_message=scenario.message,
            patient_name=scenario.patient_name,
            history_text=scenario.history or None,
        )
        elapsed = time.time() - t0

        passed, failures = _check(response, scenario)
        return TestResult(scenario=scenario, response=response, passed=passed,
                          failures=failures, elapsed=elapsed)

    except Exception as exc:
        return TestResult(scenario=scenario, response="", passed=False,
                          failures=[f"EXCEÇÃO: {exc}"], elapsed=0.0, error=str(exc))
    finally:
        for p in patches:
            try:
                p.stop()
            except RuntimeError:
                pass


# ─── Report ───────────────────────────────────────────────────────────────────

def _print_result(r: TestResult, idx: int, total: int) -> None:
    status = f"{GREEN}✓ PASSOU{RESET}" if r.passed else f"{RED}✗ FALHOU{RESET}"
    print(
        f"  [{idx:02d}/{total}] {status} "
        f"{BOLD}[{r.scenario.id}]{RESET} {r.scenario.description} "
        f"{DIM}({r.elapsed:.1f}s){RESET}"
    )
    if not r.passed:
        print(f"       {CYAN}Mensagem:{RESET} {r.scenario.message}")
        print(f"       {CYAN}Resposta:{RESET} {textwrap.shorten(r.response, 120)}")
        for f in r.failures:
            print(f"       {RED}→ {f}{RESET}")
    elif r.response:
        print(f"       {DIM}Resposta: {textwrap.shorten(r.response, 100)}{RESET}")


def run_all() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        print(f"{RED}ERRO: OPENAI_API_KEY não definida. "
              f"Defina a variável antes de rodar os testes.{RESET}")
        sys.exit(1)

    print(f"\n{BOLD}{'-'*70}{RESET}")
    print(f"{BOLD}  WPP-DENTAL -- Testes de Cenarios do Agente ReAct{RESET}")
    print(f"{BOLD}{'-'*70}{RESET}\n")

    results: list[TestResult] = []
    categories: dict[str, list[TestResult]] = {}

    total = len(SCENARIOS)
    for idx, scenario in enumerate(SCENARIOS, 1):
        print(f"  {DIM}Rodando [{scenario.id}] {scenario.description[:55]}...{RESET}", end="\r")
        r = run_scenario(scenario)
        results.append(r)
        categories.setdefault(scenario.category, []).append(r)
        _print_result(r, idx, total)

    # ── Sumário por categoria ──────────────────────────────────────────────
    print(f"\n{BOLD}{'-'*70}{RESET}")
    print(f"{BOLD}  Sumario por categoria{RESET}\n")
    for cat, cat_results in sorted(categories.items()):
        passed = sum(1 for r in cat_results if r.passed)
        total_cat = len(cat_results)
        color = GREEN if passed == total_cat else (YELLOW if passed > 0 else RED)
        print(f"  {color}{cat:<20}{RESET}  {passed}/{total_cat}")

    # ── Sumário geral ──────────────────────────────────────────────────────
    passed_total = sum(1 for r in results if r.passed)
    failed_total = total - passed_total
    color = GREEN if failed_total == 0 else (YELLOW if passed_total > failed_total else RED)
    print(f"\n{BOLD}{'-'*70}{RESET}")
    print(f"  {BOLD}Total: {color}{passed_total} passou / {failed_total} falhou{RESET} de {total} cenarios")
    print(f"{BOLD}{'-'*70}{RESET}\n")

    # ── Detalhes dos falhos ────────────────────────────────────────────────
    failed = [r for r in results if not r.passed]
    if failed:
        print(f"{BOLD}{RED}  Cenarios com falha -- detalhes completos:{RESET}\n")
        for r in failed:
            print(f"  {RED}{'-'*60}{RESET}")
            print(f"  {BOLD}[{r.scenario.id}] {r.scenario.description}{RESET}")
            print(f"  Categoria   : {r.scenario.category}")
            print(f"  Mensagem    : {r.scenario.message}")
            if r.scenario.history:
                print(f"  Histórico   : {textwrap.shorten(r.scenario.history, 80)}")
            if r.scenario.stage != "idle":
                print(f"  Stage       : {r.scenario.stage}")
            print(f"  Resposta    : {r.response or '(vazia)'}")
            for f in r.failures:
                print(f"  {RED}→ {f}{RESET}")
            print()

    # ── Exit code para CI ──────────────────────────────────────────────────
    sys.exit(0 if failed_total == 0 else 1)


# ─── Compatibilidade com pytest ───────────────────────────────────────────────

class TestAgentScenarios(unittest.TestCase):
    pass


def _make_test(scenario: Scenario):
    def test_fn(self):
        r = run_scenario(scenario)
        if not r.passed:
            self.fail(
                f"[{scenario.id}] {scenario.description}\n"
                f"Mensagem: {scenario.message}\n"
                f"Resposta: {r.response}\n"
                + "\n".join(r.failures)
            )
    test_fn.__name__ = f"test_{scenario.id}_{scenario.category}"
    test_fn.__doc__ = f"[{scenario.id}] {scenario.description}"
    return test_fn


for _scenario in SCENARIOS:
    setattr(
        TestAgentScenarios,
        f"test_{_scenario.id}_{_scenario.category}",
        _make_test(_scenario),
    )


if __name__ == "__main__":
    run_all()
