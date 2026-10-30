"""Claude as financial advisor (fork-only): payslip reading and analysis.

Two jobs, both explicitly started by the user from the card:

* **reading** a payslip or the INPS contribution statement: the PDF goes to
  Claude as a document block, a JSON schema (``payroll.py``) fixes the shape
  of the answer, and only the extracted numbers are stored - never the PDF;
* **analysis** of spending, budget, the Trade Republic portfolio and the
  payslip-based estimates: the card sends aggregates it already computed
  with its own categories (no transaction list, no IBAN), this module adds
  the estimates and asks for a structured report.

There is no API key of our own: the key is the one of Home Assistant's
Anthropic ("Claude") integration, read from its config entry. The client is
built off the event loop (constructing it loads SSL certificates) with HA's
shared httpx client, as HA's own integration does.

A call can take a minute (adaptive thinking, a long PDF): longer than the
reverse proxy waits. So every call is a background job: POST starts it and
answers at once, GET reports its state.

Storage: one Fernet-encrypted Store (``finance_dashboard.payroll``) for the
extracted payslips, the INPS statement, the profile and the last analysis.
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from datetime import UTC, date, datetime
from functools import partial
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client

from . import payroll
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = f"{DOMAIN}.payroll"
STORAGE_VERSION = 1

MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_CONTEXT_CHARS = 60_000
ANTHROPIC_DOMAIN = "anthropic"

_JOBS = "_advisor_jobs"
_CLIENT = "_advisor_client"
_STORE = "_advisor_store"


class AdvisorError(Exception):
    """A job that cannot run or failed; ``code`` goes to the card."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code


# ------------------------------------------------------------------ setup


def anthropic_key(hass: HomeAssistant) -> str | None:
    """API key of the first loaded Anthropic integration entry, if any."""
    from homeassistant.config_entries import ConfigEntryState

    for entry in hass.config_entries.async_entries(ANTHROPIC_DOMAIN):
        if entry.state is ConfigEntryState.LOADED and entry.data.get("api_key"):
            return entry.data["api_key"]
    return None


async def _client(hass: HomeAssistant):
    key = anthropic_key(hass)
    if not key:
        raise AdvisorError(
            "claude_not_configured", "Home Assistant's Anthropic integration is not set up"
        )
    data = hass.data.setdefault(DOMAIN, {})
    cached = data.get(_CLIENT)
    if cached and cached[0] == key:
        return cached[1]
    import anthropic

    client = await hass.async_add_executor_job(
        partial(anthropic.AsyncAnthropic, api_key=key, http_client=get_async_client(hass))
    )
    data[_CLIENT] = (key, client)
    return client


def _store(hass: HomeAssistant):
    data = hass.data.setdefault(DOMAIN, {})
    store = data.get(_STORE)
    if store is None:
        from .api._helpers import _get_manager

        manager = _get_manager(hass)
        creds = getattr(manager, "_credential_manager", None) if manager else None
        if creds is None:
            raise AdvisorError("not_configured", "Finance is not set up")
        store = creds.encrypted_store(STORAGE_KEY, STORAGE_VERSION)
        data[_STORE] = store
    return store


async def async_load(hass: HomeAssistant) -> dict[str, Any]:
    raw = await _store(hass).async_load()
    raw = raw if isinstance(raw, dict) else {}
    return {
        "payslips": [p for p in raw.get("payslips") or [] if isinstance(p, dict)],
        "inps": raw.get("inps") if isinstance(raw.get("inps"), dict) else None,
        "profile": payroll.merge_profile(raw.get("profile")),
        "analysis": raw.get("analysis") if isinstance(raw.get("analysis"), dict) else None,
    }


async def async_save(hass: HomeAssistant, state: dict[str, Any]) -> None:
    await _store(hass).async_save(state)


# ------------------------------------------------------------------- call


