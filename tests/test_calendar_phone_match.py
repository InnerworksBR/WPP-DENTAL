"""Testes de matching de telefone no Calendar (PH-03) e idempotencia canônica (PH-01)."""

import pytest

from src.domain.policies.phone_service import canonical_phone, phones_match

PHONE_A_SEM_9 = "1187654321"
PHONE_A_COM_9 = "11987654321"
PHONE_A_COM_55 = "5511987654321"
PHONE_B = "2187654321"   # DDD 21 — diferente de A


class TestCalendarPhoneMatch:
    """PH-03: isolamento de eventos por telefone canônico."""

    def test_ca004_pacientes_diferentes_nao_casam(self):
        """CA-004: paciente A não retorna evento de paciente B cujo final coincide."""
        # A = 1187654321, B = 2187654321 — final "87654321" coincide mas DDD é diferente
        assert not phones_match(PHONE_A_SEM_9, PHONE_B)

    def test_ca004_endswith_nao_substitui_phones_match(self):
        """Lógica antiga (endswith) casaria erroneamente; phones_match não."""
        phone_digits_a = "1187654321"
        summary_digits_b = "2187654321"
        search_term = "87654321"

        # Bug antigo: summary_digits.endswith(search_term) seria True
        assert summary_digits_b.endswith(search_term), "endswith causaria match incorreto"
        # Fix: phones_match não casa
        assert not phones_match(summary_digits_b, phone_digits_a)

    def test_ca001_variantes_do_mesmo_numero_casam(self):
        """CA-001: 3 variações do mesmo número casam entre si."""
        assert phones_match(PHONE_A_COM_9, PHONE_A_SEM_9)
        assert phones_match(PHONE_A_COM_55, PHONE_A_SEM_9)
        assert phones_match(PHONE_A_COM_55, PHONE_A_COM_9)

    def test_ca002_sem_9_casa_com_9(self):
        """CA-002: remarcação com 9o dígito divergente não cria duplicata."""
        assert phones_match(PHONE_A_SEM_9, PHONE_A_COM_9)

    def test_numeros_completamente_diferentes_nao_casam(self):
        assert not phones_match("1187654321", "1199999999")


class TestCanonicalIdempotency:
    """RNF-001: canonical_phone é idempotente."""

    def test_rnf001_idempotente_com_9(self):
        c = canonical_phone(PHONE_A_COM_9)
        assert canonical_phone(c) == c

    def test_rnf001_idempotente_com_55(self):
        c = canonical_phone(PHONE_A_COM_55)
        assert canonical_phone(c) == c

    def test_rnf001_idempotente_sem_9(self):
        c = canonical_phone(PHONE_A_SEM_9)
        assert canonical_phone(c) == c

    def test_todas_variacoes_mesma_chave_canonica(self):
        chaves = {
            canonical_phone(PHONE_A_SEM_9),
            canonical_phone(PHONE_A_COM_9),
            canonical_phone(PHONE_A_COM_55),
        }
        assert len(chaves) == 1, f"Variações produziram chaves diferentes: {chaves}"
