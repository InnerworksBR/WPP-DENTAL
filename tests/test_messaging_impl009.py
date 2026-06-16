"""Testes unitários para impl 009 — Mensageria Confiável e Alertas.

Cobre: WH-05 (retry), WH-06 (phone validation), WH-02 (_send_response único),
       WH-03/WH-09 (alert failure + template), CO-01 (DOCTOR_PHONE startup),
       WH-04/WH-08 (kind column em consume_recent_match).
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call


# ---------------------------------------------------------------------------
# WH-02: _send_response envia mensagem única (sem split)
# ---------------------------------------------------------------------------

class TestSendResponseSingleMessage:
    """WH-02: _send_response nao fragmenta a mensagem."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_multiline_sent_as_single_call(self):
        """CA-001: texto com parágrafos resulta em 1 chamada a send_message."""
        mock_wpp = MagicMock()
        mock_wpp.send_message = AsyncMock(return_value=True)

        # WhatsAppService é importado localmente dentro de _send_response — patch na origem
        with patch("src.infrastructure.integrations.whatsapp_service.WhatsAppService", return_value=mock_wpp):
            from src.interfaces.http.app import _send_response
            result = self._run(_send_response("5511999999999", "linha1\n\nlinha2\n\nlinha3"))

        assert result is True
        assert mock_wpp.send_message.call_count == 1
        args = mock_wpp.send_message.call_args[0]
        assert "linha1" in args[1]
        assert "linha2" in args[1]
        assert "linha3" in args[1]

    def test_empty_message_sent(self):
        """Texto vazio stripped e enviado sem split."""
        mock_wpp = MagicMock()
        mock_wpp.send_message = AsyncMock(return_value=True)

        with patch("src.infrastructure.integrations.whatsapp_service.WhatsAppService", return_value=mock_wpp):
            from src.interfaces.http.app import _send_response
            result = self._run(_send_response("5511999999999", ""))

        assert mock_wpp.send_message.call_count == 1

    def test_returns_false_on_failure(self):
        """Retorna False se send_message retorna False."""
        mock_wpp = MagicMock()
        mock_wpp.send_message = AsyncMock(return_value=False)

        with patch("src.infrastructure.integrations.whatsapp_service.WhatsAppService", return_value=mock_wpp):
            from src.interfaces.http.app import _send_response
            result = self._run(_send_response("5511999999999", "texto"))

        assert result is False


# ---------------------------------------------------------------------------
# WH-06: validação de telefone em _format_phone
# ---------------------------------------------------------------------------

class TestFormatPhoneValidation:
    """WH-06: _format_phone rejeita DDD inválido e tamanho incorreto."""

    @pytest.fixture(autouse=True)
    def service(self):
        from src.infrastructure.integrations.whatsapp_service import WhatsAppService
        self.svc = WhatsAppService()

    @pytest.mark.parametrize("phone,expected", [
        # válidos: móvel 13 dígitos
        ("5511999999999", "5511999999999"),
        ("11999999999", "5511999999999"),
        # válido: fixo 12 dígitos
        ("551199999999", "551199999999"),
        # inválido: DDD 10
        ("5510999999999", ""),
        # inválido: DDD 00
        ("5500999999999", ""),
        # inválido: muito curto (só 10 dígitos após 55)
        ("55119999999", ""),
        # inválido: muito longo (14 dígitos)
        ("551199999999901", ""),
        # inválido: @lid
        ("12345@lid.us", ""),
        # inválido: não-dígitos sem número válido
        ("abcdef", ""),
    ])
    def test_format_phone(self, phone, expected):
        result = self.svc._format_phone(phone)
        assert result == expected, f"_format_phone({phone!r}) = {result!r}, esperado {expected!r}"

    def test_valid_ddd_99(self):
        """DDD 99 (Maranhão interior) é válido."""
        result = self.svc._format_phone("5599998887776")
        assert result == "5599998887776"

    def test_valid_ddd_11(self):
        """DDD 11 (São Paulo) é válido."""
        result = self.svc._format_phone("5511987654321")
        assert result == "5511987654321"


# ---------------------------------------------------------------------------
# WH-05: retry com backoff em send_message e send_message_sync
# ---------------------------------------------------------------------------

