"""Validacoes de escopo para mensagens de atendimento."""

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class EscalationDecision:
    """Representa a decisao de escalar uma conversa."""

    reason: str
    summary: str


class ScopeGuardService:
    """Aplica regras deterministicas para manter o atendimento no escopo."""

    _PRICE_PATTERNS = (
        re.compile(r"\bpreco\b"),
        re.compile(r"\bvalor\b"),
        re.compile(r"\bcusta(r|m)?\b"),
        re.compile(r"\bquanto (fica|custa)\b"),
        re.compile(r"\borcamento\b"),
    )
    _PROCEDURE_TERMS = (
        "clareamento",
        "implante",
        "aparelho",
        "canal",
        "extracao",
        "limpeza",
        "cirurgia",
        "protese",
        "faceta",
        "obturacao",
        "restauracao",
    )
    _PROCEDURE_INFO_PATTERNS = (
        re.compile(r"\b(voce[s]? )?(faz|fazem|realiza|realizam)\b"),
        re.compile(r"\bcobre\b"),
        re.compile(r"\bcobertura\b"),
        re.compile(r"\bcomo funciona\b"),
        re.compile(r"\binformac(?:ao|oes)\b"),
        re.compile(r"\bsobre\b"),
        re.compile(r"\bindicad[oa]\b"),
        re.compile(r"\bserve\b"),
    )
    _SUPPORTED_OPERATIONAL_PROCEDURE_TERMS = (
        "protese",
        "ortodontia",
        "canal em molar",
        "siso",
        "extracao de siso",
    )
    _SUPPORTED_OPERATIONAL_CONTEXT_PATTERNS = (
        re.compile(r"\bagend"),
        re.compile(r"\bmarc"),
        re.compile(r"\bconsulta\b"),
        re.compile(r"\bconvenio\b"),
        re.compile(r"\bplano\b"),
        re.compile(r"\bparticular\b"),
        re.compile(r"\bcarteir"),
        re.compile(r"\bcobre\b"),
        re.compile(r"\batende\b"),
        re.compile(r"\bfaz\b"),
    )
    _CLINICAL_PATTERNS = (
        re.compile(r"\bdor\b"),
        re.compile(r"\binchac"),
        re.compile(r"\bsangr"),
        re.compile(r"\bfebre\b"),
        re.compile(r"\binflam"),
        re.compile(r"\burgenc"),
        re.compile(r"\bsensibilidade\b"),
        re.compile(r"\binfecc"),
    )
    _UNSAFE_RESPONSE_PATTERNS = (
        re.compile(r"r\$\s*\d"),
        re.compile(r"\b\d+[,.]?\d*\s*reais\b"),
        re.compile(r"\bo procedimento\b"),
        re.compile(r"\bo tratamento\b"),
        re.compile(r"\brecomendo\b"),
        re.compile(r"\bindicado\b"),
    )
    _SAFE_RESPONSE_MARKERS = (
        "nao posso informar",
        "nao consigo informar",
        "a doutora entrara em contato",
        "vou encaminhar",
        "posso te ajudar com sua consulta",
        "posso te ajudar com consultas e agendamentos",
        "apenas com agendamentos",
        "foto da carteirinha",
        "a doutora vai conferir e te orientar",
        "no momento nao realizamos",
        "atendida apenas no particular",
        "atendemos por convenio apenas pelos planos",
    )

    @staticmethod
    def _normalize(text: str) -> str:
        normalized = unicodedata.normalize("NFKD", text or "")
        normalized = normalized.encode("ascii", "ignore").decode("ascii").lower()
        return re.sub(r"\s+", " ", normalized).strip()

    @classmethod
    def classify_patient_message(cls, text: str) -> EscalationDecision | None:
        """Classifica mensagens que precisam ser tratadas fora do fluxo normal."""
        normalized = cls._normalize(text)
        if not normalized:
            return None

        if any(pattern.search(normalized) for pattern in cls._PRICE_PATTERNS):
            return EscalationDecision(
                reason="fora_do_escopo",
                summary="Paciente pediu preco/valor de consulta ou procedimento.",
            )

        if any(pattern.search(normalized) for pattern in cls._CLINICAL_PATTERNS):
            return EscalationDecision(
                reason="duvida_clinica",
                summary="Paciente trouxe duvida clinica ou sintomas.",
            )

        supports_operational_triage = (
            any(term in normalized for term in cls._SUPPORTED_OPERATIONAL_PROCEDURE_TERMS)
            and any(pattern.search(normalized) for pattern in cls._SUPPORTED_OPERATIONAL_CONTEXT_PATTERNS)
        )
        if supports_operational_triage:
            return None

        has_procedure_term = any(term in normalized for term in cls._PROCEDURE_TERMS)
        asks_about_procedure = any(
            pattern.search(normalized) for pattern in cls._PROCEDURE_INFO_PATTERNS
        )
        if has_procedure_term and asks_about_procedure:
            return EscalationDecision(
                reason="fora_do_escopo",
                summary="Paciente pediu informacoes sobre procedimento odontologico.",
            )

        return None

    @classmethod
    def response_is_safe(cls, response_text: str) -> bool:
        """Valida se a resposta gerada continua dentro do escopo permitido."""
        normalized = cls._normalize(response_text)
        if not normalized:
            return True

        if any(marker in normalized for marker in cls._SAFE_RESPONSE_MARKERS):
            return True

        if any(pattern.search(normalized) for pattern in cls._UNSAFE_RESPONSE_PATTERNS):
            return False

        has_procedure_term = any(term in normalized for term in cls._PROCEDURE_TERMS)
        if has_procedure_term:
            return False

        if any(pattern.search(normalized) for pattern in cls._CLINICAL_PATTERNS):
            return False

        return True
