"""Utilitarios para normalizacao de telefones."""


def extract_digits(value: str) -> str:
    """Retorna apenas os digitos presentes no texto informado."""
    return "".join(char for char in value if char.isdigit())


def is_valid_phone(value: str) -> bool:
    """True apenas para telefones BR plausiveis (10/11 digitos locais, ou 12/13 com 55).
    False para JID de grupo (@g.us), @lid e numeros curtos.
    """
    raw = (value or "").strip()
    if "@g.us" in raw or "@lid" in raw:
        return False
    local = raw.split("@", 1)[0].split(":", 1)[0]
    digits = extract_digits(local)
    if digits.startswith("55") and len(digits) in (12, 13):
        digits = digits[2:]
    return len(digits) in (10, 11)


def canonical_phone(value: str) -> str:
    """Forma canonica BR: DDD(2) + 8 digitos sem o 9o digito de celular como chave.

    Reconcilia a presenca/ausencia do 9o digito para que o mesmo paciente produza
    sempre a mesma chave (RF-001, RNF-001).

    Retorna "" se o valor nao for um telefone BR valido.
    """
    raw = (value or "").strip()
    local = raw.split("@", 1)[0].split(":", 1)[0]
    digits = extract_digits(local)
    if digits.startswith("55") and len(digits) in (12, 13):
        digits = digits[2:]
    if len(digits) == 11 and digits[2] == "9":
        # celular com 9o digito: DDD(2) + 9 + 8 -> DDD(2) + 8
        digits = digits[:2] + digits[3:]
    if len(digits) != 10:
        return ""
    return digits


def phones_match(a: str, b: str) -> bool:
    """True se canonical_phone(a) == canonical_phone(b), com ambos nao-vazios."""
    ca, cb = canonical_phone(a), canonical_phone(b)
    return bool(ca) and ca == cb


def normalize_conversation_phone(value: str) -> str:
    """Normaliza telefones/JIDs do WhatsApp para um identificador consistente de conversa.

    PH-04/PH-05: prefixo '55' so aplicado em numeros BR validados; JIDs de grupo/lid
    e numeros invalidos nao recebem o prefixo.
    """
    raw_value = (value or "").strip()
    if not raw_value:
        return ""

    local_part = raw_value.split("@", 1)[0].split(":", 1)[0].strip()
    digits = extract_digits(local_part)
    if not digits:
        return local_part

    if not digits.startswith("55") and is_valid_phone(digits):
        digits = f"55{digits}"

    return digits


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
