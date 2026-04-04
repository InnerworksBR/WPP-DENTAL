"""Tool CrewAI para operacoes com pacientes (SQLite)."""

from typing import Optional, Type

from pydantic import BaseModel, Field

from ...domain.policies.phone_service import build_phone_search_term, normalize_internal_phone
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
        search_term = build_phone_search_term(phone)
        db = get_db()
        cursor = db.execute(
            "SELECT name, phone, plan FROM patients WHERE phone LIKE ?",
            (f"%{search_term}%",),
        )
        row = cursor.fetchone()

        if row:
            return (
                "Paciente encontrado!\n"
                f"Nome: {row['name']}\n"
                f"Telefone: {row['phone']}\n"
                f"Convenio: {row['plan'] or 'Nao informado'}"
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
        normalized_phone = normalize_internal_phone(phone)
        search_term = build_phone_search_term(phone)
        db = get_db()

        cursor = db.execute(
            "SELECT id FROM patients WHERE phone LIKE ?",
            (f"%{search_term}%",),
        )
        existing = cursor.fetchone()

        if existing:
            db.execute(
                "UPDATE patients SET phone = ?, name = ?, plan = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (normalized_phone, name, plan, existing["id"]),
            )
            db.commit()
            return f"Paciente {name} atualizado com sucesso."

        db.execute(
            "INSERT INTO patients (phone, name, plan) VALUES (?, ?, ?)",
            (normalized_phone, name, plan),
        )
        db.commit()
        return f"Paciente {name} cadastrado com sucesso."


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
        search_term = build_phone_search_term(phone)
        db = get_db()

        cursor = db.execute(
            "SELECT id FROM patients WHERE phone LIKE ?",
            (f"%{search_term}%",),
        )
        row = cursor.fetchone()

        if not row:
            return "Paciente nao encontrado. Cadastre-o antes de registrar interacoes."

        db.execute(
            "INSERT INTO interactions (patient_id, type, summary) VALUES (?, ?, ?)",
            (row["id"], interaction_type, summary),
        )
        db.commit()
        return f"Interacao '{interaction_type}' registrada com sucesso."
