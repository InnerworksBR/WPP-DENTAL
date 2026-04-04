"""Testes das tools de convenio."""

from src.interfaces.tools.config_tool import CheckPlanTool


class TestConfigTool:
    """Garante o aceite de atendimento particular."""

    def test_accepts_particular_alias(self):
        tool = CheckPlanTool()

        result = tool._run("sem plano")

        assert "Particular" in result
        assert "Ativo" in result
