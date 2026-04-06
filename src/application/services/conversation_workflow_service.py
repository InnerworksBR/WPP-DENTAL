"""Motor deterministico do fluxo de atendimento via WhatsApp."""

from __future__ import annotations

from difflib import SequenceMatcher
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Any

from .appointment_confirmation_service import AppointmentConfirmationService
from .conversation_state_service import ConversationState, ConversationStateService
from .patient_service import PatientService
from ...infrastructure.config.config_service import ConfigService
from ...infrastructure.integrations.alert_service import AlertService
from ...infrastructure.integrations.calendar_service import CalendarService, SAO_PAULO_TZ


class ConversationWorkflowService:
    """Orquestra o atendimento usando regras de negocio e estado persistido."""

    _INTENT_PATTERNS = {
        "address": (
            "endereco",
            "endereço",
            "qual o endereco",
            "qual o endereço",
            "qual endereco",
            "qual endereço",
            "onde fica",
            "onde e",
            "onde é",
            "local da clinica",
            "local da consulta",
        ),
        "reschedule": (
            "remarcar",
            "reagendar",
            "mudar horario",
            "trocar horario",
            "alterar horario",
        ),
        "cancel": (
            "cancelar",
            "desmarcar",
            "nao vou poder",
        ),
        "query": (
            "consultar",
            "minha consulta",
            "proxima consulta",
            "qual dia",
            "quando e",
            "quando eh",
            "que horas",
        ),
        "schedule": (
            "agendar",
            "marcar",
            "consulta",
            "horario",
        ),
    }
    _PLAN_QUESTION_HINTS = (
        "atende",
        "atendem",
        "aceita",
        "aceitam",
        "trabalha com",
        "trabalham com",
        "tem convenio",
        "tem plano",
    )
    _PLAN_LIST_HINTS = (
        "quais convenios",
        "quais convenios voces atendem",
        "quais planos",
        "planos aceitos",
        "planos atendidos",
        "convenios aceitos",
        "convenios atendidos",
        "lista de convenios",
        "lista de planos",
    )
    _PLAN_CONTEXT_HINTS = (
        "convenio",
        "convenios",
        "plano",
        "planos",
        "odonto",
    )
    _PERIOD_ALIASES = {
        "manha": "manha",
        "manhã": "manha",
        "de manha": "manha",
        "pela manha": "manha",
        "madrugada": "manha",
        "tarde": "tarde",
        "a tarde": "tarde",
        "de tarde": "tarde",
        "pela tarde": "tarde",
        "noite": "noite",
        "a noite": "noite",
        "de noite": "noite",
        "pela noite": "noite",
    }
    _YES_TOKENS = ("sim", "ssim", "simm", "pode", "confirmo", "confirmar", "quero", "isso", "ok", "okay")
    _NO_TOKENS = ("nao", "não", "deixa", "melhor nao", "outro", "cancelar")
    _DATE_PATTERN = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
    _NAME_PATTERNS = (
        re.compile(r"\bmeu nome e ([a-zA-ZÀ-ÿ' ]{3,})", re.IGNORECASE),
        re.compile(r"\bme chamo ([a-zA-ZÀ-ÿ' ]{3,})", re.IGNORECASE),
        re.compile(r"\bsou ([a-zA-ZÀ-ÿ' ]{3,})", re.IGNORECASE),
    )
    _NON_NAME_TOKENS = {
        "agendar",
        "consulta",
        "remarcar",
        "cancelar",
        "particular",
        "manha",
        "manhã",
        "tarde",
        "noite",
        "canal",
        "molar",
        "siso",
        "protese",
        "prÃ³tese",
        "ortodontia",
        "aparelho",
        "odontoprev",
        "bradesco",
        "previan",
        "unimed",
        "sulamerica",
        "sulamÃ©rica",
        "uniodonto",
        "metlife",
        "caixa",
        "oi",
        "ola",
        "olá",
    }

    def __init__(self) -> None:
        self.config = ConfigService()
        self.calendar = CalendarService()
        self.patients = PatientService()

    @staticmethod
    def _normalize(text: str) -> str:
        normalized = unicodedata.normalize("NFKD", text or "")
        normalized = normalized.encode("ascii", "ignore").decode("ascii").lower()
        return re.sub(r"\s+", " ", normalized).strip()

    @classmethod
    def _similar_enough(cls, left: str, right: str, threshold: float = 0.8) -> bool:
        """Aceita pequenas variacoes de digitacao para comparacoes curtas."""
        if not left or not right:
            return False
        if left == right:
            return True
        if left[0] != right[0]:
            return False
        if min(len(left), len(right)) >= 4 and left[:2] != right[:2]:
            return False
        if abs(len(left) - len(right)) > max(1, int(max(len(left), len(right)) * 0.25)):
            return False
        return SequenceMatcher(None, left, right).ratio() >= threshold

    @classmethod
    def _contains_keyword(cls, normalized_text: str, keyword: str) -> bool:
        """Verifica uma keyword com tolerancia leve a erros de digitacao."""
        normalized_keyword = cls._normalize(keyword)
        if not normalized_text or not normalized_keyword:
            return False

        if normalized_keyword in normalized_text:
            return True

        text_tokens = normalized_text.split()
        keyword_tokens = normalized_keyword.split()

        if len(keyword_tokens) == 1:
            token = keyword_tokens[0]
            return any(cls._similar_enough(candidate, token) for candidate in text_tokens)

        window_size = len(keyword_tokens)
        for index in range(len(text_tokens) - window_size + 1):
            window_tokens = text_tokens[index:index + window_size]
            if all(
                cls._similar_enough(window_token, keyword_token)
                for window_token, keyword_token in zip(window_tokens, keyword_tokens)
            ):
                return True
            if cls._similar_enough(" ".join(window_tokens), normalized_keyword, threshold=0.84):
                return True

        return False

    def _detect_intent(self, text: str) -> str:
        normalized = self._normalize(text)
        if not normalized:
            return ""

        for keyword in self._INTENT_PATTERNS["address"]:
            if self._contains_keyword(normalized, keyword):
                return "address"

        for keyword in self._INTENT_PATTERNS["reschedule"]:
            if self._contains_keyword(normalized, keyword):
                return "reschedule"

        for keyword in self._INTENT_PATTERNS["cancel"]:
            if self._contains_keyword(normalized, keyword):
                return "cancel"

        for keyword in (
            "minha consulta",
            "proxima consulta",
            "qual dia",
            "quando e",
            "quando eh",
            "que horas",
        ):
            if self._contains_keyword(normalized, keyword):
                return "query"

        if self._contains_keyword(normalized, "consultar") and not any(
            self._contains_keyword(normalized, keyword)
            for keyword in ("agendar", "marcar", "consulta", "horario")
        ):
            return "query"

        for keyword in self._INTENT_PATTERNS["schedule"]:
            if self._contains_keyword(normalized, keyword):
                return "schedule"

        return ""

    def _extract_name(self, message: str, contact_name: str = "") -> str:
        raw_message = (message or "").strip()
        for pattern in self._NAME_PATTERNS:
            match = pattern.search(raw_message)
            if match:
                candidate = match.group(1).strip(" .,!?:;")
                if candidate:
                    return self._cleanup_name(candidate)

        words = [word for word in re.findall(r"[A-Za-zÀ-ÿ']+", raw_message) if word]
        if 1 < len(words) <= 4 and not any(
            self._normalize(word) in self._NON_NAME_TOKENS for word in words
        ):
            return self._cleanup_name(" ".join(words))

        if contact_name and not contact_name.isdigit():
            return self._cleanup_name(contact_name)
        return ""

    @staticmethod
    def _cleanup_name(name: str) -> str:
        cleaned = re.sub(r"\s+", " ", name).strip(" .,!?:;")
        return cleaned.title()

    def _extract_period(self, text: str) -> str:
        normalized = self._normalize(text)
        for alias, canonical in self._PERIOD_ALIASES.items():
            if self._contains_keyword(normalized, alias):
                return canonical
        return ""

    def _extract_date(self, text: str) -> str:
        match = self._DATE_PATTERN.search(text or "")
        if match is None:
            return ""

        day = int(match.group(1))
        month = int(match.group(2))
        year_raw = match.group(3)
        now_sp = datetime.now(SAO_PAULO_TZ)
        if year_raw:
            year = int(year_raw)
            if year < 100:
                year += 2000
        else:
            year = now_sp.year

        try:
            parsed = datetime(year, month, day, tzinfo=SAO_PAULO_TZ)
        except ValueError:
            return ""

        if not year_raw and parsed.date() < now_sp.date():
            parsed = parsed.replace(year=parsed.year + 1)
        return parsed.strftime("%d/%m/%Y")

    def _extract_plan_name(self, text: str, current_plan: str = "") -> str:
        plan = self.config.get_plan_by_name(text)
        if plan is None:
            plan = self.config.find_plan_fuzzy(text)
        if plan:
            return str(plan["name"]).strip()
        return current_plan

    def _detect_procedure_rule(self, text: str) -> dict[str, Any] | None:
        normalized = self._normalize(text)
        if not normalized:
            return None

        for rule in self.config.get_procedure_rules():
            keywords = rule.get("keywords", [])
            if any(self._contains_keyword(normalized, str(keyword)) for keyword in keywords):
                return rule
        return None

    @staticmethod
    def _capitalize_label(label: str) -> str:
        cleaned = (label or "").strip()
        return f"{cleaned[:1].upper()}{cleaned[1:]}" if cleaned else ""

    def _canonical_plan_name(self, plan_name: str) -> str:
        plan = self.config.get_plan_by_name(plan_name)
        if plan is None:
            plan = self.config.find_plan_fuzzy(plan_name)
        return str(plan["name"]).strip() if plan else str(plan_name or "").strip()

    def _format_allowed_plans(self, allowed_plans: list[str]) -> str:
        rendered: list[str] = []
        for plan_name in allowed_plans:
            canonical = self._canonical_plan_name(plan_name)
            if canonical and canonical not in rendered:
                rendered.append(canonical)
        return ", ".join(rendered)

    def _plan_is_allowed(self, current_plan: str, allowed_plans: list[str]) -> bool:
        current_canonical = self._normalize(self._canonical_plan_name(current_plan))
        allowed_canonical = {
            self._normalize(self._canonical_plan_name(plan_name))
            for plan_name in allowed_plans
            if str(plan_name).strip()
        }
        return bool(current_canonical) and current_canonical in allowed_canonical

    @staticmethod
    def _extract_freeform_reason(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip(" .,!?:;")).strip()

    def _send_operational_alert(
        self,
        *,
        phone: str,
        patient_name: str,
        summary: str,
        reason: str,
        last_message: str,
    ) -> None:
        try:
            AlertService().send_alert(
                patient_name=patient_name or "Nao informado",
                patient_phone=phone,
                summary=summary,
                reason=reason,
                last_message=last_message,
            )
        except Exception:
            # O atendimento ao paciente nao deve falhar por causa do alerta interno.
            return

    def _finalize_plan_referral(
        self,
        phone: str,
        state: ConversationState,
        patient_message: str,
    ) -> str:
        referral_to = self.config.get_plan_referral_target(state.plan_name) or "profissional parceira"
        reason_text = state.requested_reason or self._extract_freeform_reason(patient_message) or "Motivo nao informado"
        summary = f"Encaminhamento para {referral_to}. Motivo: {reason_text}."

        self.patients.upsert(phone, state.patient_name, state.plan_name)
        self.patients.save_interaction(phone, "referral", summary)
        try:
            AlertService().send_referral_alert(
                patient_name=state.patient_name,
                patient_phone=phone,
                consultation_reason=reason_text,
                referral_to=referral_to,
            )
        except Exception:
            # O atendimento ao paciente nao deve falhar por causa do alerta interno.
            pass

        message = self.config.get_plan_referral_message(
            state.plan_name,
            referral_to=referral_to,
            patient_name=state.patient_name,
        ).strip()
        if not message:
            message = (
                f"Esse convenio e atendido pela {referral_to}. "
                "Vou encaminhar seu nome, telefone e motivo da consulta para a equipe dela, "
                "e ela deve falar com voce em breve."
            )

        ConversationStateService.clear(phone)
        return message

    def _handle_referral_reason(
        self,
        phone: str,
        state: ConversationState,
        patient_message: str,
    ) -> str:
        reason_text = self._extract_freeform_reason(patient_message)
        if not reason_text:
            referral_to = self.config.get_plan_referral_target(state.plan_name) or "profissional parceira"
            return self.config.get_message(
                "referral.ask_reason",
                referral_to=referral_to,
            ).strip()

        state.requested_reason = reason_text
        return self._finalize_plan_referral(phone, state, patient_message)

    def _handle_procedure_rule(
        self,
        phone: str,
        state: ConversationState,
        patient_message: str,
        rule: dict[str, Any],
    ) -> str | None:
        label = str(rule.get("label", "esse procedimento")).strip()
        allowed_plans = [str(plan).strip() for plan in rule.get("allowed_plans", []) if str(plan).strip()]
        allowed_plans_label = self._format_allowed_plans(allowed_plans)

        if rule.get("not_performed", False):
            ConversationStateService.clear(phone)
            return self.config.get_message(
                "procedure_rules.not_performed",
                procedure_label=label,
            ).strip()

        if allowed_plans and not self._plan_is_allowed(state.plan_name, allowed_plans):
            if self._plan_is_allowed("Particular", allowed_plans):
                state.stage = "awaiting_plan"
                state.plan_name = ""
                ConversationStateService.save(phone, state)
                return self.config.get_message(
                    "procedure_rules.only_particular",
                    procedure_label=label,
                    procedure_label_capitalized=self._capitalize_label(label),
                ).strip()

            ConversationStateService.clear(phone)
            return self.config.get_message(
                "procedure_rules.only_allowed_plans",
                procedure_label=label,
                allowed_plans=allowed_plans_label,
            ).strip()

        if rule.get("requires_card_photo", False):
            summary = (
                f"Paciente deseja {label} pelo convenio {state.plan_name}. "
                "Solicitar foto da carteirinha para conferencia."
            )
            self._send_operational_alert(
                phone=phone,
                patient_name=state.patient_name,
                summary=summary,
                reason="triagem_procedimento",
                last_message=patient_message,
            )
            ConversationStateService.clear(phone)
            return self.config.get_message(
                "procedure_rules.card_photo_required",
                procedure_label=label,
                plan_name=state.plan_name,
            ).strip()

        return None

    def _ask_name(self, is_first_message: bool) -> str:
        if is_first_message:
            return self.config.get_message(
                "greeting.new_patient",
                doctor_name=self.config.get_doctor_name(),
            ).strip()
        return "Para continuar seu atendimento, pode me informar seu nome completo?"

    def _ask_intent(self, patient_name: str, is_first_message: bool) -> str:
        first_name = patient_name.split()[0] if patient_name else ""
        if is_first_message:
            return (
                f"Oi, {first_name}! 😊 Eu sou a assistente virtual da clinica da "
                f"{self.config.get_doctor_name()}. Como posso te ajudar com sua consulta hoje?"
            )
        return (
            f"{first_name}, posso te ajudar a agendar, remarcar, cancelar "
            "ou consultar sua consulta. O que voce gostaria de fazer?"
        )

    def _ask_plan(self, patient_name: str) -> str:
        return self.config.get_message(
            "ask_plan.prompt",
            patient_name=patient_name.split()[0] if patient_name else "voce",
        ).strip()

    def _ask_period(self, patient_name: str) -> str:
        first_name = patient_name.split()[0] if patient_name else ""
        prefix = f"{first_name}, " if first_name else ""
        return (
            f"{prefix}qual periodo voce prefere para a consulta? 🕒\n\n"
            "Pode ser manha, tarde ou noite. "
            "Se quiser, tambem pode me informar uma data no formato DD/MM/AAAA."
        )

    def _format_available_plans(self) -> str:
        plans = [name for name in self.config.get_plan_names() if name]
        return ", ".join(plans)

    def _find_next_available_slots(self, period: str) -> list[dict[str, Any]]:
        target = datetime.now(SAO_PAULO_TZ)
        min_business_days = self.config.get_min_business_days_ahead()
        max_days_ahead = self.config.get_max_days_ahead()
        suggestions_count = self.config.get_suggestions_count()

        business_days_counted = 0
        while business_days_counted < min_business_days:
            target += timedelta(days=1)
            if target.weekday() < 5:
                business_days_counted += 1

        for _ in range(max_days_ahead):
            while target.weekday() >= 5:
                target += timedelta(days=1)

            slots = self.calendar.get_available_slots(target, period)
            if slots:
                return slots[:suggestions_count]
            target += timedelta(days=1)

        return []

    def _format_slot_offer(self, period: str, date_str: str, slots: list[dict[str, Any]]) -> str:
        times = [slot["start"].strftime("%H:%M") for slot in slots]
        if len(times) == 1:
            return (
                f"Encontrei um horario disponivel em {date_str}, no periodo da {period} 📅: "
                f"{times[0]}. Esse horario funciona para voce?"
            )

        rendered_times = " ou ".join(times[:2])
        return (
            f"Encontrei horarios disponiveis em {date_str}, no periodo da {period} 📅: "
            f"{rendered_times}. Qual deles fica melhor para voce?"
        )

    def _format_day_slots(self, period: str, date_str: str, slots: list[dict[str, Any]]) -> str:
        rendered = ", ".join(slot["start"].strftime("%H:%M") for slot in slots)
        return (
            f"Para {date_str}, no periodo da {period}, tenho estes horarios livres 📅: "
            f"{rendered}. Qual voce prefere?"
        )

    def _format_event_label(self, event: dict[str, Any]) -> str:
        start_str = event.get("start", {}).get("dateTime", "")
        if not start_str:
            return ""
        start_dt = datetime.fromisoformat(start_str).astimezone(SAO_PAULO_TZ)
        return f"{start_dt.strftime('%d/%m/%Y')} as {start_dt.strftime('%H:%M')}"

    def _get_single_upcoming_event(self, phone: str) -> dict[str, Any] | None:
        events = self.calendar.find_appointments_by_phone(phone)
        return events[0] if events else None

    def _handle_query(self, phone: str) -> str:
        event = self._get_single_upcoming_event(phone)
        if event is None:
            return self.config.get_message("query.not_found").strip()

        start_dt = datetime.fromisoformat(event["start"]["dateTime"]).astimezone(SAO_PAULO_TZ)
        weekday_names = {
            0: "segunda-feira",
            1: "terca-feira",
            2: "quarta-feira",
            3: "quinta-feira",
            4: "sexta-feira",
            5: "sabado",
            6: "domingo",
        }
        return (
            "Sua proxima consulta esta marcada para "
            f"{start_dt.strftime('%d/%m/%Y')} ({weekday_names[start_dt.weekday()]}) "
            f"as {start_dt.strftime('%H:%M')}. Se quiser, tambem posso te ajudar a remarcar ou cancelar."
        )

    def _handle_address_query(self) -> str:
        """Responde perguntas simples sobre o endereco da clinica sem entrar no fluxo de agenda."""
        address = self.config.get_doctor_address().strip()
        if address:
            return (
                f"O endereco da clinica e {address}\n\n"
                "Se precisar de mais alguma coisa, estou por aqui."
            )
        return (
            "Ainda nao encontrei o endereco configurado da clinica. "
            "Se preferir, posso pedir para a doutora te encaminhar essa informacao."
        )

    def _is_plan_list_question(self, text: str) -> bool:
        normalized = self._normalize(text)
        return any(self._contains_keyword(normalized, keyword) for keyword in self._PLAN_LIST_HINTS)

    def _should_answer_plan_question(self, patient_message: str, explicit_plan: str) -> bool:
        normalized = self._normalize(patient_message)
        if not normalized:
            return False

        if self._is_plan_list_question(patient_message):
            return True

        has_question_hint = any(
            self._contains_keyword(normalized, keyword) for keyword in self._PLAN_QUESTION_HINTS
        )
        has_plan_context = bool(explicit_plan) or any(
            self._contains_keyword(normalized, keyword) for keyword in self._PLAN_CONTEXT_HINTS
        )
        return has_question_hint and has_plan_context

    def _format_plan_question_summary(self) -> str:
        accepted_plans: list[str] = []
        referral_plans: list[str] = []
        has_particular = False

        for plan in self.config.get_plans():
            name = str(plan.get("name", "")).strip()
            if not name:
                continue

            if self._normalize(name) == "particular":
                has_particular = True
                continue

            if plan.get("referral", False):
                referral_to = str(plan.get("referral_to", "")).strip() or "profissional parceira"
                referral_plans.append(f"{name} (encaminhado para {referral_to})")
                continue

            accepted_plans.append(name)

        response_parts: list[str] = []
        if accepted_plans:
            response_parts.append(
                "Hoje atendemos pelos convenios " + ", ".join(accepted_plans)
            )
        if has_particular:
            response_parts.append("tambem atendemos no particular")

        response = ". ".join(part.strip() for part in response_parts if part.strip()).strip()
        if response:
            response += "."

        if referral_plans:
            response += (
                " Os convenios "
                + ", ".join(referral_plans)
                + " sao encaminhados para a Dra. Tarcilia."
            )

        if response:
            response += " Se quiser, posso te orientar pelo seu plano."
            return response

        return "Posso te orientar pelo seu plano, se voce me disser qual convenio deseja consultar."

    def _handle_plan_question(self, patient_message: str, explicit_plan: str) -> str:
        if self._is_plan_list_question(patient_message):
            return self._format_plan_question_summary()

        if not explicit_plan:
            return (
                "Pode me dizer qual e o convenio que voce quer consultar? "
                f"Hoje atendemos: {self._format_available_plans()}."
            )

        canonical_plan = self._canonical_plan_name(explicit_plan)
        if self.config.is_referral_plan(canonical_plan):
            referral_to = self.config.get_plan_referral_target(canonical_plan) or "profissional parceira"
            return (
                f"O convenio {canonical_plan} e atendido pela {referral_to}. "
                "Se quiser, eu posso te orientar no encaminhamento para a equipe dela."
            )

        if self._normalize(canonical_plan) == "particular":
            return (
                "Sim, atendemos no particular. "
                "Se quiser, tambem posso te ajudar com o agendamento."
            )

        return (
            f"Sim, atendemos {canonical_plan}. "
            "Se quiser, tambem posso te ajudar a agendar ou tirar outra duvida sobre o atendimento."
        )

    def _handle_cancel_confirmation(
        self,
        phone: str,
        state: ConversationState,
        patient_message: str,
    ) -> str:
        normalized = self._normalize(patient_message)
        if "naum" in normalized or any(
            self._contains_keyword(normalized, token) for token in self._NO_TOKENS
        ):
            ConversationStateService.clear(phone)
            return "Tudo bem. Mantive sua consulta como esta. Se precisar de mais alguma coisa, estou por aqui. 😊"

        if not any(self._contains_keyword(normalized, token) for token in self._YES_TOKENS):
            return "Deseja realmente cancelar? Se puder, me responda com sim ou nao."

        if not state.pending_event_id:
            ConversationStateService.clear(phone)
            return self.config.get_message("cancel.not_found").strip()

        success = self.calendar.cancel_appointment(state.pending_event_id)
        label = state.pending_event_label or "a consulta"
        ConversationStateService.clear(phone)

        if not success:
            return (
                "Tive um problema para cancelar sua consulta agora. "
                "A doutora sera avisada e deve falar com voce em seguida."
            )

        self.patients.save_interaction(phone, "cancel", f"Consulta cancelada: {label}")
        date_str, time_str = label.split(" as ")
        return self.config.get_message(
            "cancel.cancelled",
            date=date_str,
            time=time_str,
        ).strip()

    @staticmethod
    def _split_event_label(label: str) -> tuple[str, str]:
        if " as " not in label:
            return "", ""
        date_str, time_str = label.split(" as ", 1)
        return date_str.strip(), time_str.strip()

    def _handle_pending_appointment_confirmation(
        self,
        phone: str,
        state: ConversationState,
        patient_message: str,
    ) -> str:
        label = state.pending_event_label or state.reschedule_event_label
        date_str, time_str = self._split_event_label(label)
        event_id = (
            state.metadata.get(AppointmentConfirmationService.METADATA_EVENT_ID_KEY)
            or state.pending_event_id
            or state.reschedule_event_id
        )
        appointment_start = state.metadata.get(
            AppointmentConfirmationService.METADATA_START_KEY,
            "",
        )

        if AppointmentConfirmationService.wants_cancellation(patient_message):
            AppointmentConfirmationService.mark_patient_response(
                event_id=event_id,
                appointment_start=appointment_start,
                status="cancel_requested",
                response_text=patient_message,
            )
            state.stage = "awaiting_cancel_confirmation"
            state.intent = "cancel"
            state.pending_event_id = state.pending_event_id or state.reschedule_event_id
            state.pending_event_label = label
            AppointmentConfirmationService.clear_confirmation_metadata(state)
            ConversationStateService.save(phone, state)

            if date_str and time_str:
                return self.config.get_message(
                    "cancel.confirm",
                    date=date_str,
                    time=time_str,
                ).strip()
            return "Deseja realmente cancelar sua consulta?"

        wants_reschedule = (
            AppointmentConfirmationService.needs_reschedule_response(patient_message)
            or bool(self._extract_period(patient_message))
            or bool(self._extract_date(patient_message))
        )
        if wants_reschedule:
            AppointmentConfirmationService.mark_patient_response(
                event_id=event_id,
                appointment_start=appointment_start,
                status="reschedule_requested",
                response_text=patient_message,
            )
            state.stage = "idle"
            state.intent = "reschedule"
            state.reschedule_event_id = state.reschedule_event_id or state.pending_event_id
            state.reschedule_event_label = state.reschedule_event_label or label
            state.pending_event_id = ""
            state.pending_event_label = ""
            state.requested_period = ""
            state.requested_date = ""
            AppointmentConfirmationService.clear_confirmation_metadata(state)

            intro = self.config.get_message(
                "appointment_confirmation.reschedule_intro",
                date=date_str,
                time=time_str,
            ).strip()
            follow_up = self._prepare_schedule_or_reschedule(phone, patient_message, state)
            if intro:
                return f"{intro}\n\n{follow_up}".strip()
            return follow_up

        if AppointmentConfirmationService.is_affirmative_response(patient_message):
            AppointmentConfirmationService.mark_patient_response(
                event_id=event_id,
                appointment_start=appointment_start,
                status="confirmed",
                response_text=patient_message,
            )
            ConversationStateService.clear(phone)

            return self.config.get_message(
                "appointment_confirmation.confirmed",
                date=date_str,
                time=time_str,
                doctor_name=self.config.get_doctor_name(),
            ).strip()

        return self.config.get_message(
            "appointment_confirmation.clarify",
            date=date_str,
            time=time_str,
        ).strip()

    def _prepare_schedule_or_reschedule(
        self,
        phone: str,
        patient_message: str,
        state: ConversationState,
    ) -> str:
        detected_procedure = self._detect_procedure_rule(patient_message)
        if detected_procedure is not None:
            state.requested_procedure = str(detected_procedure.get("key", "")).strip()
            state.requested_reason = state.requested_reason or str(detected_procedure.get("label", "")).strip()

        active_procedure_rule = self.config.get_procedure_rule(state.requested_procedure) if state.requested_procedure else None
        if active_procedure_rule and active_procedure_rule.get("not_performed", False):
            return self._handle_procedure_rule(
                phone,
                state,
                patient_message,
                active_procedure_rule,
            ) or ""

        if state.intent == "reschedule" and not state.reschedule_event_id:
            event = self._get_single_upcoming_event(phone)
            if event is None:
                ConversationStateService.clear(phone)
                return (
                    "Nao encontrei nenhuma consulta futura nesse numero para remarcar. "
                    "Se quiser, posso te ajudar a agendar uma nova."
                )
            state.reschedule_event_id = str(event.get("id", ""))
            state.reschedule_event_label = self._format_event_label(event)

        if not state.plan_name:
            was_waiting_plan = state.stage == "awaiting_plan"
            plan_name = self._extract_plan_name(patient_message)
            if not plan_name:
                state.stage = "awaiting_plan"
                ConversationStateService.save(phone, state)
                if was_waiting_plan:
                    return self.config.get_message(
                        "errors.plan_not_found",
                        available_plans=self._format_available_plans(),
                    ).strip()
                return self._ask_plan(state.patient_name)

            state.plan_name = plan_name
            self.patients.upsert(phone, state.patient_name, plan_name)

        if self.config.is_referral_plan(state.plan_name):
            if not state.requested_reason:
                state.stage = "awaiting_referral_reason"
                state.intent = ""
                state.requested_period = ""
                state.requested_date = ""
                state.pending_event_id = ""
                state.pending_event_label = ""
                state.reschedule_event_id = ""
                state.reschedule_event_label = ""
                ConversationStateService.save(phone, state)
                return self.config.get_message(
                    "referral.ask_reason",
                    referral_to=self.config.get_plan_referral_target(state.plan_name) or "profissional parceira",
                ).strip()
            return self._finalize_plan_referral(phone, state, patient_message)

        if active_procedure_rule:
            procedure_response = self._handle_procedure_rule(
                phone,
                state,
                patient_message,
                active_procedure_rule,
            )
            if procedure_response:
                return procedure_response

        requested_period = self._extract_period(patient_message) or state.requested_period
        requested_date = self._extract_date(patient_message) or state.requested_date

        if not requested_period:
            state.stage = "awaiting_period"
            state.requested_date = requested_date
            ConversationStateService.save(phone, state)
            return self._ask_period(state.patient_name)

        state.requested_period = requested_period
        state.requested_date = requested_date

        if requested_date:
            target_date = datetime.strptime(requested_date, "%d/%m/%Y").replace(tzinfo=SAO_PAULO_TZ)
            if target_date.weekday() >= 5:
                ConversationStateService.save(phone, state)
                return "Esse dia cai no fim de semana, e a clinica nao atende aos finais de semana."

            slots = self.calendar.get_available_slots(target_date, requested_period)
            if not slots:
                ConversationStateService.save(phone, state)
                return (
                    f"Nao encontrei horarios livres em {requested_date}, no periodo da {requested_period}. "
                    "Se quiser, me diga outro dia ou outro periodo."
                )

            ConversationStateService.save(phone, state)
            return self._format_day_slots(requested_period, requested_date, slots)

        slots = self._find_next_available_slots(requested_period)
        if not slots:
            ConversationStateService.save(phone, state)
            return (
                "Nao encontrei horarios disponiveis nos proximos dias nesse periodo. "
                "Se quiser, posso tentar outro periodo para voce."
            )

        first_slot = slots[0]
        offer_date = first_slot["start"].strftime("%d/%m/%Y")
        state.requested_date = offer_date
        ConversationStateService.save(phone, state)
        return self._format_slot_offer(requested_period, offer_date, slots)

    def process_message(
        self,
        patient_phone: str,
        patient_message: str,
        patient_name: str = "",
        history_text: str | None = None,
        is_first_message: bool | None = None,
    ) -> str:
        """Processa a mensagem do paciente usando regras de negocio e estado persistido."""

        del history_text
        if is_first_message is None:
            is_first_message = False

        state = ConversationStateService.get(patient_phone)
        known_patient = self.patients.find_by_phone(patient_phone)
        detected_intent = self._detect_intent(patient_message)
        detected_procedure = self._detect_procedure_rule(patient_message)

        if is_first_message and state.stage != "idle":
            ConversationStateService.clear(patient_phone)
            state = ConversationState()

        if detected_intent == "address":
            ConversationStateService.clear(patient_phone)
            return self._handle_address_query()

        if known_patient:
            state.patient_name = state.patient_name or known_patient["name"]
            state.plan_name = state.plan_name or known_patient["plan"]

        explicit_plan = self._extract_plan_name(patient_message)
        if detected_procedure is not None:
            state.requested_procedure = str(detected_procedure.get("key", "")).strip()
            state.requested_reason = state.requested_reason or str(detected_procedure.get("label", "")).strip()

        if state.stage == AppointmentConfirmationService.CONFIRMATION_STAGE:
            return self._handle_pending_appointment_confirmation(
                patient_phone,
                state,
                patient_message,
            )

        if state.stage == "awaiting_cancel_confirmation":
            return self._handle_cancel_confirmation(patient_phone, state, patient_message)

        if state.stage == "awaiting_referral_reason":
            return self._handle_referral_reason(patient_phone, state, patient_message)

        if self._should_answer_plan_question(patient_message, explicit_plan):
            return self._handle_plan_question(patient_message, explicit_plan)

        if not state.patient_name:
            extracted_name = self._extract_name(patient_message, patient_name)
            if extracted_name:
                state.patient_name = extracted_name
                self.patients.upsert(patient_phone, extracted_name)
            else:
                if detected_intent:
                    state.intent = detected_intent
                state.stage = "awaiting_name"
                ConversationStateService.save(patient_phone, state)
                return self._ask_name(bool(is_first_message))

        if explicit_plan:
            state.plan_name = explicit_plan

        intent = detected_intent or ("schedule" if state.requested_procedure else "") or state.intent
        if not intent:
            state.stage = "awaiting_intent"
            ConversationStateService.save(patient_phone, state)
            return self._ask_intent(state.patient_name, bool(is_first_message))

        state.intent = intent

        if intent == "query":
            ConversationStateService.clear(patient_phone)
            return self._handle_query(patient_phone)

        if intent == "cancel":
            event = self._get_single_upcoming_event(patient_phone)
            if event is None:
                ConversationStateService.clear(patient_phone)
                return self.config.get_message("cancel.not_found").strip()

            state.stage = "awaiting_cancel_confirmation"
            state.pending_event_id = str(event.get("id", ""))
            state.pending_event_label = self._format_event_label(event)
            ConversationStateService.save(patient_phone, state)

            date_str, time_str = state.pending_event_label.split(" as ")
            return self.config.get_message(
                "cancel.confirm",
                date=date_str,
                time=time_str,
            ).strip()

        return self._prepare_schedule_or_reschedule(
            patient_phone,
            patient_message,
            state,
        )
