"""Modelo de dados do Paciente."""

from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional


class Patient(BaseModel):
    """Representa um paciente no sistema."""

    id: Optional[int] = None
    phone: str = Field(..., description="Telefone do paciente (identificador principal)")
    name: str = Field(..., description="Nome completo do paciente")
    plan: Optional[str] = Field(None, description="Convênio/plano do paciente")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
