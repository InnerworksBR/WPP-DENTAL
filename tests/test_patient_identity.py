"""Testes de identidade do paciente: busca exata e upsert nao-destrutivo (PH-02, PA-01, PA-02)."""

import os
from pathlib import Path

import pytest


class _DBMixin:
    def setup_method(self):
        self.db_path = Path("./data/test_patient_identity.db")
        os.environ["DATABASE_PATH"] = str(self.db_path)
        os.environ["OPENAI_API_KEY"] = "test-key"
        from src.infrastructure.persistence.connection import close_db, init_db
        close_db()
        init_db()

    def teardown_method(self):
        from src.infrastructure.persistence.connection import close_db
        close_db()
        self.db_path.unlink(missing_ok=True)


class TestFindByPhone(_DBMixin):
    """PH-02: busca por match exato canônico, sem colisão por substring."""

    def test_ca003_substring_nao_retorna_paciente_errado(self):
        """CA-003: buscar por número diferente que compartilha substring NÃO retorna paciente errado."""
        from src.application.services.patient_service import PatientService

        PatientService.upsert("1187654321", "Maria")
        # "2187654321" tem mesma substring "87654321" mas é DDD diferente
        result = PatientService.find_by_phone("2187654321")
        assert result is None or result["phone"] != "1187654321"

    def test_busca_por_numero_exato_acha(self):
        from src.application.services.patient_service import PatientService

        PatientService.upsert("1187654321", "Maria")
        result = PatientService.find_by_phone("1187654321")
        assert result is not None
        assert result["name"] == "Maria"

    def test_ca001_variantes_9_digito_acham_mesmo_paciente(self):
        """CA-001: 3 variações do número acham o mesmo paciente."""
        from src.application.services.patient_service import PatientService

        PatientService.upsert("1187654321", "Maria")
        assert PatientService.find_by_phone("11987654321") is not None   # com 9o
        assert PatientService.find_by_phone("1187654321") is not None    # sem 9o
        assert PatientService.find_by_phone("551187654321") is not None  # com 55

    def test_paciente_inexistente_retorna_none(self):
        from src.application.services.patient_service import PatientService

        result = PatientService.find_by_phone("1100000000")
        assert result is None


class TestUpsertNaoDestrutivo(_DBMixin):
    """PA-01: upsert não sobrescreve nome válido nem zera plano."""

    def test_ca007_nome_vazio_nao_sobrescreve_nome_valido(self):
        """CA-007: upsert(phone, name="") sobre paciente com "Maria" mantém "Maria"."""
        from src.application.services.patient_service import PatientService

        PatientService.upsert("1187654321", "Maria")
        PatientService.upsert("1187654321", "")
        patient = PatientService.find_by_phone("1187654321")
        assert patient is not None
        assert patient["name"] == "Maria"

    def test_ca008_placeholder_nao_substitui_nome_valido(self):
        """CA-008: upsert(phone, name=<telefone>) NÃO substitui nome válido."""
        from src.application.services.patient_service import PatientService

        PatientService.upsert("1187654321", "Maria")
        PatientService.upsert("1187654321", "1187654321")  # placeholder = próprio telefone
        patient = PatientService.find_by_phone("1187654321")
        assert patient is not None
        assert patient["name"] == "Maria"

    def test_nome_curto_nao_substitui_nome_valido(self):
        """Nome com < 3 chars é placeholder."""
        from src.application.services.patient_service import PatientService

        PatientService.upsert("1187654321", "Maria Silva")
        PatientService.upsert("1187654321", "AB")  # muito curto
        patient = PatientService.find_by_phone("1187654321")
        assert patient["name"] == "Maria Silva"

    def test_plano_none_nao_zera_plano_existente(self):
        """plan=None não deve zerar o plano existente."""
        from src.application.services.patient_service import PatientService

        PatientService.upsert("1187654321", "Maria", "Unimed")
        PatientService.upsert("1187654321", "Maria Silva", None)
        patient = PatientService.find_by_phone("1187654321")
        assert patient is not None
        assert patient["plan"] == "Unimed"

    def test_nome_valido_novo_substitui_vazio(self):
        """Nome novo válido substitui paciente sem nome."""
        from src.application.services.patient_service import PatientService

        PatientService.upsert("1187654321", "")
        PatientService.upsert("1187654321", "Maria Silva")
        patient = PatientService.find_by_phone("1187654321")
        assert patient["name"] == "Maria Silva"

    def test_plano_explicito_atualiza(self):
        """Plano explícito (não-None) deve ser atualizado."""
        from src.application.services.patient_service import PatientService

        PatientService.upsert("1187654321", "Maria", "Particular")
        PatientService.upsert("1187654321", "Maria", "Unimed")
        patient = PatientService.find_by_phone("1187654321")
        assert patient["plan"] == "Unimed"


class TestSavePatientToolMerge(_DBMixin):
    """PA-02: SavePatientTool faz merge não-destrutivo."""

    def test_ca009_tool_preserva_plano_existente(self):
        """CA-009: SavePatientTool._run(phone, name, plan=None) sobre paciente com "Unimed" mantém "Unimed"."""
        from src.application.services.patient_service import PatientService
        from src.interfaces.tools.patient_tool import SavePatientTool

        PatientService.upsert("1187654321", "Maria", "Unimed")
        SavePatientTool()._run("1187654321", "Maria Silva", None)
        patient = PatientService.find_by_phone("1187654321")
        assert patient is not None
        assert patient["plan"] == "Unimed"

    def test_ca010_tool_nao_apaga_nome_valido(self):
        """CA-010: SavePatientTool._run(phone, name="") NÃO apaga o nome existente."""
        from src.application.services.patient_service import PatientService
        from src.interfaces.tools.patient_tool import SavePatientTool

        PatientService.upsert("1187654321", "Maria", "Particular")
        SavePatientTool()._run("1187654321", "", "Particular")
        patient = PatientService.find_by_phone("1187654321")
        assert patient is not None
        assert patient["name"] == "Maria"

    def test_tool_novo_paciente_cadastra(self):
        """SavePatientTool cria novo paciente se não existir."""
        from src.application.services.patient_service import PatientService
        from src.interfaces.tools.patient_tool import SavePatientTool

        result = SavePatientTool()._run("1187654321", "João da Silva", "Bradesco")
        assert "salvo" in result.lower()
        patient = PatientService.find_by_phone("1187654321")
        assert patient is not None
        assert patient["name"] == "João da Silva"
        assert patient["plan"] == "Bradesco"
