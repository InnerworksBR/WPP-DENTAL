"""Testes do ConfigService."""


class TestConfigService:
    """Testa o serviço de configurações."""

    def test_load_plans(self):
        """Verifica se os planos são carregados do YAML."""
        from src.infrastructure.config.config_service import ConfigService

        # Reset singleton para teste
        ConfigService._instance = None
        config = ConfigService()

        plans = config.get_plans()
        assert len(plans) > 0, "Deve haver planos cadastrados"

    def test_get_plan_names(self):
        """Verifica se os nomes dos planos são retornados."""
        from src.infrastructure.config.config_service import ConfigService

        ConfigService._instance = None
        config = ConfigService()

        names = config.get_plan_names()
        assert isinstance(names, list)
        assert len(names) > 0

    def test_find_plan_fuzzy(self):
        """Testa busca fuzzy de plano."""
        from src.infrastructure.config.config_service import ConfigService

        ConfigService._instance = None
        config = ConfigService()

        # Deve encontrar "Amil Dental" buscando por "amil"
        plan = config.find_plan_fuzzy("amil")
        assert plan is not None
        assert "Amil" in plan["name"]

    def test_find_particular_alias(self):
        """Aceita particular quando o paciente diz que nao tem plano."""
        from src.infrastructure.config.config_service import ConfigService

        ConfigService._instance = None
        config = ConfigService()

        plan = config.find_plan_fuzzy("sem plano")
        assert plan is not None
        assert plan["name"] == "Particular"

    def test_find_plan_by_alias(self):
        """Aceita apelidos configurados para os convenios."""
        from src.infrastructure.config.config_service import ConfigService

        ConfigService._instance = None
        config = ConfigService()

        plan = config.find_plan_fuzzy("Rede UNNA")
        assert plan is not None
        assert plan["name"] == "Previan (Rede UNNA)"

    def test_referral_plan_exposes_target_and_message(self):
        """Retorna corretamente a profissional e a mensagem de encaminhamento."""
        from src.infrastructure.config.config_service import ConfigService

        ConfigService._instance = None
        config = ConfigService()

        target = config.get_plan_referral_target("Caixa de Saude de Sao Vicente")
        message = config.get_plan_referral_message(
            "Caixa de Saude de Sao Vicente",
            referral_to=target,
        )

        assert target == "Dra. Tarcilia"
        assert "Dra. Tarcilia" in message

    def test_extract_plan_from_long_sentence(self):
        """Extrai um convenio mesmo quando ele vem dentro de uma frase natural."""
        from src.infrastructure.config.config_service import ConfigService

        ConfigService._instance = None
        config = ConfigService()

        plan = config.extract_plan_from_text("voces atendem caixa de peculio?")
        assert plan is not None
        assert plan["name"] == "Caixa de Peculio de Sao Vicente"

    def test_get_periods(self):
        """Verifica se os períodos estão configurados corretamente."""
        from src.infrastructure.config.config_service import ConfigService

        ConfigService._instance = None
        config = ConfigService()

        periods = config.get_periods()
        assert "manhã" in periods
        assert "tarde" in periods
        assert "noite" in periods
        assert periods["manhã"]["start"] == "07:00"

    def test_get_message_template(self):
        """Testa se templates de mensagem são carregados e formatados."""
        from src.infrastructure.config.config_service import ConfigService

        ConfigService._instance = None
        config = ConfigService()

        msg = config.get_message(
            "greeting.returning_patient",
            patient_name="João"
        )
        assert "João" in msg

    def test_slot_duration(self):
        """Verifica duração do slot."""
        from src.infrastructure.config.config_service import ConfigService

        ConfigService._instance = None
        config = ConfigService()

        assert config.get_slot_duration() == 15

    def test_suggestions_count(self):
        """Verifica quantidade de sugestões."""
        from src.infrastructure.config.config_service import ConfigService

        ConfigService._instance = None
        config = ConfigService()

        assert config.get_suggestions_count() == 2

    def test_min_business_days_ahead(self):
        """Verifica a janela mínima de dias úteis antes das sugestões."""
        from src.infrastructure.config.config_service import ConfigService

        ConfigService._instance = None
        config = ConfigService()

        assert config.get_min_business_days_ahead() == 2
