"""Interpretacao deterministica de ofertas e confirmacoes de horarios."""

import re
import unicodedata
from dataclasses import dataclass
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


class AppointmentOfferService:
    """Resolve respostas curtas do paciente para horarios ja ofertados."""

    _DATE_PATTERN = re.compile(r"\b(\d{2}/\d{2}(?:/\d{4})?)\b")
    _TIME_PATTERN = re.compile(r"\b(\d{1,2}):(\d{2})\b")
    _HOUR_ONLY_PATTERN = re.compile(r"(?:\bas\b|\ba?s?\b|\b)\s*(\d{1,2})(?:h\b| horas?\b)?")
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

    @staticmethod
    def _normalize(text: str) -> str:
        normalized = unicodedata.normalize("NFKD", text or "")
        normalized = normalized.encode("ascii", "ignore").decode("ascii").lower()
        return re.sub(r"\s+", " ", normalized).strip()

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
                date_str = f"{date_str}/{datetime.now().year}"

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
                date_str = f"{date_str}/{datetime.now().year}"

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

        for hour_match in cls._HOUR_ONLY_PATTERN.findall(normalized):
            candidate = f"{int(hour_match):02d}:00"
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
        return any(token in normalized for token in cls._AFFIRMATIVE_CONFIRMATION_TOKENS)

    @classmethod
    def build_datetime_str(cls, date_str: str, time_str: str) -> str:
        """Monta a string no formato esperado pela camada de agenda."""
        dt = datetime.strptime(f"{date_str} {time_str}", "%d/%m/%Y %H:%M")
        return dt.strftime("%d/%m/%Y %H:%M")
