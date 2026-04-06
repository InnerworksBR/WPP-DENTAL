"""Testes da camada de contexto reutilizavel do Rasa."""


class TestRasaContextService:
    """Cobre regras de contexto reaproveitadas pelas actions do Rasa."""

    def test_get_clinic_context_exposes_address_and_plans(self, monkeypatch):
        from src.application.services.rasa_context_service import RasaContextService

        service = RasaContextService()
        monkeypatch.setattr(service.patients, "resolve_name", lambda phone, fallback_name="": "Cristian")

        context = service.get_clinic_context("5511999999999")

        assert context["doctor_name"] == "Dra. Priscila"
        assert "Benjamin Constant" in context["clinic_address"]
        assert "Unimed Odonto" in context["accepted_plans_text"]
        assert "Caixa de Peculio de Sao Vicente" in context["referral_plans_text"]

    def test_check_plan_info_marks_tarcilia_referral(self):
        from src.application.services.rasa_context_service import RasaContextService

        service = RasaContextService()

        result = service.check_plan_info("voces atendem caixa de peculio?")

        assert result["plan_found"] is True
        assert result["plan_name"] == "Caixa de Peculio de Sao Vicente"
        assert result["plan_is_referral"] is True
        assert result["referral_target"] == "Dra. Tarcilia"

    def test_check_procedure_policy_marks_card_photo_when_plan_is_allowed(self):
        from src.application.services.rasa_context_service import RasaContextService

        service = RasaContextService()

        result = service.check_procedure_policy("ortodontia pela sulamerica")

        assert result["procedure_found"] is True
        assert result["procedure_label"] == "ortodontia"
        assert result["procedure_status"] == "allowed_with_card_photo"
        assert result["matched_plan_name"] == "Sulamerica"

    def test_check_procedure_policy_marks_only_particular(self):
        from src.application.services.rasa_context_service import RasaContextService

        service = RasaContextService()

        result = service.check_procedure_policy("extracao de siso")

        assert result["procedure_found"] is True
        assert result["procedure_status"] == "only_particular"

    def test_send_referral_alert_uses_only_required_fields(self, monkeypatch):
        from src.application.services.rasa_context_service import RasaContextService

        service = RasaContextService()
        captured = {}

        monkeypatch.setattr(service.patients, "resolve_name", lambda phone: "Cristian")
        monkeypatch.setattr(service.patients, "upsert", lambda phone, name, plan=None: None)
        monkeypatch.setattr(
            service.patients,
            "save_interaction",
            lambda phone, interaction_type, summary: captured.setdefault("interaction", summary),
        )

        def fake_send_referral_alert(**kwargs):
            captured.update(kwargs)
            return True

        monkeypatch.setattr(service.alerts, "send_referral_alert", fake_send_referral_alert)

        sent = service.send_referral(
            patient_phone="5511999999999",
            patient_name="Cristian",
            consultation_reason="avaliacao",
            referral_to="Dra. Tarcilia",
        )

        assert sent is True
        assert captured["patient_name"] == "Cristian"
        assert captured["patient_phone"] == "5511999999999"
        assert captured["consultation_reason"] == "avaliacao"
        assert captured["referral_to"] == "Dra. Tarcilia"
        assert "Dra. Tarcilia" in captured["interaction"]
