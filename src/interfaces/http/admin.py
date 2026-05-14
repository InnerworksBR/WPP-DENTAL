"""Painel administrativo web do WPP-DENTAL."""

from __future__ import annotations

import hmac
import os
from datetime import datetime, timedelta
from typing import Any

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ...infrastructure.config.config_service import ConfigService
from ...infrastructure.integrations.calendar_service import CalendarService, SAO_PAULO_TZ
from ...infrastructure.persistence.connection import get_db

load_dotenv()

router = APIRouter(prefix="/admin", tags=["admin"])


class DayBlockPayload(BaseModel):
    """Payload para criacao de bloqueio de dia."""

    date: str
    reason: str = ""


def _clean_key(value: str) -> str:
    """Normaliza chaves vindas de .env, headers e query string."""
    cleaned = (value or "").strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        return cleaned[1:-1].strip()
    return cleaned


def _configured_admin_keys() -> list[str]:
    """Retorna chaves aceitas para o painel administrativo."""
    candidates = (
        os.getenv("ADMIN_API_KEY", ""),
        os.getenv("WEBHOOK_API_KEY", ""),
        os.getenv("EVOLUTION_WEBHOOK_API_KEY", ""),
    )
    return [cleaned for key in candidates if (cleaned := _clean_key(key))]


def _extract_key(request: Request) -> str:
    for header_name in ("x-admin-key", "x-api-key", "apikey"):
        value = request.headers.get(header_name)
        if value:
            return _clean_key(value)

    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return _clean_key(authorization[7:])

    return _clean_key(request.query_params.get("key", ""))


def _require_admin(request: Request) -> None:
    keys = _configured_admin_keys()
    if not keys:
        return

    provided = _extract_key(request)
    if not provided or not any(hmac.compare_digest(provided, key) for key in keys):
        raise HTTPException(status_code=401, detail="Chave administrativa invalida.")


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _parse_date(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=SAO_PAULO_TZ)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Data invalida. Use YYYY-MM-DD.") from exc


def _calendar_error_payload(exc: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "error": str(exc),
        "items": [],
    }


@router.get("", response_class=HTMLResponse)
async def admin_page() -> HTMLResponse:
    """Entrega o painel administrativo."""
    return HTMLResponse(_ADMIN_HTML)


@router.get("/api/auth-config")
async def get_auth_config() -> dict[str, Any]:
    """Informa se os endpoints administrativos exigem chave."""
    return {"protected": bool(_configured_admin_keys())}


@router.get("/api/summary")
async def get_summary(request: Request) -> dict[str, Any]:
    _require_admin(request)
    db = get_db()
    config = ConfigService()

    since_24h = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    since_7d = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    return {
        "service": "wpp-dental",
        "doctor": config.get_doctor_name(),
        "plans": config.get_plan_names(),
        "metrics": {
            "patients": db.execute("SELECT COUNT(*) AS total FROM patients").fetchone()["total"],
            "conversations": db.execute(
                "SELECT COUNT(DISTINCT phone) AS total FROM conversation_history"
            ).fetchone()["total"],
            "messages_24h": db.execute(
                "SELECT COUNT(*) AS total FROM conversation_history WHERE created_at >= ?",
                (since_24h,),
            ).fetchone()["total"],
            "active_states": db.execute(
                "SELECT COUNT(*) AS total FROM conversation_state"
            ).fetchone()["total"],
            "failed_messages_7d": db.execute(
                "SELECT COUNT(*) AS total FROM processed_messages "
                "WHERE status = 'failed' AND processed_at >= ?",
                (since_7d,),
            ).fetchone()["total"],
            "pending_confirmations": db.execute(
                "SELECT COUNT(*) AS total FROM appointment_confirmations "
                "WHERE status IN ('sent', 'pending')"
            ).fetchone()["total"],
        },
    }


