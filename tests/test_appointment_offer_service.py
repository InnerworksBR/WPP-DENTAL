"""Testes da interpretacao de ofertas recentes de horarios."""

from src.domain.policies.appointment_offer_service import AppointmentOfferService


class TestAppointmentOfferService:
    """Garante que respostas curtas resolvam a opcao correta."""

    def test_extract_latest_offer(self):
        history = [
            {"role": "patient", "content": "Agendar"},
            {
                "role": "assistant",
                "content": (
                    "Cristian, temos duas opcoes disponiveis para agendar sua consulta "
                    "no dia 07/04/2026 pela manha: as 08:00 ou 08:15. Qual voce prefere?"
                ),
            },
        ]

        offer = AppointmentOfferService.extract_latest_offer(history)

        assert offer is not None
        assert offer.date_str == "07/04/2026"
        assert offer.times == ["08:00", "08:15"]

    def test_resolve_selection_from_short_hour_message(self):
        offer = AppointmentOfferService.extract_latest_offer(
            [
                {
                    "role": "assistant",
                    "content": (
                        "Temos disponibilidade no dia 07/04/2026: as 08:00 ou 08:15."
                    ),
                }
            ]
        )

        selected = AppointmentOfferService.resolve_selection("Pode ser as 8", offer)

        assert selected == "08:00"

    def test_resolve_selection_by_position(self):
        offer = AppointmentOfferService.extract_latest_offer(
            [
                {
                    "role": "assistant",
                    "content": (
                        "Temos disponibilidade no dia 07/04/2026: as 08:00 ou 08:15."
                    ),
                }
            ]
        )

        selected = AppointmentOfferService.resolve_selection("Quero a segunda opcao", offer)

        assert selected == "08:15"

    def test_resolve_selection_from_explicit_time_keeps_minute_precision(self):
        offer = AppointmentOfferService.extract_latest_offer(
            [
                {
                    "role": "assistant",
                    "content": (
                        "Temos disponibilidade no dia 07/04/2026: as 08:00 ou 08:15."
                    ),
                }
            ]
        )

        selected = AppointmentOfferService.resolve_selection("Pode ser 8:15", offer)

        assert selected == "08:15"

    def test_extract_latest_offer_ignores_confirmation_request(self):
        history = [
            {
                "role": "assistant",
                "content": "Temos disponibilidade no dia 07/04/2026: as 08:00 ou 08:15.",
            },
            {
                "role": "assistant",
                "content": (
                    "Cristian, encontrei esse horario para voce: 07/04/2026 as 08:00. "
                    "Posso confirmar sua consulta?"
                ),
            },
        ]

        offer = AppointmentOfferService.extract_latest_offer(history)

        assert offer is not None
        assert offer.times == ["08:00", "08:15"]

    def test_extract_latest_confirmation_request(self):
        confirmation = AppointmentOfferService.extract_latest_confirmation_request(
            [
                {
                    "role": "assistant",
                    "content": (
                        "Cristian, encontrei esse horario para voce: 07/04/2026 as 08:00. "
                        "Posso confirmar sua consulta?"
                    ),
                }
            ]
        )

        assert confirmation is not None
        assert confirmation.date_str == "07/04/2026"
        assert confirmation.time_str == "08:00"

    def test_extract_latest_offer_ignores_cancel_summary(self):
        offer = AppointmentOfferService.extract_latest_offer(
            [
                {
                    "role": "assistant",
                    "content": (
                        "Encontrei sua consulta:\n\n"
                        "Data: 07/04/2026\n"
                        "Horario: 08:00\n\n"
                        "Deseja realmente cancelar?"
                    ),
                }
            ]
        )

        assert offer is None

    def test_is_affirmative_confirmation(self):
        assert AppointmentOfferService.is_affirmative_confirmation("Sim, pode confirmar")
        assert not AppointmentOfferService.is_affirmative_confirmation("Nao, quero outro horario")
