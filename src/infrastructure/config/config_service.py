"""Serviço de leitura de configurações YAML."""

import os
from difflib import SequenceMatcher, get_close_matches
import re
import unicodedata
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

    @staticmethod
    def _normalize_lookup(text: str) -> str:
        normalized = unicodedata.normalize("NFKD", text or "")
        normalized = normalized.encode("ascii", "ignore").decode("ascii").lower()
        return re.sub(r"\s+", " ", normalized).strip()

    def _iter_plan_candidates(self, plan: dict[str, Any]) -> list[str]:
        candidates = [str(plan.get("name", "")).strip()]
        aliases = plan.get("aliases", [])
        if isinstance(aliases, list):
            candidates.extend(str(alias).strip() for alias in aliases if str(alias).strip())
        return [candidate for candidate in candidates if candidate]

    @staticmethod
    def _informative_tokens(text: str) -> list[str]:
        stopwords = {
            "a", "as", "o", "os",
            "de", "da", "das", "do", "dos",
            "e", "ou", "por", "para",
            "pela", "pelo", "com",
            "no", "na", "nos", "nas",
        }
        return [
            token
            for token in re.findall(r"[a-z0-9]+", text or "")
            if token and token not in stopwords
        ]

    def _resolve_plan(self, value: str) -> Optional[dict[str, Any]]:
        target = self._normalize_lookup(value)
        if target in {self._normalize_lookup(alias) for alias in self._PRIVATE_PLAN_ALIASES}:
            target = "particular"

        for plan in self.get_plans():
            if any(self._normalize_lookup(candidate) == target for candidate in self._iter_plan_candidates(plan)):
                return plan
        return None

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
        return self._resolve_plan(name)

    def find_plan_fuzzy(self, query: str) -> Optional[dict[str, Any]]:
        """Busca um plano com correspondência parcial."""
        query_lower = self._normalize_lookup(query)
        private_aliases = [self._normalize_lookup(alias) for alias in self._PRIVATE_PLAN_ALIASES]
        if query_lower in private_aliases:
            return self.get_plan_by_name("Particular")

        close_alias = get_close_matches(
            query_lower,
            private_aliases,
            n=1,
            cutoff=0.8,
        )
        if close_alias:
            return self.get_plan_by_name("Particular")

        candidate_map: dict[str, dict[str, Any]] = {}
        for plan in self.get_plans():
            for candidate in self._iter_plan_candidates(plan):
                normalized_candidate = self._normalize_lookup(candidate)
                candidate_map[normalized_candidate] = plan
                if query_lower in normalized_candidate or normalized_candidate in query_lower:
                    return plan

        close_match = get_close_matches(
            query_lower,
            list(candidate_map.keys()),
            n=1,
            cutoff=0.72,
        )
        if close_match:
            return candidate_map[close_match[0]]

        return None

    def extract_plan_from_text(self, text: str) -> Optional[dict[str, Any]]:
        """Extrai um convenio citado dentro de uma frase mais longa."""
        normalized_text = self._normalize_lookup(text)
        if not normalized_text:
            return None

        exact = self.get_plan_by_name(normalized_text)
        if exact is not None:
            return exact

        fuzzy = self.find_plan_fuzzy(normalized_text)
        if fuzzy is not None:
            return fuzzy

        text_tokens = set(self._informative_tokens(normalized_text))
        best_plan: Optional[dict[str, Any]] = None
        best_score = 0.0

        for plan in self.get_plans():
            for candidate in self._iter_plan_candidates(plan):
                normalized_candidate = self._normalize_lookup(candidate)
                if not normalized_candidate:
                    continue

                if normalized_candidate in normalized_text:
                    return plan

                candidate_tokens = set(self._informative_tokens(normalized_candidate))
                if not candidate_tokens:
                    continue

                matched_tokens = candidate_tokens.intersection(text_tokens)
                coverage = len(matched_tokens) / len(candidate_tokens)
                if coverage < 0.6:
                    continue

                similarity = 0.0
                for fragment in re.split(r"[?!.,;:()\\-]+", normalized_text):
                    fragment = fragment.strip()
                    if not fragment:
                        continue
                    similarity = max(
                        similarity,
                        SequenceMatcher(None, fragment, normalized_candidate).ratio(),
                    )

                score = coverage + (similarity * 0.25)
                if score > best_score:
                    best_score = score
                    best_plan = plan

        return best_plan

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

    def get_plan_referral_target(self, plan_name: str) -> str:
        """Retorna o nome da profissional de encaminhamento para o plano."""
        plan = self.get_plan_by_name(plan_name)
        if plan is None:
            plan = self.find_plan_fuzzy(plan_name)
        return str(plan.get("referral_to", "")).strip() if plan else ""

    def get_plan_referral_message(self, plan_name: str, **kwargs: Any) -> str:
        """Retorna a mensagem configurada de encaminhamento para o plano."""
        plan = self.get_plan_by_name(plan_name)
        if plan is None:
            plan = self.find_plan_fuzzy(plan_name)
        if not plan:
            return ""

        template = str(plan.get("referral_message", "")).strip()
        if not template:
            return ""

        try:
            return template.format(**kwargs)
        except KeyError:
            return template

    def get_procedure_rules(self) -> list[dict[str, Any]]:
        """Retorna as regras operacionais por procedimento."""
        rules_config = self._configs.get("procedure_rules", {})
        all_rules = rules_config.get("rules", [])
        return [rule for rule in all_rules if isinstance(rule, dict)]

    def get_procedure_rule(self, rule_key: str) -> Optional[dict[str, Any]]:
        """Busca uma regra operacional pelo identificador."""
        normalized_key = self._normalize_lookup(rule_key)
        for rule in self.get_procedure_rules():
            if self._normalize_lookup(str(rule.get("key", ""))) == normalized_key:
                return rule
        return None

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

    def get_min_patient_age(self) -> int:
        """Retorna a idade mínima de atendimento configurada."""
        return int(self.get_settings().get("clinic", {}).get("min_patient_age", 8))

    def get_working_days(self) -> str:
        """Retorna a descrição dos dias de atendimento."""
        return str(self.get_settings().get("clinic", {}).get("working_days", "segunda a sexta-feira"))
