"""Serviço de alertas para a doutora."""

import logging

from ..config.config_service import ConfigService
from .whatsapp_service import WhatsAppService

logger = logging.getLogger(__name__)


class AlertService:
    """Gerencia envio de alertas para a doutora via WhatsApp."""

    def __init__(self) -> None:
        self.config = ConfigService()
        self.whatsapp = WhatsAppService()

    def send_alert(
        self,
        patient_name: str,
        patient_phone: str,
        summary: str,
        reason: str,
        last_message: str = "",
    ) -> bool:
        """
        Envia alerta para a doutora via WhatsApp.

        Args:
            patient_name: Nome do paciente
            patient_phone: Telefone do paciente
            summary: Resumo da solicitação
            reason: Motivo do alerta (fora do escopo, encaminhamento, etc.)
            last_message: Última mensagem do paciente

        Returns:
            True se enviado com sucesso
        """
        doctor_phone = self.config.get_doctor_phone()

        if not doctor_phone:
            logger.error("Telefone da doutora não configurado!")
            return False

        message = self.config.get_message(
            "alerts.to_doctor",
            patient_name=patient_name or "Não informado",
            patient_phone=patient_phone,
            summary=summary,
            reason=reason,
            last_message=last_message or "(sem mensagem)",
        )

        return self.whatsapp.send_message_sync(doctor_phone, message)

    def send_referral_alert(
        self,
        *,
        patient_name: str,
        patient_phone: str,
        consultation_reason: str,
        referral_to: str,
    ) -> bool:
        """Envia um encaminhamento objetivo com apenas os dados necessarios."""
        doctor_phone = self.config.get_doctor_phone()

        if not doctor_phone:
            logger.error("Telefone da doutora nÃ£o configurado!")
            return False

        message = self.config.get_message(
            "alerts.referral_to_specialist",
            patient_name=patient_name or "NÃ£o informado",
            patient_phone=patient_phone,
            consultation_reason=consultation_reason or "NÃ£o informado",
            referral_to=referral_to or "profissional parceira",
        )

        return self.whatsapp.send_message_sync(doctor_phone, message)

    def notify_patient_escalation(self, patient_phone: str) -> bool:
        """
        Informa ao paciente que a doutora entrará em contato.

        Returns:
            True se enviado com sucesso
        """
        doctor_name = self.config.get_doctor_name()
        message = self.config.get_message(
            "escalation.to_patient",
            doctor_name=doctor_name,
        )
        return self.whatsapp.send_message_sync(patient_phone, message)

    def notify_patient_referral(self, patient_phone: str) -> bool:
        """
        Informa ao paciente que será encaminhado para outra profissional.

        Returns:
            True se enviado com sucesso
        """
        doctor_name = self.config.get_doctor_name()
        message = self.config.get_message(
            "escalation.referral",
            doctor_name=doctor_name,
        )
        return self.whatsapp.send_message_sync(patient_phone, message)