async def _ask(
    hass: HomeAssistant,
    *,
    system: str,
    content: list[dict[str, Any]],
    schema: dict[str, Any],
    effort: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """One structured request. Returns (parsed JSON, metadata)."""
    import anthropic

    client = await _client(hass)
    try:
        response = await client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            thinking={"type": "adaptive"},
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            system=system,
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.AuthenticationError as err:
        raise AdvisorError("claude_auth", "the Anthropic key was rejected") from err
    except anthropic.RateLimitError as err:
        raise AdvisorError("claude_rate_limited", "Anthropic rate limit, retry later") from err
    except anthropic.BadRequestError as err:
        raise AdvisorError("claude_bad_request", str(err.message)[:200]) from err
    except anthropic.APIStatusError as err:
        raise AdvisorError("claude_error", f"HTTP {err.status_code}") from err
    except anthropic.APIConnectionError as err:
        raise AdvisorError("claude_unreachable", "api.anthropic.com not reachable") from err

    if response.stop_reason == "refusal":
        category = (
            getattr(response.stop_details, "category", None) if response.stop_details else None
        )
        raise AdvisorError("claude_refusal", f"declined ({category or 'no category'})")
    if response.stop_reason == "max_tokens":
        raise AdvisorError("claude_truncated", "answer cut at max_tokens")
    text = "".join(b.text for b in response.content if b.type == "text")
    try:
        parsed = json.loads(text)
    except ValueError as err:
        raise AdvisorError("claude_unparsable", "answer is not JSON") from err
    usage = response.usage
    meta = {
        "model": response.model,
        "request_id": getattr(response, "_request_id", None),
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
    }
    return parsed, meta


# ------------------------------------------------------------------- jobs


def jobs(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    return hass.data.setdefault(DOMAIN, {}).setdefault(_JOBS, {})


def jobs_view(hass: HomeAssistant, kind_prefix: str) -> list[dict[str, Any]]:
    """Jobs of one family, newest first, the last ten."""
    out = [dict(j, id=i) for i, j in jobs(hass).items() if j["kind"].startswith(kind_prefix)]
    return sorted(out, key=lambda j: j["started"], reverse=True)[:10]


def _start(hass: HomeAssistant, kind: str, label: str, coro_factory) -> str:
    job_id = uuid.uuid4().hex[:12]
    job = {
        "kind": kind,
        "label": label,
        "state": "running",
        "started": datetime.now(UTC).isoformat(),
        "error": None,
    }
    jobs(hass)[job_id] = job

    async def run() -> None:
        try:
            await coro_factory()
            job["state"] = "done"
        except AdvisorError as err:
            job.update(state="error", error=err.code, detail=err.detail)
        except Exception as err:  # a job must always end in a state
            _LOGGER.exception("Advisor job %s failed", kind)
            job.update(state="error", error="internal", detail=type(err).__name__)
        job["ended"] = datetime.now(UTC).isoformat()

    hass.async_create_background_task(run(), f"{DOMAIN}_advisor_{kind}_{job_id}")
    return job_id


# -------------------------------------------------------------- documents

READ_SYSTEM = (
    "Leggi documenti di busta paga italiani e restituisci solo i numeri che ci sono scritti. "
    "Usa null per ogni campo che nel documento non compare: non stimare e non calcolare "
    "valori mancanti. Importi in euro come numeri (1234.56), date come YYYY-MM-DD, mesi come "
    "YYYY-MM."
)

PAYSLIP_PROMPT = (
    "Questo è un cedolino. Estrai: periodo di competenza; tipo (mensile, tredicesima, "
    "quattordicesima, altro); datore di lavoro; totale competenze lorde; netto in busta; "
    "imponibile previdenziale del mese e contributi INPS a carico del dipendente; imponibile "
    "fiscale del mese, IRPEF netta trattenuta, addizionali regionale e comunale trattenute "
    "nel mese; quota TFR del mese e TFR accantonato/fondo a fine mese se indicati; "
    "destinazione del TFR (azienda, fondo_tesoreria, fondo_pensione) se si capisce; data di "
    "assunzione; progressivi annui di imponibile fiscale, IRPEF e imponibile previdenziale; "
    "paga base mensile; CCNL e livello. In notes metti in una frase le voci insolite del mese"
    " (premi, conguagli, arretrati)."
)

INPS_PROMPT = (
    "Questo è l'estratto conto contributivo INPS. Estrai la data di nascita, la data "
    "dell'estratto e una riga per ogni anno e datore: anno, datore o tipo di contribuzione, "
    "settimane utili e retribuzione/reddito imponibile. Se un anno ha più periodi, una riga "
    "per periodo. total_weeks è il totale delle settimane utili."
)


def decode_pdf(raw: Any) -> bytes:
    """Base64 from the card -> PDF bytes, checked. Raises AdvisorError."""
    if not isinstance(raw, str) or not raw:
        raise AdvisorError("invalid_file", "data must be base64")
    try:
        pdf = base64.b64decode(raw.split(",", 1)[-1], validate=True)
    except ValueError as err:
        raise AdvisorError("invalid_file", "data is not base64") from err
    if len(pdf) > MAX_PDF_BYTES:
        raise AdvisorError("file_too_large", "max 10 MB")
    if not pdf.startswith(b"%PDF"):
        raise AdvisorError("invalid_file", "not a PDF")
    return pdf


def start_read(hass: HomeAssistant, kind: str, filename: str, pdf: bytes) -> str:
    """Read a payslip (kind 'payslip') or the INPS statement (kind 'inps')."""
    if kind not in ("payslip", "inps"):
        raise AdvisorError("invalid_kind", "kind must be payslip or inps")
    data = base64.standard_b64encode(pdf).decode()

    async def work() -> None:
        content = [
            {
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": data},
            },
            {"type": "text", "text": PAYSLIP_PROMPT if kind == "payslip" else INPS_PROMPT},
        ]
        schema = payroll.PAYSLIP_SCHEMA if kind == "payslip" else payroll.INPS_SCHEMA
        parsed, meta = await _ask(
            hass, system=READ_SYSTEM, content=content, schema=schema, effort="medium"
        )
        state = await async_load(hass)
        read_at = datetime.now(UTC).isoformat()
        if kind == "payslip":
            if not str(parsed.get("period") or "")[:7].replace("-", "").isdigit():
                raise AdvisorError("unreadable_payslip", "no period found")
            parsed["period"] = str(parsed["period"])[:7]
            parsed.update(
                id=uuid.uuid4().hex[:10], file=filename[:120], read_at=read_at, model=meta["model"]
            )
            key = payroll.payslip_key(parsed)
            state["payslips"] = [p for p in state["payslips"] if payroll.payslip_key(p) != key] + [
                parsed
            ]
            state["payslips"] = payroll.sorted_payslips(state["payslips"])
        else:
            parsed.update(file=filename[:120], read_at=read_at, model=meta["model"])
            state["inps"] = parsed
            if parsed.get("birth_date") and not state["profile"].get("birth_date"):
                state["profile"]["birth_date"] = str(parsed["birth_date"])[:10]
        await async_save(hass, state)

    return _start(hass, f"read_{kind}", filename[:120], work)


# --------------------------------------------------------------- analysis

ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "area": {
                        "type": "string",
                        "enum": ["spese", "budget", "investimenti", "previdenza", "tasse"],
                    },
                    "title": {"type": "string"},
                    "findings": {"type": "array", "items": {"type": "string"}},
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "priority": {"type": "string", "enum": ["alta", "media", "bassa"]},
                                "euro_impact": {"type": ["number", "null"]},
                            },
                            "required": ["text", "priority", "euro_impact"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["area", "title", "findings", "actions"],
                "additionalProperties": False,
            },
        },
        "caveats": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "sections", "caveats"],
    "additionalProperties": False,
}

