"""Serviço de leitura de configurações YAML."""

import os
from difflib import get_close_matches
import yaml
from pathlib import Path
from typing import Any, Optional


class ConfigService:
    """Lê e fornece acesso às configurações do sistema."""

    _instance: Optional["ConfigService"] = None
    _configs: dict[str, Any] = {}
    _PRIVATE_PLAN_ALIASES = {
        "particular",
        "consulta particular",
        "atendimento particular",
        "particular mesmo",
        "sem plano",
        "sem convenio",
        "sem convênio",
        "nao tenho plano",
        "não tenho plano",
        "nao tenho convenio",
        "não tenho convenio",
        "não tenho convênio",
    }

    def __new__(cls) -> "ConfigService":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load_configs()
        return cls._instance

    def _resolve_env_vars(self, value: Any) -> Any:
        """Resolve variáveis de ambiente no formato ${VAR_NAME}."""
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            env_var = value[2:-1]
            return os.getenv(env_var, value)
        if isinstance(value, dict):
            return {k: self._resolve_env_vars(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve_env_vars(item) for item in value]
        return value

    def _load_configs(self) -> None:
        """Carrega todos os arquivos YAML da pasta config/."""
        config_dir = Path(__file__).parent.parent.parent.parent / "config"
        for yaml_file in config_dir.glob("*.yaml"):
            key = yaml_file.stem  # plans, settings, messages
            with open(yaml_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                self._configs[key] = self._resolve_env_vars(data)

    def reload(self) -> None:
        """Recarrega as configurações (hot reload)."""
        self._configs.clear()
        self._load_configs()

    def get_settings(self) -> dict[str, Any]:
        """Retorna as configurações gerais."""
        return self._configs.get("settings", {})

    def get_plans(self) -> list[dict[str, Any]]:
        """Retorna a lista de planos/convênios ativos."""
        plans_config = self._configs.get("plans", {})
        all_plans = plans_config.get("plans", [])
        return [p for p in all_plans if p.get("active", True)]

    def get_plan_names(self) -> list[str]:
        """Retorna apenas os nomes dos planos ativos."""
        return [p["name"] for p in self.get_plans()]

    def get_plan_by_name(self, name: str) -> Optional[dict[str, Any]]:
        """Busca um plano pelo nome (case-insensitive)."""
        name_lower = name.lower().strip()
        if name_lower in self._PRIVATE_PLAN_ALIASES:
            name_lower = "particular"
        for plan in self.get_plans():
            if plan["name"].lower().strip() == name_lower:
                return plan
        return None

    def find_plan_fuzzy(self, query: str) -> Optional[dict[str, Any]]:
        """Busca um plano com correspondência parcial."""
        query_lower = query.lower().strip()
        if query_lower in self._PRIVATE_PLAN_ALIASES:
            return self.get_plan_by_name("Particular")

        close_alias = get_close_matches(
            query_lower,
            list(self._PRIVATE_PLAN_ALIASES),
            n=1,
            cutoff=0.8,
        )
        if close_alias:
            return self.get_plan_by_name("Particular")

        for plan in self.get_plans():
            plan_name_lower = plan["name"].lower()
            if query_lower in plan_name_lower or plan_name_lower in query_lower:
                return plan

        close_match = get_close_matches(
            query_lower,
            [plan["name"].lower().strip() for plan in self.get_plans()],
            n=1,
            cutoff=0.72,
        )
        if close_match:
            return self.get_plan_by_name(close_match[0])

        return None

    def get_referral_plans(self) -> list[dict[str, Any]]:
        """Retorna planos que devem ser encaminhados para outra doutora."""
        return [p for p in self.get_plans() if p.get("referral", False)]

    def is_referral_plan(self, plan_name: str) -> bool:
        """Verifica se o plano deve ser encaminhado."""
        plan = self.get_plan_by_name(plan_name)
        if plan is None:
            plan = self.find_plan_fuzzy(plan_name)
        return plan.get("referral", False) if plan else False

    def get_plan_restrictions(self, plan_name: str) -> list[str]:
        """Retorna as restrições de procedimentos de um plano."""
        plan = self.get_plan_by_name(plan_name)
        if plan is None:
            plan = self.find_plan_fuzzy(plan_name)
        return plan.get("restrictions", []) if plan else []

    def get_message(self, path: str, **kwargs: Any) -> str:
        """
        Retorna um template de mensagem formatado.
        path: chave pontilhada, ex: 'greeting.new_patient'
        kwargs: variáveis para interpolação
        """
        messages = self._configs.get("messages", {})
        keys = path.split(".")
        value = messages
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key, "")
            else:
                return ""
        if isinstance(value, str):
            try:
                return value.strip().format(**kwargs)
            except KeyError:
                return value.strip()
        return str(value)

    def get_doctor_name(self) -> str:
        """Retorna o nome da doutora."""
        return self.get_settings().get("doctor", {}).get("name", "Dra.")

    def get_doctor_phone(self) -> str:
        """Retorna o telefone da doutora para alertas."""
        phone = self.get_settings().get("doctor", {}).get("phone", "")
        if not phone or phone.startswith("${"):
            phone = os.getenv("DOCTOR_PHONE", "")
        return phone

    def get_doctor_address(self) -> str:
        """Retorna o endereço da clínica."""
        return self.get_settings().get("doctor", {}).get("address", "")

    def get_calendar_id(self) -> str:
        """Retorna o ID do Google Calendar."""
        cal_id = self.get_settings().get("doctor", {}).get("calendar_id", "primary")
        if cal_id.startswith("${"):
            cal_id = os.getenv("GOOGLE_CALENDAR_ID", "primary")
        return cal_id

    def get_periods(self) -> dict[str, dict[str, str]]:
        """Retorna os períodos do dia configurados."""
        return self.get_settings().get("periods", {
            "manhã": {"start": "07:00", "end": "12:00"},
            "tarde": {"start": "12:00", "end": "18:00"},
            "noite": {"start": "18:00", "end": "21:00"},
        })

    def get_slot_duration(self) -> int:
        """Retorna a duração do slot em minutos."""
        return self.get_settings().get("scheduling", {}).get("slot_duration_minutes", 15)

    def get_suggestions_count(self) -> int:
        """Retorna quantos horários sugerir."""
        return self.get_settings().get("scheduling", {}).get("suggestions_count", 2)

    def get_max_days_ahead(self) -> int:
        """Retorna o limite de dias para agendamento futuro."""
        return self.get_settings().get("scheduling", {}).get("max_days_ahead", 30)

    def get_min_business_days_ahead(self) -> int:
        """Retorna a janela minima de dias uteis antes do primeiro horario sugerido."""
        return self.get_settings().get("scheduling", {}).get("min_business_days_ahead", 2)

    def get_openai_model(self) -> str:
        """Retorna o modelo OpenAI configurado."""
        return self.get_settings().get("openai", {}).get("model", "gpt-4o-mini")

    def get_openai_temperature(self) -> float:
        """Retorna a temperatura do modelo."""
        return self.get_settings().get("openai", {}).get("temperature", 0.2)
