"""Tool CrewAI para operacoes com pacientes (SQLite)."""

from typing import Optional, Type

from pydantic import BaseModel, Field

from ...application.services.patient_service import PatientService
from ...infrastructure.persistence.connection import get_db


class FindPatientInput(BaseModel):
    """Input para buscar paciente."""

    phone: str = Field(..., description="Telefone do paciente")


class FindPatientTool:
    """Busca um paciente pelo telefone no banco de dados."""

    name: str = "buscar_paciente"
    description: str = (
        "Busca um paciente cadastrado pelo numero de telefone. "
        "Retorna nome, telefone e convenio se encontrado. "
        "Use esta ferramenta no inicio de toda conversa para verificar "
        "se o paciente ja e conhecido."
    )
    args_schema: Type[BaseModel] = FindPatientInput

    def _run(self, phone: str) -> str:
        # PH-02: reusar PatientService.find_by_phone (match exato + fallback em memoria)
        patient = PatientService.find_by_phone(phone)
        if patient:
            return (
                "Paciente encontrado!\n"
                f"Nome: {patient['name']}\n"
                f"Telefone: {patient['phone']}\n"
                f"Convenio: {patient['plan'] or 'Nao informado'}"
            )
        return "Paciente nao encontrado no sistema. E um paciente novo."


class SavePatientInput(BaseModel):
    """Input para salvar/atualizar paciente."""

    phone: str = Field(..., description="Telefone do paciente")
    name: str = Field(..., description="Nome completo do paciente")
    plan: Optional[str] = Field(None, description="Convenio/plano do paciente")


class SavePatientTool:
    """Salva ou atualiza um paciente no banco de dados."""

    name: str = "salvar_paciente"
    description: str = (
        "Salva um novo paciente ou atualiza os dados de um paciente existente. "
        "Use esta ferramenta apos coletar o nome e telefone do paciente. "
        "Se o paciente ja existe (mesmo telefone), seus dados serao atualizados."
    )
    args_schema: Type[BaseModel] = SavePatientInput

    def _run(self, phone: str, name: str, plan: Optional[str] = None) -> str:
        # PA-02: reusar PatientService.upsert (merge nao-destrutivo)
        PatientService.upsert(phone, name, plan)
        display = name.strip() if name and name.strip() else phone
        return f"Paciente {display} salvo com sucesso."


class SaveInteractionInput(BaseModel):
    """Input para salvar interacao."""

    phone: str = Field(..., description="Telefone do paciente")
    interaction_type: str = Field(
        ...,
        description="Tipo: 'schedule', 'reschedule', 'cancel', 'query', 'escalation'",
    )
    summary: str = Field(..., description="Resumo da interacao")


class SaveInteractionTool:
    """Registra uma interacao com o paciente."""

    name: str = "registrar_interacao"
    description: str = (
        "Registra uma interacao realizada com o paciente no historico. "
        "Use apos cada operacao concluida (agendamento, cancelamento, etc.)."
    )
    args_schema: Type[BaseModel] = SaveInteractionInput

    def _run(self, phone: str, interaction_type: str, summary: str) -> str:
        # PH-02: reusar PatientService.find_by_phone (match exato + fallback em memoria)
        patient = PatientService.find_by_phone(phone)
        if not patient:
            return "Paciente nao encontrado. Cadastre-o antes de registrar interacoes."

        db = get_db()
        db.execute(
            "INSERT INTO interactions (patient_id, type, summary) VALUES (?, ?, ?)",
            (patient["id"], interaction_type, summary),
        )
        db.commit()
        return f"Interacao '{interaction_type}' registrada com sucesso."