ANALYSIS_SYSTEM = """\
Sei il consulente finanziario personale di un lavoratore dipendente residente in Italia. Ricevi
un JSON con: il conto corrente aggregato per ciclo di stipendio e per categoria, i budget e gli
obiettivi di risparmio che si è dato, gli addebiti ricorrenti, il portafoglio Trade Republic
(posizioni, capitale investito, rendimento) e, se ci sono, le stime di TFR, pensione e tasse
ricavate dai suoi cedolini.

Scrivi in italiano, dandogli del tu. Ogni osservazione deve poggiare su un numero del JSON e
citarlo; non inventare dati che non ci sono. Le azioni devono essere concrete e fattibili, con
l'impatto in euro quando si può stimare (null altrimenti). Per il portafoglio guarda
concentrazione, coerenza con gli obiettivi, costo e peso della liquidità; non consigliare
singoli titoli da comprare. Per la previdenza ragiona sul divario fra ultimo stipendio e
pensione stimata e su cosa lo ridurrebbe (per esempio un fondo pensione, la cui deduzione vale
all'aliquota marginale indicata). Le stime previdenziali sono in euro di oggi e dipendono dalle
ipotesi riportate: dillo nei caveats, insieme a ogni limite dei dati. Una sezione per area
presente nei dati; summary in tre frasi al massimo."""


def start_analysis(hass: HomeAssistant, context: dict[str, Any]) -> str:
    """Analyse the card's aggregates plus the payslip estimates."""
    card = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    if len(card) > MAX_CONTEXT_CHARS:
        raise AdvisorError("context_too_large", f"max {MAX_CONTEXT_CHARS} characters")

    async def work() -> None:
        state = await async_load(hass)
        today = date.today()
        payload = {
            "oggi": today.isoformat(),
            "dalla_card": context,
            "stime_da_cedolini": payroll.estimates(
                state["payslips"], state["inps"], state["profile"], today
            )
            if state["payslips"] or state["inps"]
            else None,
        }
        content = [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]
        parsed, meta = await _ask(
            hass, system=ANALYSIS_SYSTEM, content=content, schema=ANALYSIS_SCHEMA, effort="high"
        )
        state = await async_load(hass)
        state["analysis"] = {"at": datetime.now(UTC).isoformat(), "result": parsed, **meta}
        await async_save(hass, state)

    return _start(hass, "analysis", "Analisi", work)
