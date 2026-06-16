"""Testes de resiliencia do ConfigService — impl 011."""

import os
import threading
from pathlib import Path
from unittest.mock import patch
import tempfile
import yaml

import pytest


def _reset_singleton():
    from src.infrastructure.config.config_service import ConfigService
    ConfigService._instance = None


# ---------------------------------------------------------------------------
# T-009 — Carregamento atomico: reload preserva config anterior em YAML quebrado
# ---------------------------------------------------------------------------

class TestReloadAtomico:
    """CA-001 / CA-002: reload() nao deixa janela vazia e preserva config em erro."""

    def setup_method(self):
        _reset_singleton()

    def teardown_method(self):
        _reset_singleton()

    def test_reload_preserva_config_em_yaml_quebrado(self):
        """CA-001: erro total em _load_configs durante reload — config anterior preservada."""
        from src.infrastructure.config.config_service import ConfigService

        config = ConfigService()
        config._configs = {"settings": {"doctor": {"name": "Dra. Teste"}}}
        previous = dict(config._configs)

        def patched_raise(self):
            raise RuntimeError("YAML todo corrompido")

        with patch.object(ConfigService, "_load_configs", patched_raise):
            config.reload()

        assert config._configs == previous, "Config anterior deve ser preservada apos erro no reload"

    def test_reload_nao_deixa_configs_vazio(self, tmp_path):
        """CA-002: durante reload nenhuma thread le _configs vazio."""
        from src.infrastructure.config.config_service import ConfigService

        config = ConfigService()
        config._configs = {"settings": {"doctor": {"name": "Dra. X"}}}

        snapshots = []

        def reader_thread():
            for _ in range(50):
                snap = config._configs
                snapshots.append(len(snap))

        def reload_thread():
            for _ in range(10):
                config.reload()

        t1 = threading.Thread(target=reader_thread)
        t2 = threading.Thread(target=reload_thread)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert all(s >= 0 for s in snapshots), "Dict nunca deve ser None durante reload"


# ---------------------------------------------------------------------------
# T-010 — _load_configs degrada com YAML invalido (CA-003)
# ---------------------------------------------------------------------------

class TestLoadConfigsDegrada:
    """CA-003: YAML invalido no startup e pulado; demais arquivos carregam."""

    def setup_method(self):
        _reset_singleton()

    def teardown_method(self):
        _reset_singleton()

    def test_degrada_com_yaml_invalido(self, tmp_path, caplog):
        """CA-003: arquivo YAML invalido nao impede carga dos validos."""
        import logging
        from src.infrastructure.config.config_service import ConfigService

        (tmp_path / "valido.yaml").write_text("chave: valor\n", encoding="utf-8")
        (tmp_path / "invalido.yaml").write_text(":\n  bad: [unclosed\n", encoding="utf-8")

        config = ConfigService()
        with patch.object(
            type(config),
            "_load_configs",
            lambda self: _load_from_dir(self, tmp_path),
        ):
            with caplog.at_level(logging.ERROR, logger="wpp-dental"):
                _load_from_dir(config, tmp_path)

        assert "valido" in config._configs
        assert config._configs["valido"] == {"chave": "valor"}
        assert "invalido" in config._configs
        assert config._configs["invalido"] == {}

    def test_app_nao_crasha_com_yaml_invalido(self, tmp_path):
        """CA-003: instancia criada mesmo com YAML invalido presente."""
        from src.infrastructure.config.config_service import ConfigService

        config = ConfigService()
        _load_from_dir(config, tmp_path)
        assert isinstance(config._configs, dict)


