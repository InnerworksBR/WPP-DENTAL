"""Modelo de dados do Agendamento."""

from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional


class Appointment(BaseModel):
    """Representa um agendamento no Google Calendar."""

    event_id: Optional[str] = Field(None, description="ID do evento no Google Calendar")
    patient_name: str = Field(..., description="Nome do paciente")
    patient_phone: str = Field(..., description="Telefone do paciente")
    start_time: datetime = Field(..., description="Data/hora de início da consulta")
    end_time: datetime = Field(..., description="Data/hora de término da consulta")
    summary: Optional[str] = Field(None, description="Título do evento no Calendar")

    @property
    def calendar_title(self) -> str:
        """Formato do título no Google Calendar: 'Nome - Telefone'."""
        return f"{self.patient_name} - {self.patient_phone}"
