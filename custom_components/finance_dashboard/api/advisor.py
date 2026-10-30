"""Advisor endpoints (fork-only): payslips, estimates, Claude analysis.

All admin-only: payslips and the analysis are the most personal data the
integration holds. Every call to Claude starts a background job (see
``advisor.py``); the POST answers at once with the job id and the card
polls the GET.

- GET    /payroll                 payslips, INPS statement, profile, estimates, read jobs
- POST   /payroll                 {kind: payslip|inps, filename, data: base64 PDF} -> job
- DELETE /payroll/{item_id}       a payslip by id, or "inps"
- POST   /payroll/profile         profile changes
- GET    /advisor/analysis        Claude availability, last analysis, analysis jobs
- POST   /advisor/analysis        {context: card aggregates} -> job
"""

from __future__ import annotations

from datetime import date

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from .. import advisor, payroll
from ..const import DOMAIN


def _admin(request: web.Request) -> bool:
    user = request.get("hass_user")
    return bool(user and user.is_admin)


def _forbidden(view: HomeAssistantView) -> web.Response:
    return view.json({"ok": False, "error": "admin_required"}, status_code=403)


def _error(view: HomeAssistantView, err: advisor.AdvisorError, status: int = 400) -> web.Response:
    return view.json({"ok": False, "error": err.code, "detail": err.detail}, status_code=status)


class FinanceDashboardPayrollView(HomeAssistantView):
    """Payslips and the estimates built on them."""

    url = f"/api/{DOMAIN}/payroll"
    name = f"api:{DOMAIN}:payroll"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        if not _admin(request):
            return _forbidden(self)
        hass = request.app["hass"]
        try:
            state = await advisor.async_load(hass)
        except advisor.AdvisorError as err:
            return _error(self, err, 404)
        today = date.today()
        inps = state["inps"]
        return self.json(
            {
                "ok": True,
                "claude": advisor.anthropic_key(hass) is not None,
                "payslips": state["payslips"],
                "inps": {
                    k: inps.get(k)
                    for k in (
                        "birth_date",
                        "statement_date",
                        "total_weeks",
                        "file",
                        "read_at",
                        "notes",
                    )
                }
                | {"years": len(inps.get("rows") or [])}
                if inps
                else None,
                "profile": state["profile"],
                "estimates": payroll.estimates(state["payslips"], inps, state["profile"], today),
                "jobs": advisor.jobs_view(hass, "read_"),
            }
        )

    async def post(self, request: web.Request) -> web.Response:
        if not _admin(request):
            return _forbidden(self)
        hass = request.app["hass"]
        try:
            body = await request.json()
        except ValueError:
            return self.json({"ok": False, "error": "invalid_json"}, status_code=400)
        if not isinstance(body, dict):
            return self.json({"ok": False, "error": "invalid_body"}, status_code=400)
        if advisor.anthropic_key(hass) is None:
            return _error(
                self,
                advisor.AdvisorError("claude_not_configured", "set up the Anthropic integration"),
                409,
            )
        try:
            pdf = advisor.decode_pdf(body.get("data"))
            job = advisor.start_read(
                hass, str(body.get("kind")), str(body.get("filename") or "documento.pdf"), pdf
            )
        except advisor.AdvisorError as err:
            return _error(self, err)
        return self.json({"ok": True, "job": job}, status_code=202)


class FinanceDashboardPayrollItemView(HomeAssistantView):
    """Forget one payslip, or the INPS statement."""

    url = f"/api/{DOMAIN}/payroll/{{item_id}}"
    name = f"api:{DOMAIN}:payroll_item"
    requires_auth = True

    async def delete(self, request: web.Request, item_id: str) -> web.Response:
        if not _admin(request):
            return _forbidden(self)
        hass = request.app["hass"]
        state = await advisor.async_load(hass)
        if item_id == "inps":
            found = state["inps"] is not None
            state["inps"] = None
        else:
            before = len(state["payslips"])
            state["payslips"] = [p for p in state["payslips"] if p.get("id") != item_id]
            found = len(state["payslips"]) < before
        if not found:
            return self.json({"ok": False, "error": "not_found"}, status_code=404)
        await advisor.async_save(hass, state)
        return self.json({"ok": True})


class FinanceDashboardPayrollProfileView(HomeAssistantView):
    """Birth date, sex, retirement age and the real-rate assumptions."""

    url = f"/api/{DOMAIN}/payroll/profile"
    name = f"api:{DOMAIN}:payroll_profile"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        if not _admin(request):
            return _forbidden(self)
        hass = request.app["hass"]
        try:
            changes = payroll.validate_profile(await request.json())
        except ValueError as err:
            return self.json(
                {"ok": False, "error": "invalid_profile", "detail": str(err)}, status_code=400
            )
        state = await advisor.async_load(hass)
        state["profile"] = payroll.merge_profile({**state["profile"], **changes})
        await advisor.async_save(hass, state)
        return self.json({"ok": True, "profile": state["profile"]})


class FinanceDashboardAnalysisView(HomeAssistantView):
    """Claude's analysis of spending, portfolio and pension estimates."""

    url = f"/api/{DOMAIN}/advisor/analysis"
    name = f"api:{DOMAIN}:advisor_analysis"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        if not _admin(request):
            return _forbidden(self)
        hass = request.app["hass"]
        try:
            state = await advisor.async_load(hass)
        except advisor.AdvisorError as err:
            return _error(self, err, 404)
        return self.json(
            {
                "ok": True,
                "claude": advisor.anthropic_key(hass) is not None,
                "model": advisor.MODEL,
                "last": state["analysis"],
                "jobs": advisor.jobs_view(hass, "analysis"),
            }
        )

    async def post(self, request: web.Request) -> web.Response:  # noqa: PLR0911
        if not _admin(request):
            return _forbidden(self)
        hass = request.app["hass"]
        try:
            body = await request.json()
        except ValueError:
            return self.json({"ok": False, "error": "invalid_json"}, status_code=400)
        context = body.get("context") if isinstance(body, dict) else None
        if not isinstance(context, dict):
            return self.json(
                {"ok": False, "error": "invalid_body", "detail": "context must be an object"},
                status_code=400,
            )
        if advisor.anthropic_key(hass) is None:
            return _error(
                self,
                advisor.AdvisorError("claude_not_configured", "set up the Anthropic integration"),
                409,
            )
        if any(j["state"] == "running" for j in advisor.jobs_view(hass, "analysis")):
            return self.json({"ok": False, "error": "already_running"}, status_code=409)
        try:
            job = advisor.start_analysis(hass, context)
        except advisor.AdvisorError as err:
            return _error(self, err)
        return self.json({"ok": True, "job": job}, status_code=202)
