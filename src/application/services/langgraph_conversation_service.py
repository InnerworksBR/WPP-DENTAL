"""Orquestracao conversacional com LangGraph."""

from __future__ import annotations

import logging
import os
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from .appointment_confirmation_service import AppointmentConfirmationService
from .conversation_service import ConversationService
from .conversation_state_service import ConversationState, ConversationStateService
from .conversation_workflow_service import ConversationWorkflowService
from .patient_service import PatientService

logger = logging.getLogger(__name__)


class RouteDecision(BaseModel):
    """Saida estruturada do roteador do grafo."""

    route: Literal["address", "plan_question", "procedure_question", "social", "legacy"] = Field(
        description="Rota que deve atender a mensagem."
    )
    extracted_plan: str = Field(
        default="",
        description="Nome do convenio citado na mensagem quando aplicavel.",
    )


class GraphConversationState(dict):
    """Alias simples para clareza dos dados trafegados no grafo."""


class LangGraphConversationService:
    """Usa LangGraph para decidir respostas contextuais antes do fluxo legado."""

    def __init__(self) -> None:
        self.workflow = ConversationWorkflowService()
        self.patients = PatientService()
        self.route_model = self._build_route_model()
        self.rephrase_model = self._build_rephrase_model()
        self.graph = self._build_graph()

    @staticmethod
    def enabled() -> bool:
        return os.getenv("CONVERSATION_ENGINE", "").strip().lower() == "langgraph"

    @staticmethod
    def should_fallback_to_legacy() -> bool:
        raw_value = os.getenv("LANGGRAPH_FALLBACK_TO_LEGACY", "1").strip().lower()
        return raw_value not in {"0", "false", "no", "off"}

    @staticmethod
    def _model_name() -> str:
        return os.getenv("LANGGRAPH_OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"

    def _build_route_model(self):
        return ChatOpenAI(
            model=self._model_name(),
            temperature=0,
        )

    def _build_rephrase_model(self):
        return ChatOpenAI(
            model=self._model_name(),
            temperature=0.2,
        )

    def _build_graph(self):
        graph = StateGraph(dict)
        graph.add_node("prepare_context", self._prepare_context)
        graph.add_node("route_turn", self._route_turn)
        graph.add_node("address_response", self._address_response)
        graph.add_node("plan_response", self._plan_response)
        graph.add_node("procedure_response", self._procedure_response)
        graph.add_node("social_response", self._social_response)
        graph.add_node("legacy_response", self._legacy_response)
        graph.add_node("rephrase_response", self._rephrase_response_node)

        graph.add_edge(START, "prepare_context")
        graph.add_edge("prepare_context", "route_turn")
        graph.add_conditional_edges(
            "route_turn",
            self._route_edge,
            {
                "address": "address_response",
                "plan_question": "plan_response",
                "procedure_question": "procedure_response",
                "social": "social_response",
                "legacy": "legacy_response",
            },
        )
        graph.add_edge("address_response", "rephrase_response")
        graph.add_edge("plan_response", "rephrase_response")
        graph.add_edge("procedure_response", "rephrase_response")
        graph.add_edge("social_response", "rephrase_response")
        graph.add_edge("rephrase_response", END)
        graph.add_edge("legacy_response", END)
        return graph.compile()

    def _route_edge(self, state: GraphConversationState) -> str:
        return str(state.get("route") or "legacy")

    def _prepare_context(self, state: GraphConversationState) -> GraphConversationState:
        patient_phone = str(state["patient_phone"])
        known_patient = self.patients.find_by_phone(patient_phone)
        current_state = ConversationStateService.get(patient_phone)
        explicit_plan = self.workflow._extract_plan_name(
            str(state["patient_message"]),
            current_state.plan_name,
        )
        detected_procedure = self.workflow._detect_procedure_rule(str(state["patient_message"]))
        clinic_context = {
            "doctor_name": self.workflow.config.get_doctor_name(),
            "clinic_address": self.workflow.config.get_doctor_address(),
            "available_plans": self.workflow._format_available_plans(),
        }
        return {
            **state,
            "known_patient": known_patient,
            "current_state": current_state,
            "explicit_plan": explicit_plan,
            "detected_procedure": detected_procedure,
            "clinic_context": clinic_context,
        }

    def _must_use_legacy(self, state: GraphConversationState) -> bool:
        current_state: ConversationState = state["current_state"]
        if current_state.stage in {
            AppointmentConfirmationService.CONFIRMATION_STAGE,
            "awaiting_cancel_confirmation",
            "awaiting_referral_reason",
        }:
            return True
        return False

    def _route_with_llm(self, state: GraphConversationState) -> RouteDecision:
        current_state: ConversationState = state["current_state"]
        detected_intent = self.workflow._detect_intent(str(state["patient_message"]))
        detected_procedure = state.get("detected_procedure")
        system_prompt = (
            "Voce roteia mensagens de uma secretaria virtual odontologica.\n"
            "Escolha apenas uma rota.\n"
            "- address: pergunta informativa sobre endereco/localizacao.\n"
            "- plan_question: pergunta informativa sobre convenio/plano, inclusive se aceita ou quais atendem.\n"
            "- procedure_question: pergunta informativa sobre procedimento e cobertura operativa, sem querer marcar agora.\n"
            "- social: agradecimento, ok, valeu, entendido, mensagem social curta.\n"
            "- legacy: qualquer agendamento, remarcacao, cancelamento, consulta de agenda, confirmacao, encaminhamento em andamento, ou duvida ambigua.\n"
            "Se houver qualquer risco operacional ou conversa de agenda, escolha legacy."
        )
        human_prompt = (
            f"Mensagem atual: {state['patient_message']}\n"
            f"Historico recente:\n{state.get('history_text') or '(sem historico)'}\n"
            f"Stage atual: {current_state.stage}\n"
            f"Intent atual: {current_state.intent}\n"
            f"Intent detectado por heuristica: {detected_intent}\n"
            f"Plano extraido por heuristica: {state.get('explicit_plan') or '(nenhum)'}\n"
            f"Procedimento detectado por heuristica: "
            f"{str(detected_procedure.get('label', '')) if isinstance(detected_procedure, dict) else '(nenhum)'}"
        )

        response = self.route_model.with_structured_output(RouteDecision).invoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=human_prompt),
            ]
        )
        if isinstance(response, RouteDecision):
            return response
        if isinstance(response, dict):
            return RouteDecision(**response)
        raise RuntimeError("Roteador LangGraph retornou payload invalido.")

    def _route_turn(self, state: GraphConversationState) -> GraphConversationState:
        if self._must_use_legacy(state):
            return {**state, "route": "legacy"}

        decision = self._route_with_llm(state)
        explicit_plan = str(state.get("explicit_plan") or "").strip()
        extracted_plan = explicit_plan or decision.extracted_plan.strip()
        route = decision.route

        # Protecao adicional: se a heuristica detectou uma operacao de agenda e o LLM veio ambivalente,
        # priorizamos o motor legado.
        detected_intent = self.workflow._detect_intent(str(state["patient_message"]))
        if detected_intent in {"schedule", "cancel", "query", "reschedule"} and route != "address":
            route = "legacy"

        return {
            **state,
            "route": route,
            "explicit_plan": extracted_plan,
        }

    def _address_response(self, state: GraphConversationState) -> GraphConversationState:
        return {
            **state,
            "base_response": self.workflow._handle_address_query(),
        }

    def _plan_response(self, state: GraphConversationState) -> GraphConversationState:
        patient_message = str(state["patient_message"])
        explicit_plan = str(state.get("explicit_plan") or "").strip()
        return {
            **state,
            "base_response": self.workflow._handle_plan_question(patient_message, explicit_plan),
        }

    def _procedure_response(self, state: GraphConversationState) -> GraphConversationState:
        patient_message = str(state["patient_message"])
        explicit_plan = str(state.get("explicit_plan") or "").strip()
        detected_procedure = state.get("detected_procedure")

        if not isinstance(detected_procedure, dict):
            return self._legacy_response(state)

        return {
            **state,
            "base_response": self.workflow._handle_procedure_question(
                patient_message,
                explicit_plan,
                detected_procedure,
            ),
        }

    def _social_response(self, state: GraphConversationState) -> GraphConversationState:
        patient_message = str(state["patient_message"])
        history_text = str(state.get("history_text") or "")
        response = self.workflow._handle_social_message(patient_message, history_text)
        if not response:
            response = "Perfeito. Se precisar de mais alguma coisa, estou por aqui."
        return {
            **state,
            "base_response": response,
        }

    def _call_legacy_workflow(self, state: GraphConversationState) -> str:
        patient_phone = str(state["patient_phone"])
        patient_name = str(state.get("patient_name") or "")
        return self.workflow.process_message(
            patient_phone=patient_phone,
            patient_message=str(state["patient_message"]),
            patient_name=patient_name,
            history_text=str(state.get("history_text") or ""),
            is_first_message=bool(state.get("is_first_message")),
        )

    def _legacy_response(self, state: GraphConversationState) -> GraphConversationState:
        return {
            **state,
            "response_text": self._call_legacy_workflow(state),
        }

    def _rephrase_response(self, state: GraphConversationState) -> str:
        base_response = str(state.get("base_response") or "").strip()
        if not base_response:
            return self._call_legacy_workflow(state)

        clinic_context = state.get("clinic_context", {})
        system_prompt = (
            "Voce reescreve respostas de uma secretaria virtual odontologica em portugues do Brasil.\n"
            "Seja acolhedora, objetiva e natural, como uma secretaria humana.\n"
            "Regras:\n"
            "- preserve exatamente fatos, nomes, convenios, datas, horarios, restricoes e endereco.\n"
            "- nao invente promessas, diagnosticos, valores ou politicas.\n"
            "- nao transforme uma resposta informativa em agendamento.\n"
            "- mantenha a resposta curta e clara.\n"
            "- devolva apenas a resposta final."
        )
        human_prompt = (
            f"Mensagem do paciente: {state['patient_message']}\n"
            f"Historico recente:\n{state.get('history_text') or '(sem historico)'}\n"
            f"Contexto da clinica: doutora={clinic_context.get('doctor_name', '')}; "
            f"endereco={clinic_context.get('clinic_address', '')}\n"
            f"Rascunho deterministico:\n{base_response}"
        )
        response = self.rephrase_model.invoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=human_prompt),
            ]
        )
        content = str(getattr(response, "content", "") or "").strip()
        return content or base_response

    def _rephrase_response_node(self, state: GraphConversationState) -> GraphConversationState:
        return {
            **state,
            "response_text": self._rephrase_response(state),
        }

    def process_message(
        self,
        *,
        patient_phone: str,
        patient_message: str,
        patient_name: str = "",
        history_text: str | None = None,
        is_first_message: bool | None = None,
    ) -> str:
        """Executa o grafo e retorna a resposta final."""
        payload: GraphConversationState = {
            "patient_phone": patient_phone,
            "patient_message": patient_message,
            "patient_name": patient_name,
            "history_text": history_text or ConversationService.format_history_for_prompt(patient_phone),
            "is_first_message": bool(is_first_message),
        }
        result = self.graph.invoke(payload)
        response_text = str(result.get("response_text") or "").strip()
        if not response_text:
            raise RuntimeError("LangGraph nao produziu uma resposta final.")
        return response_text
