"""Ponto de entrada HTTP do WPP-DENTAL."""

from .interfaces.http.app import app, dental_crew

__all__ = ["app", "dental_crew"]
