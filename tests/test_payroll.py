"""Payslip estimates (fork-only): taxes, TFR, pension, and the Claude jobs.

All numbers here are invented: no real payslip ever goes into the repository.
"""

from __future__ import annotations

import base64
from datetime import date
from typing import Any

import pytest

from custom_components.finance_dashboard import advisor, payroll

TODAY = date(2026, 10, 30)


def _slip(period: str, kind: str = "mensile", **kw: Any) -> dict[str, Any]:
    base = {
        "period": period,
        "kind": kind,
        "employer": "ACME SPA",
        "gross_pay": 2600.0,
        "net_pay": 1800.0,
        "social_taxable": 2600.0,
        "social_contributions": 239.0,
        "tax_taxable": 2361.0,
        "irpef_net": 420.0,
        "regional_surtax": 30.0,
        "municipal_surtax": 15.0,
        "tfr_month": 180.0,
        "tfr_accrued": None,
        "tfr_destination": "fondo_tesoreria",
        "hire_date": "2019-03-01",
        "ytd_tax_taxable": None,
        "ytd_irpef": None,
        "ytd_social_taxable": None,
        "base_monthly_salary": 2400.0,
        "ccnl": "Commercio",
        "level": "2",
        "notes": None,
    }
    base.update(kw)
    return base


def _year_of_slips() -> list[dict[str, Any]]:
    slips = [_slip(f"2025-{m:02d}") for m in (11, 12)] + [
        _slip(f"2026-{m:02d}") for m in range(1, 11)
    ]
    slips.append(_slip("2025-12", "tredicesima", social_taxable=2600.0, tfr_month=190.0))
    slips[-2]["tfr_accrued"] = 12_000.0  # October 2026
    return slips


# ------------------------------------------------------------------ rules


def test_irpef_2026_brackets() -> None:
    assert payroll.irpef(20_000) == 4_600.0
    # 28k at 23% + 10k at 33%: the 2026 second bracket, not the old 35%.
    assert payroll.irpef(38_000) == 6_440.0 + 3_300.0
    assert payroll.irpef(60_000) == 6_440.0 + 7_260.0 + 4_300.0
    assert payroll.marginal_rate(38_000) == 0.33
    assert payroll.marginal_rate(28_000) == 0.23


def test_coefficient_interpolates_by_month() -> None:
    assert payroll.coefficient(67 * 12) == 0.05608
    assert payroll.coefficient(67 * 12 + 6) == pytest.approx((0.05608 + 0.05808) / 2)
    assert payroll.coefficient(75 * 12) == 0.06510  # capped at 71
    assert payroll.coefficient(50 * 12) == 0.04204  # floored at 57


def test_pensioner_deduction_steps() -> None:
    assert payroll.pensioner_deduction(8_000) == 1_955.0
    assert payroll.pensioner_deduction(28_000) == 700.0
    assert payroll.pensioner_deduction(50_000) == 0.0


def test_profile_validation() -> None:
    assert payroll.validate_profile({"birth_date": "1990-05-12", "sex": "F"}) == {
        "birth_date": "1990-05-12",
        "sex": "F",
    }
    with pytest.raises(ValueError):
        payroll.validate_profile({"birth_date": "12/05/1990"})
    with pytest.raises(ValueError):
        payroll.validate_profile({"retirement_age_months": 900})
    with pytest.raises(ValueError):
        payroll.validate_profile({"real_salary_growth": 0.2})


# ------------------------------------------------------------- estimates


def test_annual_base_prefers_twelve_months_of_payslips() -> None:
    base = payroll.annual_social_base(_year_of_slips(), None, TODAY)
    # 12 monthly + 1 thirteenth = 13 x 2600, over 12 distinct months.
    assert base == {"amount": 33_800.0, "source": "cedolini_12_mesi"}


def test_annual_base_falls_back_to_inps_then_average() -> None:
    inps = {"rows": [{"year": 2025, "taxable_pay": 31_000.0, "weeks": 52}]}
    assert payroll.annual_social_base([_slip("2026-10")], inps, TODAY) == {
        "amount": 31_000.0,
        "source": "estratto_inps",
    }
    assert (
        payroll.annual_social_base([_slip("2026-10")], None, TODAY)["source"] == "media_mensile_x13"
    )


def test_tfr_from_payslips() -> None:
    profile = {"birth_date": "1990-05-12"}
    t = payroll.tfr_estimate(_year_of_slips(), None, profile, TODAY)
    assert t["available"] and t["quota_source"] == "cedolini"
    # 12 months of 180 + one thirteenth of 190, over 12 months.
    assert t["yearly_quota"] == 2_350.0
    assert t["now"]["gross"] == 12_000.0 and t["accrued_source"] == "cedolino"
    assert 0 < t["now"]["tax"] < t["now"]["gross"] * 0.25
    # 1.5% + 75% of 2% = 3% nominal; after 17% tax and 2% inflation: +0.49% real.
    assert t["reval_real_net"] == pytest.approx(0.0049)
    assert t["at_retirement"]["date"].startswith("2057-08")
    assert t["at_retirement"]["gross"] > t["now"]["gross"] + 30 * 2_000


