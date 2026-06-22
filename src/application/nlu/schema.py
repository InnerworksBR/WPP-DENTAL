"""Contrato da NLU: intenção + entidades + contexto (impl 015).

Estruturas neutras que descrevem o que o paciente quer, sem decidir nada de agenda. O
orquestrador (016) é quem consome este resultado e decide a ação.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Intent(str, Enum):
    """Intenção de alto nível da mensagem do paciente."""

    AGENDAR = "agendar"
    REMARCAR = "remarcar"
    CANCELAR = "cancelar"
    CONFIRMAR = "confirmar"            # afirmação a um pedido de confirmação pendente
    RECUSAR = "recusar"               # recusa da oferta/horário atual
    ESCOLHER_HORARIO = "escolher_horario"  # escolha de um horário já ofertado
    INFORMAR_NOME = "informar_nome"
    INFORMAR_PLANO = "informar_plano"
    CONSULTAR = "consultar"           # consultar consulta existente
    SAUDACAO = "saudacao"
    FORA_ESCOPO = "fora_escopo"
    AMBIGUO = "ambiguo"


@dataclass
class Entities:
    """Entidades objetivas extraídas da mensagem (paridade com AppointmentRequestConstraints)."""

    period: str = ""
    date: str = ""                    # DD/MM/YYYY
    time: str = ""                    # horário específico pedido (ex.: "11:00")
    earliest_time: str = ""           # "a partir das X"
    weekday: str = ""                 # "0".."4" (seg..sex)
    excluded_dates: list[str] = field(default_factory=list)
    excluded_day_numbers: list[int] = field(default_factory=list)
    requested_day_number: int = 0
    plan: str = ""
    name: str = ""
    affirmation: "bool | None" = None
    selected_option: "int | None" = None   # 1 ou 2, quando o paciente escolhe pela posição
    selected_time: str = ""                  # horário ofertado efetivamente escolhido
    rejects_current_slot: bool = False
    changes_pending_confirmation: bool = False


@dataclass
class NluContext:
    """Contexto mínimo (derivado do estado) para desambiguar a classificação."""

    has_pending_offer: bool = False
    has_pending_confirmation: bool = False
    offered_date: str = ""
    offered_times: list[str] = field(default_factory=list)
    requested_period: str = ""
    awaiting_name: bool = False
    awaiting_plan: bool = False


@dataclass
class NluResult:
    """Resultado da classificação: intenção + entidades + proveniência."""

    intent: Intent
    entities: Entities = field(default_factory=Entities)
    source: str = "deterministic"     # "deterministic" | "llm"
