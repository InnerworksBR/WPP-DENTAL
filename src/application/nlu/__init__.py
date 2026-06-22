"""Pacote de NLU (entendimento de linguagem natural) — impl 015.

Exporta o classificador de intenção e o contrato (`NluResult`, `Intent`, `Entities`,
`NluContext`). A NLU descreve a mensagem; quem decide a agenda é o orquestrador (016).
"""

from .intent_classifier import IntentClassifier
from .schema import Entities, Intent, NluContext, NluResult

__all__ = ["IntentClassifier", "Entities", "Intent", "NluContext", "NluResult"]