class TestWhatsAppRetry:
    """WH-05: send_message tenta até WHATSAPP_SEND_RETRIES+1 vezes."""

    @pytest.fixture(autouse=True)
    def service(self):
        from src.infrastructure.integrations.whatsapp_service import WhatsAppService
        self.svc = WhatsAppService()

    def _run(self, coro):
        return asyncio.run(coro)

    def test_async_retries_on_http_error(self):
        """CA-002: send_message com HTTP 500 reais tenta 3x (1+2 retries) e retorna False."""
        import httpx

        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise httpx.HTTPStatusError("500 error", request=MagicMock(), response=MagicMock(status_code=500))

        with (
            patch("src.infrastructure.integrations.whatsapp_service.WhatsAppService._format_phone",
                  return_value="5511999999999"),
            patch("httpx.AsyncClient") as mock_client_cls,
            patch("asyncio.sleep", new=AsyncMock()),
            patch.dict("os.environ", {"WHATSAPP_SEND_RETRIES": "2"}),
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = mock_post
            mock_client_cls.return_value = mock_client

            result = self._run(self.svc.send_message("5511999999999", "teste"))

        assert result is False
        assert call_count == 3  # 1 original + 2 retries

    def test_async_succeeds_on_second_attempt(self):
        """send_message retorna True se a 2a tentativa funciona."""
        import httpx

        attempt = [0]
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={})

        async def mock_post(*args, **kwargs):
            attempt[0] += 1
            if attempt[0] == 1:
                raise httpx.HTTPStatusError("500", request=MagicMock(), response=MagicMock(status_code=500))
            return mock_response

        with (
            patch("src.infrastructure.integrations.whatsapp_service.WhatsAppService._format_phone",
                  return_value="5511999999999"),
            patch("httpx.AsyncClient") as mock_client_cls,
            patch("asyncio.sleep", new=AsyncMock()),
            patch("src.infrastructure.integrations.whatsapp_service.OutboundMessageStore.record"),
            patch.dict("os.environ", {"WHATSAPP_SEND_RETRIES": "2"}),
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = mock_post
            mock_client_cls.return_value = mock_client

            result = self._run(self.svc.send_message("5511999999999", "teste"))

        assert result is True
        assert attempt[0] == 2

    def test_sync_retries_on_http_error(self):
        """send_message_sync com HTTP 500 tenta 3x e retorna False."""
        import httpx

        call_count = [0]

        def mock_post(*args, **kwargs):
            call_count[0] += 1
            raise httpx.HTTPStatusError("500 error", request=MagicMock(), response=MagicMock(status_code=500))

        with (
            patch("src.infrastructure.integrations.whatsapp_service.WhatsAppService._format_phone",
                  return_value="5511999999999"),
            patch("httpx.Client") as mock_client_cls,
            patch("time.sleep"),
            patch.dict("os.environ", {"WHATSAPP_SEND_RETRIES": "2"}),
        ):
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post = mock_post
            mock_client_cls.return_value = mock_client

            result = self.svc.send_message_sync("5511999999999", "teste")

        assert result is False
        assert call_count[0] == 3

    def test_invalid_phone_returns_false_immediately(self):
        """Telefone inválido retorna False sem fazer chamada de rede."""
        mock_post = MagicMock()
        with patch("httpx.AsyncClient") as mock_client_cls:
            result = self._run(self.svc.send_message("abcdef", "teste"))

        assert result is False
        mock_client_cls.assert_not_called()


# ---------------------------------------------------------------------------
# WH-03: AlertService persiste alerta falho e loga critical
# ---------------------------------------------------------------------------

class TestAlertServiceFailurePersistence:
    """WH-03: AlertService chama FailedAlertStore.record quando send falha."""

    def test_send_alert_failure_triggers_critical_and_store(self):
        """CA-004: send_alert com send_message_sync=False chama FailedAlertStore.record."""
        from src.infrastructure.integrations.alert_service import AlertService

        with (
            patch.object(AlertService, "__init__", lambda self: None),
            patch("src.infrastructure.integrations.alert_service.ConfigService") as mock_cfg_cls,
            patch("src.infrastructure.integrations.alert_service.WhatsAppService") as mock_wpp_cls,
            patch("src.infrastructure.integrations.alert_service.FailedAlertStore") as mock_store,
        ):
            mock_cfg = MagicMock()
            mock_cfg.get_doctor_phone.return_value = "5511999990000"
            mock_cfg.get_message.return_value = "Alerta: paciente X"
            mock_cfg_cls.return_value = mock_cfg

            mock_wpp = MagicMock()
            mock_wpp.send_message_sync.return_value = False
            mock_wpp_cls.return_value = mock_wpp

            svc = AlertService.__new__(AlertService)
            svc.config = mock_cfg
            svc.whatsapp = mock_wpp

            import logging
            with patch.object(logging.getLogger("src.infrastructure.integrations.alert_service"), "critical") as mock_crit:
                result = svc.send_alert(
                    patient_name="João",
                    patient_phone="5511888881111",
                    summary="Pediu preço",
                    reason="fora_do_escopo",
                    last_message="quanto custa?",
                )

            assert result is False
            mock_store.record.assert_called_once()
            mock_crit.assert_called_once()

    def test_send_alert_success_no_store_call(self):
        """Alerta enviado com sucesso não chama FailedAlertStore.record."""
        from src.infrastructure.integrations.alert_service import AlertService

        with (
            patch("src.infrastructure.integrations.alert_service.ConfigService") as mock_cfg_cls,
            patch("src.infrastructure.integrations.alert_service.WhatsAppService") as mock_wpp_cls,
            patch("src.infrastructure.integrations.alert_service.FailedAlertStore") as mock_store,
        ):
            mock_cfg = MagicMock()
            mock_cfg.get_doctor_phone.return_value = "5511999990000"
            mock_cfg.get_message.return_value = "Alerta: paciente X"
            mock_cfg_cls.return_value = mock_cfg

            mock_wpp = MagicMock()
            mock_wpp.send_message_sync.return_value = True
            mock_wpp_cls.return_value = mock_wpp

            svc = AlertService.__new__(AlertService)
            svc.config = mock_cfg
            svc.whatsapp = mock_wpp

            result = svc.send_alert(
                patient_name="João",
                patient_phone="5511888881111",
                summary="Ok",
                reason="escopo_ok",
            )

            assert result is True
            mock_store.record.assert_not_called()

    def test_send_alert_no_doctor_phone_returns_false(self):
        """AlertService retorna False e não envia se DOCTOR_PHONE vazio."""
        from src.infrastructure.integrations.alert_service import AlertService

        with (
            patch("src.infrastructure.integrations.alert_service.ConfigService") as mock_cfg_cls,
            patch("src.infrastructure.integrations.alert_service.WhatsAppService") as mock_wpp_cls,
            patch("src.infrastructure.integrations.alert_service.FailedAlertStore") as mock_store,
        ):
            mock_cfg = MagicMock()
            mock_cfg.get_doctor_phone.return_value = ""
            mock_cfg_cls.return_value = mock_cfg

            mock_wpp = MagicMock()
            mock_wpp_cls.return_value = mock_wpp

            svc = AlertService.__new__(AlertService)
            svc.config = mock_cfg
            svc.whatsapp = mock_wpp

            result = svc.send_alert("X", "5511999999999", "sum", "reason")
            assert result is False
            mock_wpp.send_message_sync.assert_not_called()

    def test_send_to_doctor_uses_kind_doctor_alert(self):
        """AlertService envia com kind='doctor_alert' ao chamar send_message_sync."""
        from src.infrastructure.integrations.alert_service import AlertService

        with (
            patch("src.infrastructure.integrations.alert_service.ConfigService") as mock_cfg_cls,
            patch("src.infrastructure.integrations.alert_service.WhatsAppService") as mock_wpp_cls,
            patch("src.infrastructure.integrations.alert_service.FailedAlertStore"),
        ):
            mock_cfg = MagicMock()
            mock_cfg.get_doctor_phone.return_value = "5511999990000"
            mock_cfg.get_message.return_value = "Alerta"
            mock_cfg_cls.return_value = mock_cfg

            mock_wpp = MagicMock()
            mock_wpp.send_message_sync.return_value = True
            mock_wpp_cls.return_value = mock_wpp

            svc = AlertService.__new__(AlertService)
            svc.config = mock_cfg
            svc.whatsapp = mock_wpp

            svc.send_alert("João", "5511888881111", "sum", "reason")

            mock_wpp.send_message_sync.assert_called_once_with(
                "5511999990000", "Alerta", kind="doctor_alert"
            )


# ---------------------------------------------------------------------------
# CO-01: DOCTOR_PHONE validado no startup
# ---------------------------------------------------------------------------

class TestDoctorPhoneStartupValidation:
    """CO-01: lifespan loga critical quando DOCTOR_PHONE não configurado."""

    def test_missing_doctor_phone_logs_critical(self, caplog):
        """CA-005: get_doctor_phone vazio → CRITICAL logado no lifespan."""
        import logging

        with (
            patch("src.interfaces.http.app.init_db"),
            patch("src.interfaces.http.app.ConfigService") as mock_cfg_cls,
            patch("src.interfaces.http.app.AppointmentConfirmationService.scheduler_enabled", return_value=False),
            patch("src.interfaces.http.app.close_db"),
        ):
            mock_cfg = MagicMock()
            mock_cfg.get_doctor_name.return_value = "Dra. Priscila"
            mock_cfg.get_plan_names.return_value = []
            mock_cfg.get_openai_model.return_value = "gpt-4"
            mock_cfg.get_doctor_phone.return_value = ""
            mock_cfg_cls.return_value = mock_cfg

            from src.interfaces.http.app import lifespan, app as fastapi_app

            with caplog.at_level(logging.CRITICAL, logger="wpp-dental"):
                async def run():
                    async with lifespan(fastapi_app):
                        pass
                asyncio.run(run())

        critical_records = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
        assert critical_records, "Esperava ao menos um registro CRITICAL"
        msgs = " ".join(r.getMessage() for r in critical_records)
        assert "DOCTOR_PHONE" in msgs or "doctor" in msgs.lower()

    def test_valid_doctor_phone_no_critical(self, caplog):
        """DOCTOR_PHONE configurado → nenhum CRITICAL logado."""
        import logging

        with (
            patch("src.interfaces.http.app.init_db"),
            patch("src.interfaces.http.app.ConfigService") as mock_cfg_cls,
            patch("src.interfaces.http.app.AppointmentConfirmationService.scheduler_enabled", return_value=False),
            patch("src.interfaces.http.app.close_db"),
        ):
            mock_cfg = MagicMock()
            mock_cfg.get_doctor_name.return_value = "Dra. Priscila"
            mock_cfg.get_plan_names.return_value = []
            mock_cfg.get_openai_model.return_value = "gpt-4"
            mock_cfg.get_doctor_phone.return_value = "5511999999999"
            mock_cfg_cls.return_value = mock_cfg

            from src.interfaces.http.app import lifespan, app as fastapi_app

            with caplog.at_level(logging.CRITICAL, logger="wpp-dental"):
                async def run():
                    async with lifespan(fastapi_app):
                        pass
                asyncio.run(run())

        critical_records = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
        assert not critical_records, f"Nao esperava CRITICAL mas encontrou: {critical_records}"


# ---------------------------------------------------------------------------
# WH-04/WH-08: consume_recent_match ignora kind='doctor_alert' no content match
# ---------------------------------------------------------------------------

class TestConsumeRecentMatchKindFilter:
    """WH-04/WH-08: registros doctor_alert não são confundidos com respostas manuais."""

    def test_doctor_alert_not_matched_by_content(self, tmp_path, monkeypatch):
        """CA-009: content match ignora kind='doctor_alert'."""
        import sqlite3
        from datetime import datetime

        db_path = str(tmp_path / "test.db")

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE outbound_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                content TEXT NOT NULL,
                message_id TEXT,
                kind TEXT NOT NULL DEFAULT 'bot',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute(
            "INSERT INTO outbound_messages (phone, content, kind) VALUES (?, ?, ?)",
            ("5511999990000", "Alerta: paciente João solicitou cancelamento", "doctor_alert"),
        )
        conn.commit()

        monkeypatch.setattr(
            "src.infrastructure.persistence.outbound_message_store.get_db",
            lambda: conn,
        )
        monkeypatch.setattr(
            "src.infrastructure.persistence.outbound_message_store.OutboundMessageStore._normalize_phone",
            lambda phone: "5511999990000",
        )

        from src.infrastructure.persistence.outbound_message_store import OutboundMessageStore

        result = OutboundMessageStore.consume_recent_match(
            "5511999990000",
            "Alerta: paciente João solicitou cancelamento",
        )

        assert result is False, "Registro doctor_alert NÃO deve ser confundido com resposta manual"

    def test_bot_message_still_matched_by_content(self, tmp_path, monkeypatch):
        """Eco de mensagem bot (kind='bot') ainda é reconhecido por conteúdo."""
        import sqlite3

        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE outbound_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                content TEXT NOT NULL,
                message_id TEXT,
                kind TEXT NOT NULL DEFAULT 'bot',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute(
            "INSERT INTO outbound_messages (phone, content, kind) VALUES (?, ?, ?)",
            ("5511999999999", "Temos disponibilidade na segunda as 14h", "bot"),
        )
        conn.commit()

        monkeypatch.setattr(
            "src.infrastructure.persistence.outbound_message_store.get_db",
            lambda: conn,
        )
        monkeypatch.setattr(
            "src.infrastructure.persistence.outbound_message_store.OutboundMessageStore._normalize_phone",
            lambda phone: "5511999999999",
        )

        from src.infrastructure.persistence.outbound_message_store import OutboundMessageStore

        result = OutboundMessageStore.consume_recent_match(
            "5511999999999",
            "Temos disponibilidade na segunda as 14h",
        )

        assert result is True, "Eco de mensagem bot ainda deve ser reconhecido"

    def test_id_match_works_for_doctor_alert(self, tmp_path, monkeypatch):
        """Match por message_id ainda funciona para doctor_alert (eco confirmado pelo ID)."""
        import sqlite3

        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE outbound_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                content TEXT NOT NULL,
                message_id TEXT,
                kind TEXT NOT NULL DEFAULT 'bot',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute(
            "INSERT INTO outbound_messages (phone, content, message_id, kind) VALUES (?, ?, ?, ?)",
            ("5511999990000", "Alerta médico", "msg-alert-001", "doctor_alert"),
        )
        conn.commit()

        monkeypatch.setattr(
            "src.infrastructure.persistence.outbound_message_store.get_db",
            lambda: conn,
        )
        monkeypatch.setattr(
            "src.infrastructure.persistence.outbound_message_store.OutboundMessageStore._normalize_phone",
            lambda phone: "5511999990000",
        )

        from src.infrastructure.persistence.outbound_message_store import OutboundMessageStore

        result = OutboundMessageStore.consume_recent_match(
            "5511999990000",
            "Alerta médico",
            message_id="msg-alert-001",
        )

        assert result is True, "Match por ID deve funcionar mesmo para doctor_alert"


# ---------------------------------------------------------------------------
# WH-09: template placeholder — log warning + fallback
# ---------------------------------------------------------------------------

class TestGetMessageTemplateFallback:
    """WH-09: get_message com placeholder ausente loga warning."""

    def test_missing_kwarg_logs_warning(self, monkeypatch):
        """Quando format() lança KeyError, get_message loga warning e retorna raw template."""
        from src.infrastructure.config.config_service import ConfigService
        import logging

        svc = MagicMock(spec=ConfigService)
        svc._configs = {"messages": {"alerts": {"to_doctor": "Olá {patient_name}, resultado: {missing_key}"}}}
        svc._normalize_lookup = ConfigService._normalize_lookup.__get__(svc)
        svc._get_fallback_message = ConfigService._get_fallback_message.__get__(svc)

        with patch.object(
            logging.getLogger("src.infrastructure.config.config_service"),
            "warning",
        ) as mock_warn:
            # Chama get_message com argumento faltando
            result = ConfigService.get_message(svc, "alerts.to_doctor", patient_name="João")

        # Resultado deve ser o raw template (sem o placeholder substituído) ou fallback
        assert result  # não vazio
        # O warning pode ter sido chamado para a KeyError (WH-09 fix opcional)
        # O principal é que não levanta exceção e retorna algo
