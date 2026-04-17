"""Servidor webhook principal do WPP-DENTAL."""

import asyncio
import hmac
import logging
import os
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

load_dotenv()

from ...application.services.clean_agent_service import CleanAgentService
from ...application.services.appointment_confirmation_service import AppointmentConfirmationService
from ...application.services.conversation_service import ConversationService
from ...application.services.conversation_state_service import ConversationStateService
from ...application.services.handoff_service import HandoffService
from ...application.services.patient_service import PatientService
from ...domain.policies.appointment_offer_service import AppointmentOfferService
from ...domain.policies.phone_service import normalize_conversation_phone, normalize_internal_phone
from ...domain.policies.scope_guard_service import ScopeGuardService
from ...infrastructure.config.config_service import ConfigService
from ...infrastructure.integrations.calendar_service import CalendarService
from ...infrastructure.persistence import OutboundMessageStore
from ...infrastructure.persistence.connection import close_db, get_db, init_db

from ...infrastructure.logging_config import setup_logging

setup_logging(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("wpp-dental")
_webhook_auth_warning_logged = False
_webhook_auth_mismatch_warning_logged = False


async def _run_appointment_confirmation_scheduler() -> None:
    """Executa o cron interno diario para confirmar consultas do dia seguinte."""
    service = AppointmentConfirmationService()

    while True:
        next_run = service.get_next_run_datetime()
        sleep_seconds = max((next_run - datetime.now(next_run.tzinfo)).total_seconds(), 1)
        logger.info(
            "Proxima rotina de confirmacao automatica agendada para %s",
            next_run.strftime("%d/%m/%Y %H:%M:%S"),
        )
        await asyncio.sleep(sleep_seconds)

        try:
            result = await service.send_next_day_confirmations(reference_time=next_run)
            logger.info("Rotina de confirmacao automatica concluida: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Falha na rotina de confirmacao automatica: %s",
                exc,
                exc_info=True,
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gerencia startup e shutdown da aplicacao."""
    logger.info("WPP-DENTAL iniciando...")
    init_db()
    logger.info("Banco de dados inicializado")

    config = ConfigService()
    logger.info("Configuracoes carregadas")
    logger.info("Doutora: %s", config.get_doctor_name())
    logger.info("Planos ativos: %s", ", ".join(config.get_plan_names()))
    logger.info("Modelo LLM: %s", config.get_openai_model())
    scheduler_task = None
    if AppointmentConfirmationService.scheduler_enabled():
        scheduler_task = asyncio.create_task(_run_appointment_confirmation_scheduler())
        app.state.appointment_confirmation_scheduler = scheduler_task
        logger.info(
            "Cron interno de confirmacao diaria habilitado para %02d:00 (America/Sao_Paulo)",
            AppointmentConfirmationService.REMINDER_HOUR,
        )
    else:
        logger.info("Cron interno de confirmacao diaria desabilitado por configuracao")
    logger.info("WPP-DENTAL pronto para atender")

    try:
        yield
    finally:
        if scheduler_task is not None:
            scheduler_task.cancel()
            with suppress(asyncio.CancelledError):
                await scheduler_task

        close_db()
        logger.info("WPP-DENTAL encerrado")


app = FastAPI(
    title="WPP-DENTAL",
    description="Assistente IA de Agendamento via WhatsApp",
    version="0.1.0",
    lifespan=lifespan,
)

dental_crew = CleanAgentService()


@app.get("/")
async def root_check():
    """Endpoint raiz para health checks mais simples de plataforma."""
    return {"status": "ok", "service": "wpp-dental"}


@app.get("/health")
async def health_check():
    """Endpoint de health check."""
    return {"status": "ok", "service": "wpp-dental"}


@app.post("/webhook/message")
async def receive_message(request: Request):
    """Webhook que recebe mensagens da Evolution API."""
    try:
        payload: dict[str, Any] = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    _authenticate_request(
        request,
        payload,
        require_key=False,
        include_evolution_fallback=True,
        allow_unauthorized=True,
    )
    logger.debug("Webhook recebido: %s", payload)

    event = payload.get("event", "")
    if event not in ("messages.upsert", "MESSAGES_UPSERT", "messages"):
        logger.debug("Evento ignorado: %s", event)
        return JSONResponse({"status": "ignored", "event": event})

    data = payload.get("data", {})
    message_data = _extract_message_data(data)
    if message_data is None:
        logger.debug("Mensagem ignorada (nao e texto recebido)")
        return JSONResponse({"status": "ignored", "reason": "not_text_or_sent"})

    phone = message_data["phone"]
    text = message_data["text"]
    contact_name = message_data.get("contact_name", "")
    message_id = message_data.get("message_id", "")
    from_me = message_data.get("from_me", "") == "1"

    if message_id:
        claimed, state = _try_claim_message_processing(message_id, phone)
        if not claimed:
            logger.debug("Mensagem duplicada/em processamento ignorada: %s (%s)", message_id, state)
            return JSONResponse(
                {"status": "duplicate", "message_id": message_id, "state": state}
            )

    if from_me:
        return _handle_outbound_message(
            phone=phone,
            text=text,
            contact_name=contact_name,
            message_id=message_id,
        )

    logger.info("Mensagem de %s (%s): %s...", phone, contact_name, text[:50])

    if HandoffService.is_active(phone):
        expires_at = HandoffService.get_expires_at(phone)
        logger.info(
            "Mensagem de %s ignorada por handoff manual ativo ate %s",
            phone,
            expires_at.isoformat(timespec="seconds") if expires_at else "desconhecido",
        )
        ConversationService.add_message(phone, "patient", text)
        if message_id:
            _mark_message_processed(message_id, phone)
        return JSONResponse(
            {
                "status": "handoff_active",
                "phone": phone,
                "handoff_until": (
                    expires_at.replace(microsecond=0).isoformat() if expires_at else None
                ),
            }
        )

    if ConversationService.reset_context_if_finished(phone):
        ConversationStateService.clear(phone)

    current_state = ConversationStateService.get(phone)

    escalation_response = await _handle_scope_escalation(
        phone=phone,
        text=text,
        contact_name=contact_name,
        message_id=message_id,
    )
    if escalation_response is not None:
        return escalation_response

    if current_state.stage not in {
        "awaiting_cancel_confirmation",
        AppointmentConfirmationService.CONFIRMATION_STAGE,
    }:
        slot_selection_response = await _handle_offered_slot_selection(
            phone=phone,
            text=text,
            contact_name=contact_name,
            message_id=message_id,
        )
        if slot_selection_response is not None:
            return slot_selection_response

    history_text = ConversationService.format_history_for_prompt(phone)
    is_first_message = not ConversationService.has_recent_history(phone)

    try:
        response_text = str(
            dental_crew.process_message(
                patient_phone=phone,
                patient_message=text,
                patient_name=contact_name,
                history_text=history_text,
                is_first_message=is_first_message,
            )
        ).strip()
    except Exception as exc:
        logger.error("Erro ao processar mensagem de %s: %s", phone, exc, exc_info=True)
        return await _handle_processing_failure(
            phone=phone,
            text=text,
            contact_name=contact_name,
            message_id=message_id,
        )

    if not ScopeGuardService.response_is_safe(response_text):
        logger.warning("Resposta fora do escopo detectada para %s; substituindo por escalacao segura.", phone)
        response_text = await _force_safe_escalation_response(
            phone=phone,
            text=text,
            contact_name=contact_name,
        )

    delivered = await _send_response(phone, response_text)
    if not delivered:
        if message_id:
            _mark_message_failed(message_id, phone, "Failed to deliver message to patient")
        raise HTTPException(status_code=502, detail="Failed to deliver message to patient")

    ConversationService.add_message(phone, "patient", text)
    ConversationService.add_message(phone, "assistant", response_text)
    if message_id:
        _mark_message_processed(message_id, phone)

    return JSONResponse(
        {
            "status": "processed",
            "phone": phone,
            "response_preview": response_text[:100],
        }
    )


def _extract_message_data(data: dict[str, Any] | list[Any]) -> dict[str, str] | None:
    """Extrai os dados da mensagem do payload da Evolution API."""
    if isinstance(data, dict) and "key" in data and "message" in data:
        return _build_message_data(data)

    messages = data if isinstance(data, list) else data.get("messages", [])
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, dict):
                extracted = _build_message_data(message)
                if extracted is not None:
                    return extracted

    return None


def _build_message_data(message_wrapper: dict[str, Any]) -> dict[str, str] | None:
    """Constroi o dict padrao com dados da mensagem."""
    key = message_wrapper.get("key", {})
    remote_jid = key.get("remoteJid", "")
    phone = normalize_conversation_phone(remote_jid)

    message = message_wrapper.get("message", {})
    text = (
        message.get("conversation")
        or message.get("extendedTextMessage", {}).get("text")
        or ""
    )
    if not text:
        return None

    return {
        "phone": phone,
        "text": text,
        "contact_name": message_wrapper.get("pushName", ""),
        "message_id": key.get("id", ""),
        "from_me": "1" if key.get("fromMe", False) else "0",
    }


def _handle_outbound_message(
    *,
    phone: str,
    text: str,
    contact_name: str,
    message_id: str,
):
    """Processa webhooks de mensagens enviadas pela propria instancia do WhatsApp."""
    if OutboundMessageStore.consume_recent_match(phone, text):
        logger.debug("Eco de mensagem automatica ignorado para %s", phone)
        if message_id:
            _mark_message_processed(message_id, phone)
        return JSONResponse(
            {
                "status": "ignored",
                "phone": phone,
                "reason": "assistant_outbound_echo",
            }
        )

    expires_at = HandoffService.activate(phone)
    logger.info(
        "Handoff manual ativado para %s (%s) ate %s",
        phone,
        contact_name,
        expires_at.isoformat(timespec="seconds"),
    )
    ConversationService.add_message(phone, "doctor", text)
    if message_id:
        _mark_message_processed(message_id, phone)
    return JSONResponse(
        {
            "status": "handoff_activated",
            "phone": phone,
            "handoff_until": expires_at.replace(microsecond=0).isoformat(),
        }
    )


def _get_configured_api_keys(
    include_evolution_fallback: bool = False,
) -> tuple[list[str], list[str]]:
    """Retorna as chaves dedicadas e as chaves de fallback configuradas."""
    dedicated_keys = [
        value.strip()
        for value in (
            os.getenv("WEBHOOK_API_KEY", ""),
            os.getenv("EVOLUTION_WEBHOOK_API_KEY", ""),
        )
        if value and value.strip()
    ]
    fallback_keys = []
    if include_evolution_fallback:
        evolution_api_key = os.getenv("EVOLUTION_API_KEY", "")
        if evolution_api_key and evolution_api_key.strip():
            fallback_keys.append(evolution_api_key.strip())
    return dedicated_keys, fallback_keys


def _extract_request_api_key(request: Request, payload: dict[str, Any] | None = None) -> str:
    """Extrai a chave enviada pelo chamador em formatos comuns."""
    for header_name in ("apikey", "x-api-key", "x-webhook-key"):
        value = request.headers.get(header_name)
        if value:
            return value.strip()

    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()

    for query_name in ("apikey", "token", "key"):
        value = request.query_params.get(query_name)
        if value:
            return value.strip()

    if isinstance(payload, dict):
        for payload_key in ("apikey", "token", "key"):
            value = payload.get(payload_key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return ""


def _get_patient_escalation_message() -> str:
    """Retorna a mensagem padrao enviada ao paciente em caso de escalacao."""
    config = ConfigService()
    return config.get_message(
        "escalation.to_patient",
        doctor_name=config.get_doctor_name(),
    ).strip()


def _build_patient_name(phone: str, contact_name: str) -> str:
    """Resolve o nome mais confiavel disponivel para o paciente."""
    patient_name = PatientService.resolve_name(phone)
    if patient_name:
        return patient_name
    return normalize_internal_phone(phone)


def _save_patient_if_missing(phone: str, patient_name: str) -> None:
    """Cria um cadastro minimo quando o paciente ainda nao existe."""
    state = ConversationStateService.get(phone)
    PatientService.upsert(phone, patient_name, state.plan_name or None)


def _register_scheduling_interaction(phone: str, summary: str) -> None:
    """Registra o agendamento realizado a partir de uma escolha de horario ofertado."""
    state = ConversationStateService.get(phone)
    interaction_type = "reschedule" if state.intent == "reschedule" else "schedule"
    PatientService.save_interaction(phone, interaction_type, summary)


def _build_confirmation_message(date_str: str, time_str: str) -> str:
    """Gera a confirmacao padrao de agendamento."""
    config = ConfigService()
    base = config.get_message(
        "scheduling.confirmed",
        date=date_str,
        time=time_str,
        doctor_name=config.get_doctor_name(),
    ).strip()
    address = config.get_doctor_address().strip()
    if address:
        return f"{base}\n\nEndereco: {address}"
    return base


def _build_slot_confirmation_request_message(
    patient_name: str,
    date_str: str,
    time_str: str,
) -> str:
    """Solicita confirmacao explicita antes de criar a consulta."""
    first_name = (patient_name or "").strip().split()[0] if patient_name else ""
    prefix = f"{first_name}, " if first_name else ""
    return (
        f"{prefix}separei este horario para voce 😊\n"
        f"{date_str} as {time_str}\n\n"
        "Posso confirmar sua consulta?"
    )


def _split_response_messages(response_text: str) -> list[str]:
    """Divide uma resposta em blocos curtos para envio no WhatsApp."""
    chunks = []
    for chunk in (response_text or "").split("\n\n"):
        normalized = chunk.strip()
        if normalized:
            chunks.append(normalized)
    return chunks or [response_text.strip()]


async def _send_response(phone: str, response_text: str) -> bool:
    """Envia uma resposta ao paciente, separando paragrafos quando fizer sentido."""
    from ...infrastructure.integrations.whatsapp_service import WhatsAppService

    whatsapp = WhatsAppService()
    for chunk in _split_response_messages(response_text):
        delivered = await whatsapp.send_message(phone, chunk)
        if not delivered:
            return False
    return True


async def _force_safe_escalation_response(
    phone: str,
    text: str,
    contact_name: str,
) -> str:
    """Troca uma resposta insegura por uma escalacao segura e alerta a doutora."""
    await _send_scope_alert(
        patient_phone=phone,
        patient_name=contact_name or "Desconhecido",
        summary="Resposta gerada fora do escopo foi bloqueada automaticamente.",
        reason="fora_do_escopo",
        last_message=text,
    )
    ConversationStateService.clear(phone)
    return _get_patient_escalation_message()


async def _handle_offered_slot_selection(
    phone: str,
    text: str,
    contact_name: str,
    message_id: str,
):
    """Resolve escolhas objetivas de horarios ja ofertados sem depender do LLM."""
    history = ConversationService.get_history(phone, limit=6)
    patient_name = _build_patient_name(phone, contact_name)
    calendar = CalendarService()

    pending_confirmation = AppointmentOfferService.extract_latest_confirmation_request(history)
    if pending_confirmation and AppointmentOfferService.is_affirmative_confirmation(text):
        datetime_str = AppointmentOfferService.build_datetime_str(
            pending_confirmation.date_str,
            pending_confirmation.time_str,
        )
        state = ConversationStateService.get(phone)

        try:
            calendar.create_appointment_if_available(
                patient_name=patient_name,
                patient_phone=phone,
                start_time=datetime.strptime(datetime_str, "%d/%m/%Y %H:%M"),
            )
        except ValueError as exc:
            response_text = (
                "Esse horario acabou de ficar indisponivel. 😕\n"
                f"Era o de {pending_confirmation.date_str} as {pending_confirmation.time_str}.\n\n"
                "Se quiser, posso te mostrar outras opcoes."
            )
            logger.info("Confirmacao de horario ofertado falhou para %s: %s", phone, exc)
        else:
            _save_patient_if_missing(phone, patient_name)
            if state.intent == "reschedule" and state.reschedule_event_id:
                cancelled = calendar.cancel_appointment(state.reschedule_event_id)
                if not cancelled:
                    await _send_scope_alert(
                        patient_phone=phone,
                        patient_name=patient_name,
                        summary=(
                            "Novo horario confirmado, mas a consulta anterior nao foi cancelada "
                            "automaticamente."
                        ),
                        reason="remarcacao_parcial",
                        last_message=text,
                    )
            _register_scheduling_interaction(
                phone,
                f"Agendamento confirmado em {pending_confirmation.date_str} as {pending_confirmation.time_str}",
            )
            response_text = _build_confirmation_message(
                pending_confirmation.date_str,
                pending_confirmation.time_str,
            )
            ConversationStateService.clear(phone)

        delivered = await _send_response(phone, response_text)
        if not delivered:
            if message_id:
                _mark_message_failed(
                    message_id,
                    phone,
                    "Failed to deliver slot confirmation resolution message to patient",
                )
            raise HTTPException(
                status_code=502,
                detail="Failed to deliver slot confirmation resolution message to patient",
            )

        ConversationService.add_message(phone, "patient", text)
        ConversationService.add_message(phone, "assistant", response_text)
        if message_id:
            _mark_message_processed(message_id, phone)

        return JSONResponse(
            {
                "status": "slot_confirmation_resolved",
                "phone": phone,
                "response_preview": response_text[:100],
                "selected_time": pending_confirmation.time_str,
            }
        )

    offer = AppointmentOfferService.extract_latest_offer(history)
    if offer is None:
        return None

    selected_time = AppointmentOfferService.resolve_selection(text, offer)
    if selected_time is None:
        return None

    response_text = _build_slot_confirmation_request_message(
        patient_name=patient_name,
        date_str=offer.date_str,
        time_str=selected_time,
    )

    delivered = await _send_response(phone, response_text)
    if not delivered:
        if message_id:
            _mark_message_failed(
                message_id,
                phone,
                "Failed to deliver slot selection resolution message to patient",
            )
        raise HTTPException(
            status_code=502,
            detail="Failed to deliver slot selection resolution message to patient",
        )

    ConversationService.add_message(phone, "patient", text)
    ConversationService.add_message(phone, "assistant", response_text)
    if message_id:
        _mark_message_processed(message_id, phone)

    return JSONResponse(
        {
            "status": "slot_confirmation_requested",
            "phone": phone,
            "response_preview": response_text[:100],
            "selected_time": selected_time,
        }
    )


async def _send_scope_alert(
    patient_phone: str,
    patient_name: str,
    summary: str,
    reason: str,
    last_message: str,
) -> None:
    """Envia alerta de escopo para a doutora sem interromper o fluxo."""
    try:
        from ...infrastructure.integrations.alert_service import AlertService

        alert = AlertService()
        alert.send_alert(
            patient_name=patient_name,
            patient_phone=patient_phone,
            summary=summary,
            reason=reason,
            last_message=last_message,
        )
    except Exception as exc:
        logger.error("Falha ao enviar alerta de escopo: %s", exc, exc_info=True)


async def _handle_scope_escalation(
    phone: str,
    text: str,
    contact_name: str,
    message_id: str,
):
    """Interrompe o fluxo normal quando a mensagem do paciente foge do escopo."""
    decision = ScopeGuardService.classify_patient_message(text)
    if decision is None:
        return None

    await _send_scope_alert(
        patient_phone=phone,
        patient_name=contact_name or "Desconhecido",
        summary=decision.summary,
        reason=decision.reason,
        last_message=text,
    )

    response_text = _get_patient_escalation_message()
    ConversationStateService.clear(phone)

    delivered = await _send_response(phone, response_text)
    if not delivered:
        if message_id:
            _mark_message_failed(message_id, phone, "Failed to deliver escalation message to patient")
        raise HTTPException(status_code=502, detail="Failed to deliver escalation message to patient")

    ConversationService.add_message(phone, "patient", text)
    ConversationService.add_message(phone, "assistant", response_text)
    if message_id:
        _mark_message_processed(message_id, phone)

    return JSONResponse(
        {
            "status": "escalated",
            "phone": phone,
            "response_preview": response_text[:100],
            "reason": decision.reason,
        }
    )


def _authenticate_request(
    request: Request,
    payload: dict[str, Any] | None = None,
    *,
    require_key: bool = True,
    include_evolution_fallback: bool = False,
    allow_unauthorized: bool = False,
) -> None:
    """Valida se a chamada veio com a chave configurada."""
    global _webhook_auth_warning_logged, _webhook_auth_mismatch_warning_logged

    dedicated_keys, fallback_keys = _get_configured_api_keys(
        include_evolution_fallback=include_evolution_fallback
    )
    accepted_keys = dedicated_keys + fallback_keys

    if not dedicated_keys:
        if require_key:
            if not accepted_keys:
                logger.error("Webhook API key nao configurada.")
                raise HTTPException(status_code=503, detail="Webhook authentication not configured")
        else:
            if not accepted_keys and not _webhook_auth_warning_logged:
                logger.warning(
                    "Webhook /webhook/message sem autenticacao dedicada configurada. "
                    "Defina WEBHOOK_API_KEY para proteger esse endpoint."
                )
                _webhook_auth_warning_logged = True
            return

    if not accepted_keys:
        if not _webhook_auth_warning_logged:
            logger.warning(
                "Webhook /webhook/message sem autenticacao dedicada configurada. "
                "Defina WEBHOOK_API_KEY para proteger esse endpoint."
            )
            _webhook_auth_warning_logged = True
        return

    provided_api_key = _extract_request_api_key(request, payload)
    if not provided_api_key or not any(
        hmac.compare_digest(provided_api_key, expected_api_key)
        for expected_api_key in accepted_keys
    ):
        if allow_unauthorized:
            if not _webhook_auth_mismatch_warning_logged:
                logger.warning(
                    "Webhook /webhook/message recebido sem chave valida. "
                    "A requisicao sera aceita para compatibilidade com a Evolution."
                )
                _webhook_auth_mismatch_warning_logged = True
            return
        raise HTTPException(status_code=401, detail="Unauthorized webhook request")


def _is_processing_stale(processed_at: str | None, max_age_minutes: int = 5) -> bool:
    """Indica se um registro em processamento ficou preso por tempo demais."""
    if not processed_at:
        return True
    try:
        created_at = datetime.strptime(processed_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    return datetime.utcnow() - created_at > timedelta(minutes=max_age_minutes)


def _try_claim_message_processing(message_id: str, phone: str) -> tuple[bool, str]:
    """Tenta reservar o processamento de uma mensagem."""
    db = get_db()
    cursor = db.execute(
        "INSERT OR IGNORE INTO processed_messages (message_id, phone, status, last_error) "
        "VALUES (?, ?, 'processing', NULL)",
        (message_id, phone),
    )
    if cursor.rowcount == 1:
        db.commit()
        return True, "claimed"

    row = db.execute(
        "SELECT status, processed_at FROM processed_messages WHERE message_id = ?",
        (message_id,),
    ).fetchone()
    if row is None:
        return False, "missing"

    status = (row["status"] or "processed").lower()
    if status == "failed" or (status == "processing" and _is_processing_stale(row["processed_at"])):
        cursor = db.execute(
            "UPDATE processed_messages "
            "SET phone = ?, status = 'processing', last_error = NULL, processed_at = CURRENT_TIMESTAMP "
            "WHERE message_id = ?",
            (phone, message_id),
        )
        db.commit()
        if cursor.rowcount == 1:
            return True, "reclaimed"

    return False, status


def _mark_message_processed(message_id: str, phone: str) -> None:
    """Marca o message_id como processado com sucesso."""
    db = get_db()
    db.execute(
        "UPDATE processed_messages "
        "SET phone = ?, status = 'processed', last_error = NULL, processed_at = CURRENT_TIMESTAMP "
        "WHERE message_id = ?",
        (phone, message_id),
    )
    db.commit()


def _mark_message_failed(message_id: str, phone: str, error: str) -> None:
    """Registra falha para permitir retry futuro do mesmo webhook."""
    db = get_db()
    db.execute(
        "UPDATE processed_messages "
        "SET phone = ?, status = 'failed', last_error = ?, processed_at = CURRENT_TIMESTAMP "
        "WHERE message_id = ?",
        (phone, error[:500], message_id),
    )
    db.commit()


async def _notify_doctor_of_processing_error(
    patient_name: str,
    patient_phone: str,
    last_message: str,
) -> None:
    """Tenta alertar a doutora quando houver falha interna no atendimento."""
    try:
        from ...infrastructure.integrations.alert_service import AlertService

        alert = AlertService()
        alert.send_alert(
            patient_name=patient_name,
            patient_phone=patient_phone,
            summary="Erro tecnico no processamento",
            reason="Erro interno do sistema",
            last_message=last_message,
        )
    except Exception as alert_error:
        logger.error("Falha ao alertar a doutora: %s", alert_error, exc_info=True)


async def _handle_processing_failure(
    phone: str,
    text: str,
    contact_name: str,
    message_id: str,
):
    """Envia uma resposta de fallback para o paciente em caso de erro interno."""
    await _notify_doctor_of_processing_error(
        patient_name=contact_name or "Desconhecido",
        patient_phone=phone,
        last_message=text,
    )

    config = ConfigService()
    fallback_message = config.get_message(
        "errors.general",
        doctor_name=config.get_doctor_name(),
    ).strip()
    ConversationStateService.clear(phone)

    delivered = await _send_response(phone, fallback_message)
    if not delivered:
        if message_id:
            _mark_message_failed(message_id, phone, "Failed to deliver fallback message to patient")
        raise HTTPException(status_code=502, detail="Failed to deliver fallback message to patient")

    ConversationService.add_message(phone, "patient", text)
    ConversationService.add_message(phone, "assistant", fallback_message)
    if message_id:
        _mark_message_processed(message_id, phone)

    return JSONResponse(
        {
            "status": "fallback_sent",
            "phone": phone,
            "response_preview": fallback_message[:100],
        }
    )


@app.post("/webhook/reload-config")
async def reload_config(request: Request):
    """Recarrega as configuracoes YAML sem reiniciar o servidor."""
    _authenticate_request(request, include_evolution_fallback=True)
    try:
        config = ConfigService()
        config.reload()
        logger.info("Configuracoes recarregadas com sucesso")
        return {
            "status": "ok",
            "plans": config.get_plan_names(),
            "doctor": config.get_doctor_name(),
        }
    except Exception as exc:
        logger.error("Erro ao recarregar configuracoes: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
