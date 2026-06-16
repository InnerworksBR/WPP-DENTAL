"""Interpretacao deterministica de ofertas e confirmacoes de horarios."""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class AppointmentOffer:
    """Representa uma oferta recente de horarios enviada ao paciente."""

    date_str: str
    times: list[str]


@dataclass(frozen=True)
class AppointmentConfirmationRequest:
    """Representa um pedido de confirmacao de agendamento ja proposto ao paciente."""

    date_str: str
    time_str: str


@dataclass(frozen=True)
class AppointmentRequestConstraints:
    """Restricoes objetivas extraidas da mensagem do paciente."""

    rejects_current_slot: bool = False
    earliest_time: str = ""
    requested_period: str = ""
    requested_weekday: str = ""
    excluded_day_numbers: list[int] = field(default_factory=list)
    excluded_dates: list[str] = field(default_factory=list)
    changes_pending_confirmation: bool = False


class AppointmentOfferService:
    """Resolve respostas curtas do paciente para horarios ja ofertados."""

    _DATE_PATTERN = re.compile(r"\b(\d{2}/\d{2}(?:/\d{4})?)\b")
    _TIME_PATTERN = re.compile(r"\b(\d{1,2}):(\d{2})\b")
    # CA-07: require explicit hour context — "as/a 9", "9h", "9 horas".
    # Never match bare numbers ("2 pessoas", "dia 3").
    _HOUR_ONLY_PATTERN = re.compile(
        r"\bas?\s+(\d{1,2})\b"   # "as 9", "às 9", "a 9"
        r"|(\d{1,2})\s*h(?:oras?)?\b"  # "9h", "9 horas"
    )
    _FIRST_OPTION_PATTERN = re.compile(r"\b(primeira|primeiro|1a|1o|opcao 1|opcao numero 1|1)\b")
    _SECOND_OPTION_PATTERN = re.compile(r"\b(segunda|segundo|2a|2o|opcao 2|opcao numero 2|2)\b")
    _CONFIRMATION_MARKERS = (
        "posso confirmar sua consulta",
        "posso confirmar a sua consulta",
    )
    _OFFER_MARKERS = (
        "temos disponibilidade",
        "encontrei horarios disponiveis",
        "encontrei horario disponivel",
        "horarios disponiveis",
        "horario disponivel",
        "opcoes disponiveis",
        "encontrei esse horario para voce",
        "tenho estes horarios livres",
        "qual voce prefere",
        "qual horario prefere",
        "qual prefere",
        "qual horario voce prefere",
        "qual a sua preferencia",
    )
    _AFFIRMATIVE_CONFIRMATION_TOKENS = (
        "sim",
        "confirmo",
        "confirmar",
        "pode confirmar",
        "pode sim",
        "confirmado",
        "fechado",
        "ok",
        "okay",
        "pode agendar",
        "pode marcar",
    )
    _NEGATIVE_CONFIRMATION_TOKENS = (
        "nao",
        "cancel",
        "troca",
        "muda",
        "outro horario",
        "outra opcao",
    )
    _CHANGE_REQUEST_TOKENS = (
        "remarcar",
        "reagendar",
        "mudar",
        "trocar",
        "outro horario",
        "outra opcao",
        "outro dia",
        "outra data",
        "outra hora",
    )
    _REJECTION_TOKENS = (
        "nao quero",
        "nao queria",
        "nao consigo",
        "nao posso",
        "nao da",
        "nao serve",
        "tem outra",
        "outra data",
        "outro horario",
        "outro dia",
        "prefiro outro",
    )
    _PERIODS = {
        "manha": "manha",
        "tarde": "tarde",
        "noite": "noite",
    }
    _WEEKDAY_TEXT = {
        "segunda": "0",
        "segunda feira": "0",
        "terca": "1",
        "terca feira": "1",
        "quarta": "2",
        "quarta feira": "2",
        "quinta": "3",
        "quinta feira": "3",
        "sexta": "4",
        "sexta feira": "4",
    }
    _DAY_WORDS = {
        "primeiro": 1,
        "um": 1,
        "dois": 2,
        "tres": 3,
        "quatro": 4,
        "cinco": 5,
        "seis": 6,
        "sete": 7,
        "oito": 8,
        "nove": 9,
        "dez": 10,
    }

    @staticmethod
    def _normalize(text: str) -> str:
        normalized = unicodedata.normalize("NFKD", text or "")
        normalized = normalized.encode("ascii", "ignore").decode("ascii").lower()
        return re.sub(r"\s+", " ", normalized).strip()

    @staticmethod
    def _resolve_year(date_str_dm: str) -> str:
        """Resolve o ano de uma data 'DD/MM' usando hoje como referencia (CA-08).

        Se 'DD/MM' com o ano atual ja ficou no passado, usa o proximo ano.
        """
        now = datetime.now()
        year = now.year
        try:
            day, month = int(date_str_dm[:2]), int(date_str_dm[3:5])
            candidate = datetime(year, month, day)
            if candidate.date() < now.date():
                year += 1
        except ValueError:
            pass
        return f"{date_str_dm}/{year}"

    @classmethod
    def _is_confirmation_request_text(cls, text: str) -> bool:
        normalized = cls._normalize(text)
        return any(marker in normalized for marker in cls._CONFIRMATION_MARKERS)

    @classmethod
    def _is_offer_text(cls, text: str) -> bool:
        normalized = cls._normalize(text)
        return any(marker in normalized for marker in cls._OFFER_MARKERS)

    @classmethod
    def extract_latest_offer(cls, history: list[dict]) -> AppointmentOffer | None:
        """Extrai a oferta mais recente de horarios a partir do historico."""
        for message in reversed(history):
            if message.get("role") != "assistant":
                continue

            content = message.get("content", "")
            if cls._is_confirmation_request_text(content):
                continue
            if not cls._is_offer_text(content):
                continue

            date_match = cls._DATE_PATTERN.search(content)
            time_matches = cls._TIME_PATTERN.findall(content)
            if not date_match or len(time_matches) < 1:
                continue

            date_str = date_match.group(1)
            if len(date_str) == 5:
                date_str = cls._resolve_year(date_str)

            times = []
            for hour, minute in time_matches:
                formatted = f"{int(hour):02d}:{minute}"
                if formatted not in times:
                    times.append(formatted)

            if times:
                return AppointmentOffer(date_str=date_str, times=times)

        return None

    @classmethod
    def extract_latest_confirmation_request(
        cls,
        history: list[dict],
    ) -> AppointmentConfirmationRequest | None:
        """Extrai o pedido mais recente de confirmacao de horario."""
        for message in reversed(history):
            if message.get("role") != "assistant":
                continue

            content = message.get("content", "")
            if not cls._is_confirmation_request_text(content):
                continue

            date_match = cls._DATE_PATTERN.search(content)
            time_matches = cls._TIME_PATTERN.findall(content)
            if not date_match or not time_matches:
                continue

            date_str = date_match.group(1)
            if len(date_str) == 5:
                date_str = cls._resolve_year(date_str)

            hour, minute = time_matches[0]
            return AppointmentConfirmationRequest(
                date_str=date_str,
                time_str=f"{int(hour):02d}:{minute}",
            )

        return None

    @classmethod
    def resolve_selection(cls, patient_message: str, offer: AppointmentOffer) -> str | None:
        """Interpreta qual horario ofertado o paciente escolheu."""
        if offer is None:
            return None

        normalized = cls._normalize(patient_message)
        if not normalized:
            return None

        explicit_dates = cls._DATE_PATTERN.findall(normalized)
        if explicit_dates:
            normalized_offer_date = offer.date_str
            for explicit_date in explicit_dates:
                candidate = explicit_date
                if len(candidate) == 5:
                    candidate = cls._resolve_year(candidate)
                if candidate != normalized_offer_date:
                    return None

        day_only_match = re.search(r"\bdia\s+(\d{1,2})\b", normalized)
        if day_only_match and int(day_only_match.group(1)) != int(offer.date_str[:2]):
            return None

        if len(offer.times) == 1 and any(
            token in normalized for token in ("sim", "pode ser", "confirmo", "fechado")
        ):
            return offer.times[0]

        explicit_times = {
            f"{int(hour):02d}:{minute}"
            for hour, minute in cls._TIME_PATTERN.findall(normalized)
        }
        for time_str in offer.times:
            if time_str in explicit_times:
                return time_str

        for g1, g2 in cls._HOUR_ONLY_PATTERN.findall(normalized):
            hour_str = g1 or g2
            if hour_str:
                candidate = f"{int(hour_str):02d}:00"
                if candidate in offer.times:
                    return candidate

        if offer.times:
            if cls._FIRST_OPTION_PATTERN.search(normalized):
                return offer.times[0]
            if len(offer.times) >= 2 and cls._SECOND_OPTION_PATTERN.search(normalized):
                return offer.times[1]

        return None

    @classmethod
    def is_affirmative_confirmation(cls, patient_message: str) -> bool:
        """Indica se o paciente confirmou o horario proposto."""
        normalized = cls._normalize(patient_message)
        if not normalized:
            return False
        if any(token in normalized for token in cls._NEGATIVE_CONFIRMATION_TOKENS):
            return False
        # Word-boundary match: evita "assim" ativar "sim", "okdoutora" ativar "ok"
        for token in cls._AFFIRMATIVE_CONFIRMATION_TOKENS:
            if re.search(r"\b" + re.escape(token) + r"\b", normalized):
                return True
        return False

    @classmethod
    def has_change_request(cls, patient_message: str) -> bool:
        """Indica se o paciente pediu mudanca/troca de horario ou dia."""
        normalized = cls._normalize(patient_message)
        return any(token in normalized for token in cls._CHANGE_REQUEST_TOKENS)

    @classmethod
    def extract_request_constraints(cls, patient_message: str) -> AppointmentRequestConstraints:
        """Extrai recusas e filtros de horario sem depender do LLM."""
        normalized = cls._normalize(patient_message)
        if not normalized:
            return AppointmentRequestConstraints()

        rejects_current_slot = any(token in normalized for token in cls._REJECTION_TOKENS)
        if normalized in {"nao", "nao.", "não", "não."}:
            rejects_current_slot = True

        earliest_time = ""
        earliest_patterns = (
            r"(?:depois|apos|ap[oó]s|a partir)\s+d[ae]s?\s*(\d{1,2})(?::?(\d{2}))?\s*h?",
            r"(?:so|somente|apenas)\s+(?:consigo\s+)?(?:depois|apos|a partir)\s+d[ae]s?\s*(\d{1,2})(?::?(\d{2}))?\s*h?",
        )
        for pattern in earliest_patterns:
            match = re.search(pattern, normalized)
            if match:
                hour = int(match.group(1))
                minute = int(match.group(2) or "00")
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    earliest_time = f"{hour:02d}:{minute:02d}"
                break

        requested_period = ""
        for token, period in cls._PERIODS.items():
            if re.search(rf"\b{token}\b", normalized):
                requested_period = period
                if period == "tarde" and not earliest_time:
                    earliest_time = "12:00"
                break

        requested_weekday = ""
        for token, weekday in cls._WEEKDAY_TEXT.items():
            if re.search(rf"\b{token}\b", normalized):
                requested_weekday = weekday
                break

        excluded_dates = []
        for day, month, year in re.findall(
            r"(?:menos|exceto|nao|n[aã]o)\s+(?:no\s+)?dia\s+(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?",
            normalized,
        ):
            full_year = int(year) if year else datetime.now().year
            if full_year < 100:
                full_year += 2000
            try:
                excluded_dates.append(datetime(full_year, int(month), int(day)).strftime("%d/%m/%Y"))
            except ValueError:
                continue

        excluded_day_numbers = []
        for raw in re.findall(r"(?:menos|exceto|nao|n[aã]o)\s+(?:no\s+)?dia\s+(\d{1,2})\b", normalized):
            day = int(raw)
            if 1 <= day <= 31 and day not in excluded_day_numbers:
                excluded_day_numbers.append(day)
        for raw in re.findall(r"(?:menos|exceto|nao|n[aã]o)\s+(?:no\s+)?dia\s+([a-z]+)\b", normalized):
            day = cls._DAY_WORDS.get(raw)
            if day and day not in excluded_day_numbers:
                excluded_day_numbers.append(day)

        changes_pending_confirmation = any(
            [
                rejects_current_slot,
                earliest_time,
                requested_period,
                requested_weekday,
                excluded_dates,
                excluded_day_numbers,
            ]
        )

        return AppointmentRequestConstraints(
            rejects_current_slot=rejects_current_slot,
            earliest_time=earliest_time,
            requested_period=requested_period,
            requested_weekday=requested_weekday,
            excluded_day_numbers=excluded_day_numbers,
            excluded_dates=excluded_dates,
            changes_pending_confirmation=changes_pending_confirmation,
        )

    @classmethod
    def build_datetime_str(cls, date_str: str, time_str: str) -> str:
        """Monta a string no formato esperado pela camada de agenda."""
        dt = datetime.strptime(f"{date_str} {time_str}", "%d/%m/%Y %H:%M")
        return dt.strftime("%d/%m/%Y %H:%M")