@router.get("/api/conversations")
async def list_conversations(request: Request, limit: int = 40) -> dict[str, Any]:
    _require_admin(request)
    limit = max(1, min(limit, 100))
    db = get_db()
    rows = db.execute(
        """
        SELECT
            h.phone,
            MAX(h.created_at) AS last_message_at,
            COUNT(*) AS message_count,
            p.name AS patient_name,
            p.plan AS plan,
            s.state_json AS state_json,
            s.updated_at AS state_updated_at
        FROM conversation_history h
        LEFT JOIN patients p ON p.phone = h.phone
        LEFT JOIN conversation_state s ON s.phone = h.phone
        GROUP BY h.phone
        ORDER BY last_message_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    items = []
    for row in rows:
        last = db.execute(
            "SELECT role, content FROM conversation_history "
            "WHERE phone = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (row["phone"],),
        ).fetchone()
        items.append(
            {
                **_row_to_dict(row),
                "last_role": last["role"] if last else "",
                "last_content": last["content"] if last else "",
            }
        )
    return {"items": items}


@router.get("/api/conversations/{phone}")
async def get_conversation(request: Request, phone: str, limit: int = 120) -> dict[str, Any]:
    _require_admin(request)
    limit = max(1, min(limit, 300))
    db = get_db()
    messages = db.execute(
        "SELECT id, role, content, created_at FROM conversation_history "
        "WHERE phone = ? ORDER BY created_at DESC, id DESC LIMIT ?",
        (phone, limit),
    ).fetchall()
    patient = db.execute("SELECT * FROM patients WHERE phone = ?", (phone,)).fetchone()
    state = db.execute("SELECT * FROM conversation_state WHERE phone = ?", (phone,)).fetchone()
    interactions = db.execute(
        "SELECT i.id, i.type, i.summary, i.created_at FROM interactions i "
        "JOIN patients p ON p.id = i.patient_id "
        "WHERE p.phone = ? ORDER BY i.created_at DESC LIMIT 30",
        (phone,),
    ).fetchall()

    ordered_messages = [_row_to_dict(row) for row in messages]
    ordered_messages.reverse()
    return {
        "phone": phone,
        "patient": _row_to_dict(patient),
        "state": _row_to_dict(state),
        "messages": ordered_messages,
        "interactions": [_row_to_dict(row) for row in interactions],
    }


@router.get("/api/errors")
async def list_errors(request: Request, limit: int = 50) -> dict[str, Any]:
    _require_admin(request)
    limit = max(1, min(limit, 100))
    db = get_db()
    failed = db.execute(
        "SELECT message_id, phone, status, last_error, processed_at "
        "FROM processed_messages "
        "WHERE status IN ('failed', 'processing') "
        "ORDER BY processed_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    confirmations = db.execute(
        "SELECT id, event_id, phone, patient_name, appointment_start, status, "
        "response_text, sent_at, responded_at "
        "FROM appointment_confirmations "
        "ORDER BY sent_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return {
        "processed_messages": [_row_to_dict(row) for row in failed],
        "appointment_confirmations": [_row_to_dict(row) for row in confirmations],
    }


@router.get("/api/appointments")
async def list_appointments(request: Request, days: int = 30) -> dict[str, Any]:
    _require_admin(request)
    days = max(1, min(days, 120))
    calendar = CalendarService()
    start = datetime.now(SAO_PAULO_TZ)
    end = start + timedelta(days=days)
    try:
        events = calendar.list_events_between(start, end)
    except Exception as exc:
        return _calendar_error_payload(exc)

    items = []
    for event in events:
        if CalendarService.event_is_day_block(event):
            continue
        start_data = event.get("start", {})
        if not start_data.get("dateTime"):
            continue
        patient_phone = CalendarService._extract_patient_phone_from_event(event)
        if not patient_phone:
            continue
        items.append(
            {
                "event_id": str(event.get("id", "")),
                "summary": str(event.get("summary", "")),
                "patient_name": CalendarService._extract_patient_name_from_event(event),
                "patient_phone": patient_phone,
                "start": start_data.get("dateTime", ""),
                "end": event.get("end", {}).get("dateTime", ""),
            }
        )
    return {"ok": True, "items": items}


@router.get("/api/blocks")
async def list_blocks(request: Request, days: int = 90) -> dict[str, Any]:
    _require_admin(request)
    days = max(1, min(days, 365))
    calendar = CalendarService()
    start = datetime.now(SAO_PAULO_TZ) - timedelta(days=7)
    end = datetime.now(SAO_PAULO_TZ) + timedelta(days=days)
    try:
        blocks = calendar.list_day_blocks(start, end)
    except Exception as exc:
        return _calendar_error_payload(exc)
    return {"ok": True, "items": blocks}


@router.post("/api/blocks")
async def create_block(request: Request, payload: DayBlockPayload) -> dict[str, Any]:
    _require_admin(request)
    calendar = CalendarService()
    try:
        event = calendar.create_day_block(_parse_date(payload.date), payload.reason)
    except HTTPException:
        raise
    except Exception as exc:
        return _calendar_error_payload(exc)
    return {"ok": True, "event_id": event.get("id"), "event": event}


@router.delete("/api/blocks/{event_id}")
async def delete_block(request: Request, event_id: str) -> dict[str, Any]:
    _require_admin(request)
    calendar = CalendarService()
    try:
        deleted = calendar.delete_day_block(event_id)
    except Exception as exc:
        return _calendar_error_payload(exc)
    if not deleted:
        return {"ok": False, "error": "Bloqueio nao encontrado.", "items": []}
    return {"ok": True}


_ADMIN_HTML = r"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WPP-DENTAL Admin</title>
  <style>
    :root {
      --bg: #f5f7f8;
      --panel: #ffffff;
      --text: #172026;
      --muted: #66737c;
      --line: #dce4e8;
      --brand: #0b7285;
      --brand-strong: #075766;
      --danger: #b42318;
      --ok: #13795b;
      --soft: #e9f6f7;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--text); }
    button, input, select { font: inherit; }
    button {
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--text);
      min-height: 38px;
      padding: 0 12px;
      border-radius: 6px;
      cursor: pointer;
    }
    button.primary { background: var(--brand); color: white; border-color: var(--brand); }
    button.primary:hover { background: var(--brand-strong); }
    button.danger { color: var(--danger); border-color: #f1c4bf; }
    input {
      width: 100%;
      min-height: 38px;
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: white;
      color: var(--text);
    }
    .layout { display: grid; grid-template-columns: 260px 1fr; min-height: 100vh; }
    aside { border-right: 1px solid var(--line); background: #fbfcfd; padding: 20px; }
    main { padding: 22px; min-width: 0; }
    .brand { font-weight: 800; font-size: 18px; margin-bottom: 4px; }
    .muted { color: var(--muted); font-size: 13px; }
    .auth { display: grid; gap: 8px; margin: 22px 0; }
    nav { display: grid; gap: 8px; margin-top: 20px; }
    nav button { text-align: left; }
    nav button.active { background: var(--soft); border-color: #9bd1d8; color: var(--brand-strong); }
    .topbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 18px; }
    .grid { display: grid; grid-template-columns: repeat(6, minmax(120px, 1fr)); gap: 12px; }
    .stat, .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }
    .stat { padding: 14px; }
    .stat strong { display: block; font-size: 24px; margin-top: 6px; }
    .panel { padding: 14px; margin-top: 14px; }
    .panel h2 { font-size: 16px; margin: 0 0 12px; }
    .toolbar { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }
    .split { display: grid; grid-template-columns: 360px 1fr; gap: 14px; align-items: start; }
    .list { display: grid; gap: 8px; }
    .row { border: 1px solid var(--line); border-radius: 8px; padding: 10px; background: white; }
    .row.clickable { cursor: pointer; }
    .row.clickable:hover { border-color: #9bd1d8; }
    .row-title { display: flex; justify-content: space-between; gap: 10px; font-weight: 700; }
    .preview { color: var(--muted); margin-top: 6px; font-size: 13px; overflow-wrap: anywhere; }
    .messages { display: grid; gap: 8px; max-height: 66vh; overflow: auto; padding-right: 4px; }
    .msg { max-width: 760px; padding: 10px 12px; border-radius: 8px; border: 1px solid var(--line); background: #fff; }
    .msg.patient { border-left: 4px solid var(--brand); }
    .msg.assistant { border-left: 4px solid var(--ok); }
    .msg.doctor { border-left: 4px solid #7c3aed; }
    .msg .meta { color: var(--muted); font-size: 12px; margin-bottom: 5px; }
    table { width: 100%; border-collapse: collapse; background: white; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
    th, td { padding: 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; font-size: 14px; }
    th { background: #f7fafb; color: #43515a; }
    td { overflow-wrap: anywhere; }
    .hidden { display: none; }
    .status { min-height: 20px; margin-top: 8px; color: var(--muted); font-size: 13px; }
    .error { color: var(--danger); }
    .ok { color: var(--ok); }
    @media (max-width: 980px) {
      .layout { grid-template-columns: 1fr; }
      aside { border-right: 0; border-bottom: 1px solid var(--line); }
      .grid { grid-template-columns: repeat(2, 1fr); }
      .split { grid-template-columns: 1fr; }
      .topbar { align-items: flex-start; flex-direction: column; }
    }
  </style>
</head>
<body>
  <div class="layout">
    <aside>
      <div class="brand">WPP-DENTAL</div>
      <div class="muted">Painel administrativo</div>
      <div class="auth">
        <label class="muted" for="admin-key">Chave de acesso</label>
        <input id="admin-key" type="password" placeholder="ADMIN_API_KEY ou WEBHOOK_API_KEY">
        <button id="save-key" class="primary">Entrar</button>
        <div id="auth-status" class="status"></div>
      </div>
      <nav>
        <button class="active" data-view="dashboard">Visao geral</button>
        <button data-view="conversations">Conversas</button>
        <button data-view="appointments">Marcacoes</button>
        <button data-view="blocks">Bloqueios</button>
        <button data-view="errors">Erros</button>
      </nav>
    </aside>
    <main>
      <div class="topbar">
        <div>
          <h1 id="page-title">Visao geral</h1>
          <div id="page-subtitle" class="muted"></div>
        </div>
        <button id="refresh">Atualizar</button>
      </div>

      <section id="view-dashboard">
        <div id="stats" class="grid"></div>
      </section>

      <section id="view-conversations" class="hidden">
        <div class="split">
          <div class="panel">
            <h2>Conversas recentes</h2>
            <div id="conversation-list" class="list"></div>
          </div>
          <div class="panel">
            <h2 id="conversation-title">Selecione uma conversa</h2>
            <div id="conversation-detail"></div>
          </div>
        </div>
      </section>

      <section id="view-appointments" class="hidden">
        <div class="panel">
          <div class="toolbar">
            <strong>Proximas marcacoes</strong>
            <input id="appointments-days" type="number" min="1" max="120" value="30" style="max-width: 110px">
            <button id="load-appointments">Carregar</button>
          </div>
          <div id="appointments-table"></div>
        </div>
      </section>

      <section id="view-blocks" class="hidden">
        <div class="panel">
          <h2>Novo bloqueio de dia</h2>
          <div class="toolbar">
            <input id="block-date" type="date" style="max-width: 180px">
            <input id="block-reason" type="text" placeholder="Motivo do bloqueio">
            <button id="create-block" class="primary">Bloquear dia</button>
          </div>
          <div id="block-status" class="status"></div>
        </div>
        <div class="panel">
          <h2>Bloqueios ativos</h2>
          <div id="blocks-table"></div>
        </div>
      </section>

      <section id="view-errors" class="hidden">
        <div class="panel">
          <h2>Falhas e confirmacoes</h2>
          <div id="errors-table"></div>
        </div>
      </section>
    </main>
  </div>

  <script>
    const state = {
      view: "dashboard",
      key: localStorage.getItem("wppDentalAdminKey") || "",
      protected: true,
    };

    const $ = (id) => document.getElementById(id);
    $("admin-key").value = state.key;

    function headers() {
      return state.key ? {"x-admin-key": state.key, "content-type": "application/json"} : {"content-type": "application/json"};
    }

    async function api(path, options = {}) {
      const response = await fetch(path, {...options, headers: {...headers(), ...(options.headers || {})}});
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `HTTP ${response.status}`);
      }
      return response.json();
    }

    async function loadAuthConfig() {
      try {
        const response = await fetch("/admin/api/auth-config");
        const data = await response.json();
        state.protected = Boolean(data.protected);
      } catch (error) {
        state.protected = true;
      }
    }

    function setStatus(id, text, type = "") {
      const node = $(id);
      node.textContent = text;
      node.className = `status ${type}`;
    }

    function fmt(value) {
      if (!value) return "-";
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? value : date.toLocaleString("pt-BR");
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
      }[char]));
    }

    function table(headers, rows) {
      if (!rows.length) return '<div class="muted">Nenhum registro encontrado.</div>';
      return `<table><thead><tr>${headers.map(h => `<th>${escapeHtml(h)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table>`;
    }

    function setView(view) {
      state.view = view;
      document.querySelectorAll("nav button").forEach(btn => btn.classList.toggle("active", btn.dataset.view === view));
      document.querySelectorAll("main section").forEach(section => section.classList.add("hidden"));
      $(`view-${view}`).classList.remove("hidden");
      $("page-title").textContent = {
        dashboard: "Visao geral",
        conversations: "Conversas",
        appointments: "Marcacoes",
        blocks: "Bloqueios",
        errors: "Erros",
      }[view];
      loadCurrent();
    }

    async function loadDashboard() {
      const data = await api("/admin/api/summary");
      $("page-subtitle").textContent = `${data.doctor} · ${data.service}`;
      const labels = [
        ["Pacientes", "patients"],
        ["Conversas", "conversations"],
        ["Msgs 24h", "messages_24h"],
        ["Estados ativos", "active_states"],
        ["Erros 7d", "failed_messages_7d"],
        ["Confirmacoes", "pending_confirmations"],
      ];
      $("stats").innerHTML = labels.map(([label, key]) =>
        `<div class="stat"><span class="muted">${label}</span><strong>${data.metrics[key]}</strong></div>`
      ).join("");
    }

    async function loadConversations() {
      const data = await api("/admin/api/conversations");
      $("conversation-list").innerHTML = data.items.map(item => `
        <div class="row clickable" data-phone="${escapeHtml(item.phone)}">
          <div class="row-title"><span>${escapeHtml(item.patient_name || item.phone)}</span><span class="muted">${fmt(item.last_message_at)}</span></div>
          <div class="preview">${escapeHtml(item.last_role)}: ${escapeHtml(item.last_content || "")}</div>
        </div>
      `).join("") || '<div class="muted">Sem conversas ainda.</div>';
      document.querySelectorAll("[data-phone]").forEach(row => {
        row.addEventListener("click", () => loadConversation(row.dataset.phone));
      });
    }

    async function loadConversation(phone) {
      const data = await api(`/admin/api/conversations/${encodeURIComponent(phone)}`);
      $("conversation-title").textContent = data.patient?.name || phone;
      const interactions = data.interactions.map(i => `<div class="row"><strong>${escapeHtml(i.type)}</strong><div class="preview">${escapeHtml(i.summary)} · ${fmt(i.created_at)}</div></div>`).join("");
      const messages = data.messages.map(msg => `
        <div class="msg ${escapeHtml(msg.role)}">
          <div class="meta">${escapeHtml(msg.role)} · ${fmt(msg.created_at)}</div>
          <div>${escapeHtml(msg.content)}</div>
        </div>
      `).join("");
      $("conversation-detail").innerHTML = `
        <div class="muted">Telefone: ${escapeHtml(phone)} ${data.patient?.plan ? `· Plano: ${escapeHtml(data.patient.plan)}` : ""}</div>
        <div class="messages" style="margin-top: 12px">${messages || '<div class="muted">Sem mensagens.</div>'}</div>
        <div style="margin-top: 14px"><strong>Interacoes registradas</strong></div>
        <div class="list" style="margin-top: 8px">${interactions || '<div class="muted">Nenhuma interacao registrada.</div>'}</div>
      `;
    }

    async function loadAppointments() {
      const days = $("appointments-days").value || 30;
      const data = await api(`/admin/api/appointments?days=${encodeURIComponent(days)}`);
      if (!data.ok) {
        $("appointments-table").innerHTML = `<div class="error">${escapeHtml(data.error)}</div>`;
        return;
      }
      $("appointments-table").innerHTML = table(["Paciente", "Telefone", "Inicio", "Fim"], data.items.map(item => `
        <tr><td>${escapeHtml(item.patient_name)}</td><td>${escapeHtml(item.patient_phone)}</td><td>${fmt(item.start)}</td><td>${fmt(item.end)}</td></tr>
      `));
    }

    async function loadBlocks() {
      const data = await api("/admin/api/blocks");
      if (!data.ok) {
        $("blocks-table").innerHTML = `<div class="error">${escapeHtml(data.error)}</div>`;
        return;
      }
      $("blocks-table").innerHTML = table(["Data", "Motivo", ""], data.items.map(item => `
        <tr>
          <td>${escapeHtml(item.start_date)}</td>
          <td>${escapeHtml(item.description || item.summary)}</td>
          <td><button class="danger" data-delete-block="${escapeHtml(item.event_id)}">Remover</button></td>
        </tr>
      `));
      document.querySelectorAll("[data-delete-block]").forEach(button => {
        button.addEventListener("click", async () => {
          const data = await api(`/admin/api/blocks/${encodeURIComponent(button.dataset.deleteBlock)}`, {method: "DELETE"});
          if (!data.ok) {
            $("blocks-table").innerHTML = `<div class="error">${escapeHtml(data.error || "Nao foi possivel remover o bloqueio.")}</div>`;
            return;
          }
          loadBlocks();
        });
      });
    }

    async function createBlock() {
      setStatus("block-status", "Criando bloqueio...");
      try {
        const data = await api("/admin/api/blocks", {
          method: "POST",
          body: JSON.stringify({date: $("block-date").value, reason: $("block-reason").value}),
        });
        if (!data.ok) throw new Error(data.error || "Nao foi possivel criar o bloqueio.");
        $("block-reason").value = "";
        setStatus("block-status", "Dia bloqueado com sucesso.", "ok");
        loadBlocks();
      } catch (error) {
        setStatus("block-status", error.message, "error");
      }
    }

    async function loadErrors() {
      const data = await api("/admin/api/errors");
      const failedRows = data.processed_messages.map(item => `
        <tr><td>${escapeHtml(item.status)}</td><td>${escapeHtml(item.phone)}</td><td>${escapeHtml(item.last_error || item.message_id)}</td><td>${fmt(item.processed_at)}</td></tr>
      `);
      const confirmationRows = data.appointment_confirmations.map(item => `
        <tr><td>${escapeHtml(item.status)}</td><td>${escapeHtml(item.patient_name || item.phone)}</td><td>${escapeHtml(item.response_text || item.event_id)}</td><td>${fmt(item.sent_at)}</td></tr>
      `);
      $("errors-table").innerHTML =
        "<h3>Mensagens processadas</h3>" + table(["Status", "Telefone", "Detalhe", "Quando"], failedRows) +
        '<h3 style="margin-top:18px">Confirmacoes de consulta</h3>' + table(["Status", "Paciente", "Detalhe", "Enviado"], confirmationRows);
    }

    async function loadCurrent() {
      try {
        setStatus("auth-status", "");
        if (state.protected && !state.key) {
          setStatus("auth-status", "Informe a chave administrativa para carregar os dados.");
          return;
        }
        if (state.view === "dashboard") await loadDashboard();
        if (state.view === "conversations") await loadConversations();
        if (state.view === "appointments") await loadAppointments();
        if (state.view === "blocks") await loadBlocks();
        if (state.view === "errors") await loadErrors();
      } catch (error) {
        setStatus("auth-status", error.message, "error");
      }
    }

    document.querySelectorAll("nav button").forEach(btn => btn.addEventListener("click", () => setView(btn.dataset.view)));
    $("save-key").addEventListener("click", () => {
      state.key = $("admin-key").value.trim();
      localStorage.setItem("wppDentalAdminKey", state.key);
      setStatus("auth-status", "Chave salva.", "ok");
      loadCurrent();
    });
    $("refresh").addEventListener("click", loadCurrent);
    $("load-appointments").addEventListener("click", loadAppointments);
    $("create-block").addEventListener("click", createBlock);

    const today = new Date();
    $("block-date").value = today.toISOString().slice(0, 10);
    loadAuthConfig().then(loadCurrent);
  </script>
</body>
</html>
"""