def test_tfr_without_accrued_estimates_from_hire_date() -> None:
    t = payroll.tfr_estimate([_slip("2026-10")], None, {}, TODAY)
    assert t["accrued_source"] == "stima_da_assunzione"
    assert t["at_retirement"] is None  # no birth date, no projection
    assert (
        payroll.tfr_estimate([_slip("2026-10", hire_date=None)], None, {}, TODAY)["available"]
        is False
    )


def test_pension_estimate() -> None:
    inps = {
        "birth_date": "1990-05-12",
        "rows": [
            {"year": y, "taxable_pay": 28_000.0, "weeks": 52, "employer": "X", "kind": "dip"}
            for y in range(2014, 2026)
        ],
    }
    p = payroll.pension_estimate(_year_of_slips(), inps, {}, TODAY)
    assert p["available"]
    assert p["years_from_inps"][0] == 2014 and 2026 not in p["years_from_inps"]
    # 12 years at 52 weeks plus the whole 2026.
    assert p["weeks_now"] == 13 * 52
    old = p["old_age"]
    assert old["date"].startswith("2057-08") and old["age_months"] == 67 * 12 + 3
    assert old["coefficient"] == pytest.approx(0.05608 + (0.05808 - 0.05608) * 3 / 12, abs=1e-5)
    assert 0.4 < old["replacement_rate"] < 0.9
    assert old["net_monthly"] < old["gross_monthly"]
    # Born 1990, 13 years of contributions now: 43y1m arrive in 2056, before 67y3m.
    assert p["early"] and p["early"]["date"] < old["date"]


def test_pension_needs_a_birth_date() -> None:
    assert payroll.pension_estimate(_year_of_slips(), None, {}, TODAY) == {
        "available": False,
        "reason": "data_di_nascita_mancante",
    }


def test_tax_year_uses_progressives_when_present() -> None:
    slips = _year_of_slips()
    slips[-2].update(ytd_tax_taxable=23_610.0, ytd_irpef=4_200.0)
    t = payroll.tax_year(slips, TODAY)
    assert t["months"] == 10 and t["taxable_ytd"] == 23_610.0 and t["irpef_ytd"] == 4_200.0
    assert t["projected_taxable"] == pytest.approx(28_332.0)
    assert t["marginal_rate"] == 0.33
    assert payroll.tax_year([], TODAY) == {"available": False}


def test_same_month_and_kind_is_the_same_payslip() -> None:
    a, b = _slip("2026-10"), _slip("2026-10", "tredicesima")
    assert payroll.payslip_key(a) != payroll.payslip_key(b)
    assert payroll.payslip_key(a) == payroll.payslip_key(_slip("2026-10", net_pay=1.0))


# ------------------------------------------------------------- the jobs


def test_decode_pdf_checks_what_arrives() -> None:
    pdf = b"%PDF-1.7 fake"
    assert advisor.decode_pdf(base64.b64encode(pdf).decode()) == pdf
    assert (
        advisor.decode_pdf("data:application/pdf;base64," + base64.b64encode(pdf).decode()) == pdf
    )
    for bad in ("", "not base64!!", base64.b64encode(b"PK zip").decode()):
        with pytest.raises(advisor.AdvisorError):
            advisor.decode_pdf(bad)


class _FakeStore:
    def __init__(self) -> None:
        self.data: Any = None

    async def async_load(self) -> Any:
        return self.data

    async def async_save(self, data: Any) -> None:
        self.data = data


@pytest.fixture
def fake_claude(hass, monkeypatch):
    store = _FakeStore()
    hass.data.setdefault(advisor.DOMAIN, {})[advisor._STORE] = store
    calls: list[dict[str, Any]] = []
    answers: list[dict[str, Any]] = []

    async def ask(_hass, *, system, content, schema, effort):
        calls.append({"system": system, "content": content, "schema": schema, "effort": effort})
        return answers.pop(0), {
            "model": "claude-opus-5-5",
            "request_id": "req_x",
            "input_tokens": 10,
            "output_tokens": 5,
        }

    monkeypatch.setattr(advisor, "_ask", ask)
    return store, calls, answers


async def test_read_payslip_replaces_the_same_month(hass, fake_claude) -> None:
    store, calls, answers = fake_claude
    answers += [_slip("2026-10", net_pay=1800.0), _slip("2026-10", net_pay=1850.0)]
    for _ in range(2):
        advisor.start_read(hass, "payslip", "ottobre.pdf", b"%PDF fake")
        await hass.async_block_till_done()
    slips = store.data["payslips"]
    assert len(slips) == 1 and slips[0]["net_pay"] == 1850.0 and slips[0]["file"] == "ottobre.pdf"
    doc = calls[0]["content"][0]
    assert doc["type"] == "document" and doc["source"]["media_type"] == "application/pdf"
    assert calls[0]["schema"] is payroll.PAYSLIP_SCHEMA
    assert all(j["state"] == "done" for j in advisor.jobs_view(hass, "read_"))


