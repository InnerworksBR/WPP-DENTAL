"""Tool CrewAI para envio de mensagens WhatsApp."""

from typing import Type

from pydantic import BaseModel, Field

from ...infrastructure.integrations.whatsapp_service import WhatsAppService


class SendWhatsAppMessageInput(BaseModel):
    """Input para enviar mensagem."""
    phone: str = Field(..., description="Número de telefone do destinatário")
    message: str = Field(..., description="Texto da mensagem a enviar")


class SendWhatsAppMessageTool:
    """Envia uma mensagem via WhatsApp usando a Evolution API."""

    name: str = "enviar_mensagem_whatsapp"
    description: str = (
        "Envia uma mensagem de texto para um número de WhatsApp. "
        "Use esta ferramenta para responder ao paciente ou enviar alertas. "
        "O número deve conter apenas dígitos, com DDD."
    )
    args_schema: Type[BaseModel] = SendWhatsAppMessageInput

    def _run(self, phone: str, message: str) -> str:
        service = WhatsAppService()
        success = service.send_message_sync(phone, message)
        if success:
            return "Mensagem enviada com sucesso."
        return "Erro ao enviar mensagem. Tente novamente."


class SendAlertToDoctorInput(BaseModel):
    """Input para enviar alerta à doutora."""
    patient_name: str = Field(..., description="Nome do paciente")
    patient_phone: str = Field(..., description="Telefone do paciente")
    summary: str = Field(..., description="Resumo da solicitação")
    reason: str = Field(
        ...,
        description="Motivo do alerta: 'fora_do_escopo', 'encaminhamento', 'duvida_clinica', 'outro'"
    )
    last_message: str = Field("", description="Última mensagem do paciente")


class SendAlertToDoctorTool:
    """Envia um alerta para a doutora via WhatsApp."""

    name: str = "alertar_doutora"
    description: str = (
        "Envia um alerta para a doutora via WhatsApp quando uma situação foge "
        "do escopo da IA. Use quando: "
        "1) O paciente perguntar preços ou informações de procedimentos. "
        "2) O convênio debe ser encaminhado para outra profissional. "
        "3) Qualquer dúvida clínica. "
        "4) Qualquer situação não prevista."
    )
    args_schema: Type[BaseModel] = SendAlertToDoctorInput

    def _run(
        self,
        patient_name: str,
        patient_phone: str,
        summary: str,
        reason: str,
        last_message: str = "",
    ) -> str:
        from ...infrastructure.integrations.alert_service import AlertService

        alert_service = AlertService()
        success = alert_service.send_alert(
            patient_name=patient_name,
            patient_phone=patient_phone,
            summary=summary,
            reason=reason,
            last_message=last_message,
        )

        if success:
            return "Alerta enviado à doutora com sucesso."
        return "Erro ao enviar alerta. A doutora será notificada por outro meio."
