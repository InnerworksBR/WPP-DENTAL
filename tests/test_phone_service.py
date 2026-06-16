"""Testes para as funcoes de telefone: canonical_phone, is_valid_phone, phones_match (PH-01/04/05)."""

import pytest

from src.domain.policies.phone_service import (
    canonical_phone,
    is_valid_phone,
    normalize_conversation_phone,
    phones_match,
)

# Números BR de referência usados nos testes: DDD 11, número fixo 87654321
PHONE_SEM_9 = "1187654321"      # 10 dígitos locais (fixo ou sem 9o)
PHONE_COM_9 = "11987654321"     # 11 dígitos locais (celular com 9o)
PHONE_COM_55 = "5511987654321"  # 13 dígitos com DDI 55 e 9o
PHONE_55_SEM_9 = "551187654321" # 12 dígitos com DDI 55 sem 9o


class TestCanonicalPhone:
    """RF-001: forma canônica reconcilia presença/ausência do 9o dígito."""

    def test_ca001_com_9_igual_sem_9(self):
        """CA-001: as 3 variações do mesmo número produzem a mesma chave."""
        assert canonical_phone(PHONE_COM_9) == canonical_phone(PHONE_SEM_9)

    def test_ca001_com_55_igual_sem_55(self):
        assert canonical_phone(PHONE_COM_55) == canonical_phone(PHONE_COM_9)

    def test_ca001_com_55_sem_9_igual_sem_9(self):
        assert canonical_phone(PHONE_55_SEM_9) == canonical_phone(PHONE_SEM_9)

    def test_rnf001_idempotente(self):
        """RNF-001: canonical_phone é idempotente."""
        c = canonical_phone(PHONE_COM_9)
        assert canonical_phone(c) == c

    def test_rnf001_idempotente_sem_9(self):
        c = canonical_phone(PHONE_SEM_9)
        assert canonical_phone(c) == c

    def test_resultado_tem_10_digitos(self):
        assert len(canonical_phone(PHONE_COM_55)) == 10

    def test_invalido_retorna_vazio_jid_grupo(self):
        """CA-006: JID de grupo retorna ""."""
        assert canonical_phone("123456789@g.us") == ""

    def test_invalido_retorna_vazio_lid(self):
        """CA-006: @lid retorna ""."""
        assert canonical_phone("999@lid") == ""

    def test_invalido_retorna_vazio_curto(self):
        """CA-006: número curto retorna ""."""
        assert canonical_phone("12345") == ""

    def test_numero_estrangeiro_retorna_vazio(self):
        # número hipotético estrangeiro com 8 dígitos (não-BR)
        assert canonical_phone("12345678") == ""


class TestIsValidPhone:
    """RF-005: validação de telefone BR."""

    def test_br_10_digitos_valido(self):
        assert is_valid_phone(PHONE_SEM_9)

    def test_br_11_digitos_valido(self):
        assert is_valid_phone(PHONE_COM_9)

    def test_br_com_55_12_valido(self):
        assert is_valid_phone(PHONE_55_SEM_9)

    def test_br_com_55_13_valido(self):
        assert is_valid_phone(PHONE_COM_55)

    def test_ca006_jid_grupo_invalido(self):
        assert not is_valid_phone("123456789@g.us")

    def test_ca006_jid_lid_invalido(self):
        assert not is_valid_phone("999@lid")

    def test_ca006_numero_curto_invalido(self):
        assert not is_valid_phone("12345")

    def test_string_vazia_invalida(self):
        assert not is_valid_phone("")


class TestPhonesMatch:
    """RF-001: phones_match por forma canônica."""

    def test_ca001_formatos_diferentes_casam(self):
        assert phones_match(PHONE_COM_55, PHONE_SEM_9)

    def test_ca001_com_e_sem_9_casam(self):
        assert phones_match(PHONE_COM_9, PHONE_SEM_9)

    def test_simetrico(self):
        assert phones_match(PHONE_COM_9, PHONE_SEM_9) == phones_match(PHONE_SEM_9, PHONE_COM_9)

    def test_ca004_numeros_distintos_nao_casam(self):
        """CA-004: DDD diferente não casa."""
        assert not phones_match("1187654321", "2187654321")

    def test_numeros_completamente_diferentes(self):
        assert not phones_match("1187654321", "1199999999")

    def test_string_vazia_nao_casa(self):
        assert not phones_match("", PHONE_SEM_9)
        assert not phones_match(PHONE_SEM_9, "")


class TestNormalizeConversationPhone:
    """RF-004: normalize_conversation_phone não adiciona 55 cegamente."""

    def test_br_valido_recebe_55(self):
        result = normalize_conversation_phone(PHONE_COM_9)
        assert result.startswith("55")

    def test_ca005_jid_grupo_nao_recebe_55(self):
        """CA-005: JID de grupo não vira telefone BR."""
        result = normalize_conversation_phone("123456789@g.us")
        assert not result.startswith("55")

    def test_string_vazia_retorna_vazia(self):
        assert normalize_conversation_phone("") == ""

    def test_numero_com_55_mantido(self):
        result = normalize_conversation_phone(PHONE_COM_55)
        assert result == PHONE_COM_55
