"""Utilitarios para normalizacao de telefones."""


def extract_digits(value: str) -> str:
    """Retorna apenas os digitos presentes no texto informado."""
    return "".join(char for char in value if char.isdigit())


def normalize_internal_phone(phone: str) -> str:
    """Normaliza para o formato interno da aplicacao: DDD + numero, sem codigo do pais."""
    digits = extract_digits(phone)

    if digits.startswith("55") and len(digits) in (12, 13):
        digits = digits[2:]

    if len(digits) > 11:
        digits = digits[-11:]

    return digits


def build_phone_search_term(phone: str) -> str:
    """Retorna a chave de busca mais confiavel para localizar o paciente/consulta."""
    normalized = normalize_internal_phone(phone)
    return normalized[-11:] if len(normalized) >= 11 else normalized
