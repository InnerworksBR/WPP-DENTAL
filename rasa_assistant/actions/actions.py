"""Custom actions do assistente Rasa CALM."""

from __future__ import annotations

from typing import Any

from rasa_sdk import Action, Tracker
from rasa_sdk.events import EventType, SlotSet
from rasa_sdk.executor import CollectingDispatcher

from src.application.services.conversation_service import ConversationService
from src.application.services.conversation_workflow_service import ConversationWorkflowService
from src.application.services.rasa_context_service import RasaContextService
from src.application.services.patient_service import PatientService


class _ClinicActionBase(Action):
    context_service = RasaContextService()
    legacy_workflow = ConversationWorkflowService()
    patients = PatientService()

    @staticmethod
    def _latest_text(tracker: Tracker) -> str:
        return str(tracker.latest_message.get("text", "") or "").strip()

    @classmethod
    def _resolve_patient_name(cls, tracker: Tracker) -> str:
        slot_name = str(tracker.get_slot("patient_name") or "").strip()
        if slot_name:
            return slot_name
        return cls.patients.resolve_name(tracker.sender_id, "")


class ActionLoadClinicContext(_ClinicActionBase):
    def name(self) -> str:
        return "action_load_clinic_context"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: dict[str, Any],
    ) -> list[EventType]:
        context = self.context_service.get_clinic_context(
            tracker.sender_id,
            self._resolve_patient_name(tracker),
        )
        return [SlotSet(slot_name, slot_value) for slot_name, slot_value in context.items()]


class ActionCheckPlanInfo(_ClinicActionBase):
    def name(self) -> str:
        return "action_check_plan_info"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: dict[str, Any],
    ) -> list[EventType]:
        result = self.context_service.check_plan_info(self._latest_text(tracker))
        return [SlotSet(slot_name, slot_value) for slot_name, slot_value in result.items()]


class ActionCheckProcedurePolicy(_ClinicActionBase):
    def name(self) -> str:
        return "action_check_procedure_policy"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: dict[str, Any],
    ) -> list[EventType]:
        result = self.context_service.check_procedure_policy(self._latest_text(tracker))
        return [SlotSet(slot_name, slot_value) for slot_name, slot_value in result.items()]


class ActionSendTarciliaReferral(_ClinicActionBase):
    def name(self) -> str:
        return "action_send_tarcilia_referral"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: dict[str, Any],
    ) -> list[EventType]:
        patient_name = self._resolve_patient_name(tracker)
        consultation_reason = str(tracker.get_slot("consultation_reason") or "").strip()
        referral_target = str(tracker.get_slot("referral_target") or "").strip() or "Dra. Tarcilia"

        self.context_service.send_referral(
            patient_phone=tracker.sender_id,
            patient_name=patient_name,
            consultation_reason=consultation_reason,
            referral_to=referral_target,
        )
        return [
            SlotSet("patient_name", patient_name),
            SlotSet("patient_phone", tracker.sender_id),
            SlotSet("referral_target", referral_target),
        ]


class ActionHandleLegacyTurn(_ClinicActionBase):
    def name(self) -> str:
        return "action_handle_legacy_turn"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: dict[str, Any],
    ) -> list[EventType]:
        patient_name = self._resolve_patient_name(tracker)
        response_text = self.legacy_workflow.process_message(
            patient_phone=tracker.sender_id,
            patient_message=self._latest_text(tracker),
            patient_name=patient_name,
            history_text=ConversationService.format_history_for_prompt(tracker.sender_id),
            is_first_message=not ConversationService.has_recent_history(tracker.sender_id),
        )
        dispatcher.utter_message(text=response_text)
        return [
            SlotSet("patient_name", patient_name),
            SlotSet("patient_phone", tracker.sender_id),
        ]