async def test_inps_statement_fills_the_birth_date(hass, fake_claude) -> None:
    store, _calls, answers = fake_claude
    answers.append(
        {
            "birth_date": "1990-05-12",
            "statement_date": "2026-10-01",
            "rows": [],
            "total_weeks": 600,
            "notes": None,
        }
    )
    advisor.start_read(hass, "inps", "estratto.pdf", b"%PDF fake")
    await hass.async_block_till_done()
    assert store.data["profile"]["birth_date"] == "1990-05-12"
    assert store.data["inps"]["total_weeks"] == 600


async def test_unreadable_payslip_ends_in_error(hass, fake_claude) -> None:
    store, _calls, answers = fake_claude
    answers.append(_slip("boh"))
    advisor.start_read(hass, "payslip", "x.pdf", b"%PDF fake")
    await hass.async_block_till_done()
    job = advisor.jobs_view(hass, "read_")[0]
    assert job["state"] == "error" and job["error"] == "unreadable_payslip"
    assert store.data is None  # nothing saved


async def test_analysis_adds_the_estimates_and_keeps_the_last(hass, fake_claude) -> None:
    store, calls, answers = fake_claude
    store.data = {"payslips": _year_of_slips(), "profile": {"birth_date": "1990-05-12"}}
    answers.append({"summary": "ok", "sections": [], "caveats": []})
    advisor.start_analysis(hass, {"cicli": [{"entrate": 1676}]})
    await hass.async_block_till_done()
    sent = calls[0]["content"][0]["text"]
    assert '"dalla_card"' in sent and '"stime_da_cedolini"' in sent and '"pension"' in sent
    assert calls[0]["effort"] == "high" and calls[0]["schema"] is advisor.ANALYSIS_SCHEMA
    assert store.data["analysis"]["result"]["summary"] == "ok"
    assert store.data["analysis"]["model"] == "claude-opus-5-5"


def test_analysis_context_size_is_capped(hass) -> None:
    with pytest.raises(advisor.AdvisorError):
        advisor.start_analysis(hass, {"x": "y" * (advisor.MAX_CONTEXT_CHARS + 1)})


# ------------------------------------------------------- card statements


def _statement(**kw: Any) -> dict[str, Any]:
    base = {
        "issuer": "Nexi",
        "card_last4": "9999",
        "statement_date": "2026-09-30",
        "period_from": "2026-09-01",
        "period_to": "2026-09-30",
        "total_due": 61.5,
        "debit_date": "2026-10-15",
        "transactions": [
            {
                "date": "2026-09-03",
                "booking_date": None,
                "description": "NETFLIX.COM",
                "merchant": "Netflix",
                "city": None,
                "amount": 13.99,
                "original_currency": None,
                "original_amount": None,
            },
            {
                "date": "2026-09-12",
                "booking_date": "2026-09-14",
                "description": "ESSO ROMA",
                "merchant": "Esso",
                "city": "Roma",
                "amount": 50.0,
                "original_currency": None,
                "original_amount": None,
            },
            {
                "date": "2026-09-20",
                "booking_date": None,
                "description": "STORNO AMAZON",
                "merchant": "Amazon",
                "city": None,
                "amount": -2.49,
                "original_currency": None,
                "original_amount": None,
            },
            {
                "date": "boh",
                "booking_date": None,
                "description": "riga rotta",
                "merchant": None,
                "city": None,
                "amount": 1.0,
                "original_currency": None,
                "original_amount": None,
            },
        ],
    }
    base.update(kw)
    return base


def test_card_statement_is_checked_against_its_total() -> None:
    s = advisor.clean_card_statement(_statement())
    assert [t["date"] for t in s["transactions"]] == ["2026-09-03", "2026-09-12", "2026-09-20"]
    assert s["rows_total"] == 61.5 and s["difference"] == 0.0
    off = advisor.clean_card_statement(_statement(total_due=70.0))
    assert off["difference"] == 8.5
    with pytest.raises(advisor.AdvisorError):
        advisor.clean_card_statement(_statement(statement_date="settembre"))


async def test_card_statement_reupload_replaces(hass, fake_claude) -> None:
    store, calls, answers = fake_claude
    answers += [
        _statement(),
        _statement(total_due=61.5, issuer="Nexi S.p.A."),
        _statement(statement_date="2026-08-31"),
    ]
    for _ in range(3):
        advisor.start_read(hass, "card", "estratto.pdf", b"%PDF fake")
        await hass.async_block_till_done()
    cards = store.data["card_statements"]
    assert [c["statement_date"] for c in cards] == ["2026-08-31", "2026-09-30"]
    assert cards[1]["issuer"] == "Nexi S.p.A."
    assert calls[0]["schema"] is advisor.CARD_SCHEMA
    assert advisor.jobs_view(hass, "read_card")[0]["state"] == "done"
