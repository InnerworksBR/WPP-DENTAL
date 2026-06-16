"""Persistência de alertas que falharam no envio para reenvio manual."""

from __future__ import annotations

import logging

from .connection import get_db

logger = logging.getLogger(__name__)


class FailedAlertStore:
    """Registra alertas que não puderam ser enviados à doutora."""

    @classmethod
    def record(
        cls,
        *,
        doctor_phone: str,
        patient_phone: str,
        patient_name: str,
        message: str,
        reason: str,
    ) -> None:
        """Persiste um alerta falho para reenvio manual posterior."""
        try:
            db = get_db()
            db.execute(
                "INSERT INTO pending_alerts (doctor_phone, patient_phone, patient_name, message, reason) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    (doctor_phone or "").strip(),
                    (patient_phone or "").strip(),
                    (patient_name or "").strip(),
                    (message or "").strip(),
                    (reason or "").strip(),
                ),
            )
            db.commit()
            logger.info(
                "Alerta falho persistido para reenvio manual (patient=%s, reason=%s)",
                patient_phone,
                reason,
            )
        except Exception as exc:
            logger.error("Falha ao persistir alerta falho: %s", exc, exc_info=True)
