"""Politicas e regras de dominio."""

from .appointment_offer_service import AppointmentOfferService
from .phone_service import build_phone_search_term, extract_digits, normalize_internal_phone
from .scope_guard_service import ScopeGuardService

__all__ = [
    "AppointmentOfferService",
    "ScopeGuardService",
    "build_phone_search_term",
    "extract_digits",
    "normalize_internal_phone",
]