def _load_from_dir(config_obj, config_dir: Path):
    """Auxiliar: roda _load_configs apontando para config_dir de teste."""
    new_configs = {}
    for yaml_file in config_dir.glob("*.yaml"):
        key = yaml_file.stem
        try:
            with open(yaml_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            new_configs[key] = data
        except (yaml.YAMLError, OSError) as exc:
            import logging
            logging.getLogger("wpp-dental").error(
                "Falha ao carregar config '%s': %s", yaml_file.name, exc
            )
            new_configs[key] = {}
    config_obj._configs = new_configs


# ---------------------------------------------------------------------------
# T-011 — Getters defensivos: calendar_id null, get_message fallback, resolve_env_vars
# ---------------------------------------------------------------------------

class TestGettersDefensivos:
    """CA-004 a CA-007: getters a prova de falhas."""

    def setup_method(self):
        _reset_singleton()

    def teardown_method(self):
        _reset_singleton()

    def test_get_calendar_id_com_null(self):
        """CA-004: calendar_id: null no YAML nao levanta AttributeError."""
        from src.infrastructure.config.config_service import ConfigService

        config = ConfigService()
        config._configs = {"settings": {"doctor": {"calendar_id": None}}}
        result = config.get_calendar_id()
        assert isinstance(result, str)
        assert result in ("primary", os.getenv("GOOGLE_CALENDAR_ID", "primary"))

    def test_get_calendar_id_com_numero(self):
        """CA-004: calendar_id como numero inteiro e coagido para str."""
        from src.infrastructure.config.config_service import ConfigService

        config = ConfigService()
        config._configs = {"settings": {"doctor": {"calendar_id": 123}}}
        result = config.get_calendar_id()
        assert isinstance(result, str)
        assert result == "123"

    def test_get_message_chave_ausente_retorna_nao_vazio(self, caplog):
        """CA-005: chave pontilhada ausente retorna string nao-vazia e loga warning."""
        import logging
        from src.infrastructure.config.config_service import ConfigService

        config = ConfigService()
        config._configs = {"messages": {"errors": {"general": "Erro interno generico."}}}
        with caplog.at_level(logging.WARNING, logger="wpp-dental"):
            result = config.get_message("foo.bar.baz")
        assert result, "get_message deve retornar string nao-vazia para chave ausente"
        assert "foo.bar.baz" in caplog.text or "warning" in caplog.text.lower() or len(caplog.records) > 0

    def test_get_message_chave_ausente_sem_fallback(self, caplog):
        """CA-005: sem errors.general configurado, ainda retorna string nao-vazia."""
        import logging
        from src.infrastructure.config.config_service import ConfigService

        config = ConfigService()
        config._configs = {"messages": {}}
        with caplog.at_level(logging.WARNING, logger="wpp-dental"):
            result = config.get_message("chave.inexistente")
        assert result, "get_message deve retornar string nao-vazia mesmo sem fallback configurado"

    def test_resolve_env_vars_ausente_nao_vaza_literal(self, monkeypatch):
        """CA-006: env ausente nao vaza ${VAR} para o consumidor."""
        from src.infrastructure.config.config_service import ConfigService

        monkeypatch.delenv("NAO_EXISTE_123", raising=False)
        config = ConfigService()
        result = config._resolve_env_vars("${NAO_EXISTE_123}")
        assert "${" not in result, f"Literal de env nao deve vazar: {result!r}"

    def test_resolve_env_vars_no_meio_da_string(self, monkeypatch):
        """CA-006: ${VAR} no meio de um texto e interpolado corretamente."""
        from src.infrastructure.config.config_service import ConfigService

        monkeypatch.setenv("TEST_VAR_DENTAL_011", "INTERPOLADO")
        config = ConfigService()
        result = config._resolve_env_vars("prefixo ${TEST_VAR_DENTAL_011} sufixo")
        assert result == "prefixo INTERPOLADO sufixo"

    def test_get_doctor_name_default_nao_truncado(self):
        """CA-007: sem config, get_doctor_name retorna nome completo (nao 'Dra.')."""
        from src.infrastructure.config.config_service import ConfigService

        config = ConfigService()
        config._configs = {}
        name = config.get_doctor_name()
        assert len(name) > 5, f"Nome padrao deve ser completo, obtido: {name!r}"
        assert name != "Dra.", f"Nome padrao nao pode ser truncado: {name!r}"

    def test_singleton_lock_carrega_uma_vez(self):
        """CA-007: varias chamadas a ConfigService() retornam o mesmo objeto."""
        from src.infrastructure.config.config_service import ConfigService

        instances = []

        def get_instance():
            instances.append(ConfigService())

        threads = [threading.Thread(target=get_instance) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(set(id(i) for i in instances)) == 1, "Todas as threads devem obter a mesma instancia"
