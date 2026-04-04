"""Tool CrewAI para consulta de configurações (planos, regras)."""

from typing import Optional, Type

from pydantic import BaseModel, Field

from ...infrastructure.config.config_service import ConfigService


class CheckPlanInput(BaseModel):
    """Input para verificar plano."""
    plan_name: str = Field(..., description="Nome do convênio/plano informado pelo paciente")


class CheckPlanTool:
    """Verifica se um plano/convênio é atendido pela doutora."""

    name: str = "verificar_convenio"
    description: str = (
        "Verifica se um convênio/plano odontológico é atendido pela doutora. "
        "Retorna se o plano existe, se tem restrições de procedimentos, "
        "e se deve ser encaminhado para outra profissional. "
        "Use esta ferramenta quando o paciente informar seu convênio."
    )
    args_schema: Type[BaseModel] = CheckPlanInput

    def _run(self, plan_name: str) -> str:
        config = ConfigService()

        # Tenta busca exata primeiro, depois fuzzy
        plan = config.get_plan_by_name(plan_name)
        if plan is None:
            plan = config.find_plan_fuzzy(plan_name)

        if plan is None:
            available = ", ".join(config.get_plan_names())
            return (
                f"Convênio '{plan_name}' NÃO encontrado.\n"
                f"Convênios aceitos: {available}\n"
                f"Peça ao paciente para verificar o nome correto do convênio."
            )

        result = f"Convênio: {plan['name']}\n"
        result += f"Status: {'Ativo' if plan.get('active', True) else 'Inativo'}\n"

        if plan.get("referral", False):
            referral_msg = plan.get(
                "referral_message",
                "Este convênio deve ser encaminhado para outra profissional."
            )
            result += f"⚠️ ENCAMINHAMENTO NECESSÁRIO: {referral_msg}\n"
            result += "AÇÃO OBRIGATÓRIA: Alertar a doutora e informar o paciente sobre o encaminhamento."
            return result

        restrictions = plan.get("restrictions", [])
        if restrictions:
            result += f"Restrições: {', '.join(restrictions)}\n"
            result += "Estes procedimentos NÃO são cobertos por este convênio."
        else:
            result += "Sem restrições de procedimentos."

        return result


class ListPlansInput(BaseModel):
    """Input vazio para listar planos."""
    pass


class ListPlansTool:
    """Lista todos os planos/convênios aceitos."""

    name: str = "listar_convenios"
    description: str = (
        "Lista todos os convênios/planos odontológicos atendidos pela doutora. "
        "Use quando o paciente perguntar quais convênios são aceitos."
    )
    args_schema: Type[BaseModel] = ListPlansInput

    def _run(self) -> str:
        config = ConfigService()
        plans = config.get_plans()

        if not plans:
            return "Nenhum convênio cadastrado no momento."

        result = "Convênios atendidos:\n"
        for plan in plans:
            if not plan.get("referral", False):
                result += f"  ✅ {plan['name']}\n"

        referral_plans = [p for p in plans if p.get("referral", False)]
        if referral_plans:
            result += "\nConvênios com encaminhamento:\n"
            for plan in referral_plans:
                result += f"  🔄 {plan['name']}\n"

        return result
