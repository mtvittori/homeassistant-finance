"""Payslips, INPS statement and the estimates built on them (fork-only).

Pure functions, no Home Assistant: the data comes from payslips and the INPS
contribution statement read by Claude (see ``advisor.py``), the rules are the
Italian ones in force in 2026 and are written down here with their source, so
that a rule change is one constant to edit, not a hunt through the code.

Every amount is in euros of today: salary growth and revaluation are *real*
rates (net of inflation), so a pension "in 2058" reads as what it would buy
now. That is the only honest way to show a number thirty years out.

Sources (checked 30/10/2026):

* IRPEF 2026: 23% to 28.000, 33% to 50.000, 43% above (L. 199/2025, art. 1
  c. 3 - the second bracket went from 35% to 33%).
* Transformation coefficients 2025-2026: D.M. 436/2024 (Ministero del Lavoro),
  57 years 4,204% ... 67 years 5,608% ... 71 years 6,510%.
* Retirement age: 67 years + 1 month from 2027, + 3 months from 2028
  (L. 199/2025 reshaping the D.M. 19/12/2025 adjustment; INPS circ. 28/2026).
  Early retirement: 42 years 10 months of contributions (women 41y10m), plus
  the same 3 months by 2028.
* TFR (art. 2120 c.c.): yearly quota = pay / 13.5, minus the 0.5% extra IVS
  contribution; revalued each year by 1.5% + 75% of inflation, the
  revaluation taxed at 17% (substitute tax). Without a pension fund, in a
  company with 50+ employees, the TFR goes to the INPS Fondo Tesoreria under
  the same rules.
* IVS contribution for employees: 33% of the social-security taxable pay.
"""

from __future__ import annotations

from datetime import date
from typing import Any

IRPEF_BRACKETS_2026: tuple[tuple[float, float], ...] = (
    (28_000.0, 0.23),
    (50_000.0, 0.33),
    (float("inf"), 0.43),
)

# D.M. 436/2024, valid 2025-2026: age -> coefficient (fraction, not percent).
TRANSFORMATION_COEFFICIENTS: dict[int, float] = {
    57: 0.04204,
    58: 0.04308,
    59: 0.04419,
    60: 0.04536,
    61: 0.04661,
    62: 0.04795,
    63: 0.04936,
    64: 0.05088,
    65: 0.05250,
    66: 0.05423,
    67: 0.05608,
    68: 0.05808,
    69: 0.06024,
    70: 0.06258,
    71: 0.06510,
}

IVS_RATE = 0.33
TFR_DIVISOR = 13.5
TFR_EXTRA_IVS = 0.005
TFR_FIXED_REVAL = 0.015
TFR_INFLATION_SHARE = 0.75
TFR_REVAL_TAX = 0.17

# Months: 67y3m old-age, 42y10m (+3) early for men, one year less for women.
OLD_AGE_MONTHS_2028 = 67 * 12 + 3
EARLY_MONTHS_2028 = {"M": 42 * 12 + 10 + 3, "F": 41 * 12 + 10 + 3}
WEEKS_PER_YEAR = 52

PAYSLIP_KINDS = ("mensile", "tredicesima", "quattordicesima", "altro")

DEFAULT_PROFILE: dict[str, Any] = {
    "birth_date": None,  # "YYYY-MM-DD", from the INPS statement or typed
    "sex": "M",
    "retirement_age_months": OLD_AGE_MONTHS_2028,
    "real_salary_growth": 0.01,  # yearly, net of inflation; = revaluation, a neutral default
    "real_montante_reval": 0.01,  # yearly, net of inflation (GDP-linked)
    "inflation": 0.02,  # only for the TFR revaluation formula
}


# ---------------------------------------------------------------- schemas


def _num() -> dict[str, Any]:
    return {"type": ["number", "null"]}


def _str() -> dict[str, Any]:
    return {"type": ["string", "null"]}


PAYSLIP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "period": {"type": "string", "description": "Mese di competenza, YYYY-MM"},
        "kind": {"type": "string", "enum": list(PAYSLIP_KINDS)},
        "employer": _str(),
        "gross_pay": _num(),
        "net_pay": _num(),
        "social_taxable": _num(),
        "social_contributions": _num(),
        "tax_taxable": _num(),
        "irpef_net": _num(),
        "regional_surtax": _num(),
        "municipal_surtax": _num(),
        "tfr_month": _num(),
        "tfr_accrued": _num(),
        "tfr_destination": {
            "type": ["string", "null"],
            "enum": ["azienda", "fondo_tesoreria", "fondo_pensione", None],
        },
        "hire_date": _str(),
        "ytd_tax_taxable": _num(),
        "ytd_irpef": _num(),
        "ytd_social_taxable": _num(),
        "base_monthly_salary": _num(),
        "ccnl": _str(),
        "level": _str(),
        "notes": _str(),
    },
    "required": [
        "period",
        "kind",
        "employer",
        "gross_pay",
        "net_pay",
        "social_taxable",
        "social_contributions",
        "tax_taxable",
        "irpef_net",
        "regional_surtax",
        "municipal_surtax",
        "tfr_month",
        "tfr_accrued",
        "tfr_destination",
        "hire_date",
        "ytd_tax_taxable",
        "ytd_irpef",
        "ytd_social_taxable",
        "base_monthly_salary",
        "ccnl",
        "level",
        "notes",
    ],
    "additionalProperties": False,
}

INPS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "birth_date": _str(),
        "statement_date": _str(),
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "year": {"type": "integer"},
                    "employer": _str(),
                    "kind": _str(),
                    "weeks": _num(),
                    "taxable_pay": _num(),
                },
                "required": ["year", "employer", "kind", "weeks", "taxable_pay"],
                "additionalProperties": False,
            },
        },
        "total_weeks": _num(),
        "notes": _str(),
    },
    "required": ["birth_date", "statement_date", "rows", "total_weeks", "notes"],
    "additionalProperties": False,
}


# ------------------------------------------------------------------ taxes


def irpef(income: float) -> float:
    """Gross IRPEF on a yearly taxable income, 2026 brackets."""
    tax, lower = 0.0, 0.0
    for upper, rate in IRPEF_BRACKETS_2026:
        if income <= lower:
            break
        tax += (min(income, upper) - lower) * rate
        lower = upper
    return round(tax, 2)


def marginal_rate(income: float) -> float:
    for upper, rate in IRPEF_BRACKETS_2026:
        if income <= upper:
            return rate
    return IRPEF_BRACKETS_2026[-1][1]


def pensioner_deduction(income: float) -> float:
    """Detrazione per redditi da pensione (TUIR art. 13 c. 3, from 2022)."""
    if income <= 8_500:
        return 1_955.0
    if income <= 28_000:
        return 700 + 1_255 * (28_000 - income) / 19_500
    if income <= 50_000:
        return 700 * (50_000 - income) / 22_000
    return 0.0


# ---------------------------------------------------------------- helpers


def _f(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def _parse_date(raw: Any) -> date | None:
    try:
        return date.fromisoformat(str(raw)[:10]) if raw else None
    except ValueError:
        return None


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    day = min(d.day, 28)
    return date(year, month, day)


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month) - (1 if b.day < a.day else 0)


def coefficient(age_months: int) -> float:
    """Transformation coefficient for an age in months, interpolated by month.

    Below 57 there is no coefficient (no pension yet), above 71 the law uses
    the 71 one.
    """
    years, months = divmod(max(57 * 12, min(71 * 12, age_months)), 12)
    low = TRANSFORMATION_COEFFICIENTS[years]
    high = TRANSFORMATION_COEFFICIENTS.get(years + 1, low)
    return low + (high - low) * months / 12


def merge_profile(raw: Any) -> dict[str, Any]:
    prof = dict(DEFAULT_PROFILE)
    if isinstance(raw, dict):
        for k in DEFAULT_PROFILE:
            if raw.get(k) is not None:
                prof[k] = raw[k]
    return prof


def validate_profile(body: Any) -> dict[str, Any]:
    """Body of the profile update -> clean changes. Raises ValueError."""
    if not isinstance(body, dict):
        raise ValueError("body must be an object")
    out: dict[str, Any] = {}
    if "birth_date" in body:
        d = _parse_date(body["birth_date"])
        if body["birth_date"] and not d:
            raise ValueError("birth_date must be YYYY-MM-DD")
        out["birth_date"] = d.isoformat() if d else None
    if "sex" in body:
        if body["sex"] not in ("M", "F"):
            raise ValueError("sex must be M or F")
        out["sex"] = body["sex"]
    if "retirement_age_months" in body:
        v = body["retirement_age_months"]
        if not isinstance(v, int) or not 57 * 12 <= v <= 71 * 12:
            raise ValueError("retirement_age_months must be 684-852")
        out["retirement_age_months"] = v
    for key, lo, hi in (
        ("real_salary_growth", -0.05, 0.05),
        ("real_montante_reval", -0.05, 0.05),
        ("inflation", 0.0, 0.15),
    ):
        if key in body:
            v = _f(body[key])
            if v is None or not lo <= v <= hi:
                raise ValueError(f"{key} must be between {lo} and {hi}")
            out[key] = v
    return out


def sorted_payslips(payslips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {k: i for i, k in enumerate(PAYSLIP_KINDS)}
    return sorted(payslips, key=lambda p: (str(p.get("period") or ""), order.get(p.get("kind"), 9)))


def payslip_key(p: dict[str, Any]) -> str:
    """Same month and kind = same payslip: a re-upload replaces it."""
    return f"{p.get('period')}|{p.get('kind')}"


# --------------------------------------------------------- annual base


def annual_social_base(
    payslips: list[dict[str, Any]], inps: dict[str, Any] | None, today: date
) -> dict[str, Any]:
    """Yearly social-security taxable pay, and where the number comes from.

    The last twelve months of payslips win when they cover at least ten
    months (they include the 13th/14th month); otherwise the last full year
    of the INPS statement; otherwise the average month x 13.
    """
    start = _add_months(date(today.year, today.month, 1), -12)
    recent = [p for p in payslips if (d := _parse_date(f"{p.get('period')}-01")) and d >= start]
    months = {p.get("period") for p in recent if p.get("kind") == "mensile"}
    total = sum(_f(p.get("social_taxable")) or 0 for p in recent)
    if len(months) >= 10 and total > 0:
        return {"amount": round(total * 12 / len(months), 2), "source": "cedolini_12_mesi"}
    rows = (inps or {}).get("rows") or []
    years = [r for r in rows if r.get("year") == today.year - 1 and _f(r.get("taxable_pay"))]
    if years:
        return {
            "amount": round(sum(_f(r["taxable_pay"]) for r in years), 2),
            "source": "estratto_inps",
        }
    monthly = [
        _f(p.get("social_taxable"))
        for p in payslips
        if p.get("kind") == "mensile" and _f(p.get("social_taxable"))
    ]
    if monthly:
        return {"amount": round(sum(monthly) / len(monthly) * 13, 2), "source": "media_mensile_x13"}
    return {"amount": 0.0, "source": None}


# ------------------------------------------------------------------ TFR


def tfr_estimate(
    payslips: list[dict[str, Any]],
    inps: dict[str, Any] | None,
    profile: dict[str, Any],
    today: date,
) -> dict[str, Any]:
    """TFR now, at retirement, and what is left after the separate taxation."""
    prof = merge_profile(profile)
    ps = sorted_payslips(payslips)
    base = annual_social_base(ps, inps, today)
    if not ps and not base["amount"]:
        return {"available": False}

    # Yearly quota: the payslips' own TFR lines when there are enough of them.
    start = _add_months(date(today.year, today.month, 1), -12)
    last12 = [p for p in ps if (d := _parse_date(f"{p.get('period')}-01")) and d >= start]
    months = {p.get("period") for p in last12 if p.get("kind") == "mensile"}
    quotas = sum(_f(p.get("tfr_month")) or 0 for p in last12)
    if len(months) >= 6 and quotas > 0:
        quota = quotas * 12 / len(months)
        quota_source = "cedolini"
    else:
        quota = base["amount"] / TFR_DIVISOR - TFR_EXTRA_IVS * base["amount"]
        quota_source = "formula"

    hire = next(
        (_parse_date(p.get("hire_date")) for p in reversed(ps) if _parse_date(p.get("hire_date"))),
        None,
    )
    accrued_now = next(
        (_f(p.get("tfr_accrued")) for p in reversed(ps) if _f(p.get("tfr_accrued"))), None
    )
    accrued_source = "cedolino"
    years_service = max(0.0, _months_between(hire, today) / 12) if hire else None
    if accrued_now is None:
        if years_service is None:
            return {"available": False, "reason": "data_assunzione_mancante"}
        accrued_now = quota * years_service
        accrued_source = "stima_da_assunzione"

    destination = next(
        (p.get("tfr_destination") for p in reversed(ps) if p.get("tfr_destination")), None
    )
    infl = float(prof["inflation"])
    reval_nominal = TFR_FIXED_REVAL + TFR_INFLATION_SHARE * infl
    reval_real_net = reval_nominal * (1 - TFR_REVAL_TAX) - infl

    def taxed(gross: float, quotas_sum: float, years: float | None) -> dict[str, float]:
        # Separate taxation: average IRPEF rate of the "reference income"
        # (quotas x 12 / years of service). Revaluations already paid 17%.
        yrs = max(1.0, years or 1.0)
        ref = quotas_sum * 12 / yrs
        rate = irpef(ref) / ref if ref > 0 else 0.0
        tax = min(gross, quotas_sum) * rate
        return {
            "gross": round(gross, 2),
            "tax": round(tax, 2),
            "net": round(gross - tax, 2),
            "rate": round(rate, 4),
        }

    out: dict[str, Any] = {
        "available": True,
        "destination": destination,
        "yearly_quota": round(quota, 2),
        "quota_source": quota_source,
        "hire_date": hire.isoformat() if hire else None,
        "years_service": round(years_service, 1) if years_service is not None else None,
        "now": taxed(accrued_now, accrued_now, years_service),
        "accrued_source": accrued_source,
        "reval_nominal": round(reval_nominal, 4),
        "reval_real_net": round(reval_real_net, 4),
        "at_retirement": None,
    }

    birth = _parse_date(prof.get("birth_date"))
    if birth:
        retire = _add_months(birth, int(prof["retirement_age_months"]))
        years_left = max(0, retire.year - today.year)
        bal, quotas_sum, q = accrued_now, accrued_now, quota
        for _ in range(years_left):
            bal = bal * (1 + reval_real_net) + q
            quotas_sum += q
            q *= 1 + float(prof["real_salary_growth"])
        total_years = (years_service or 0) + years_left
        out["at_retirement"] = {"date": retire.isoformat(), **taxed(bal, quotas_sum, total_years)}
    return out


# --------------------------------------------------------------- pension


def pension_estimate(
    payslips: list[dict[str, Any]],
    inps: dict[str, Any] | None,
    profile: dict[str, Any],
    today: date,
) -> dict[str, Any]:
    """Contributory pension: montante x transformation coefficient.

    Past years come from the INPS statement, years it does not cover yet from
    the payslips, the future from the yearly base growing at the real salary
    growth. The montante is revalued at a real rate (the legal one follows
    nominal GDP; real is what keeps the result in today's euros).
    """
    prof = merge_profile(profile)
    birth = _parse_date(prof.get("birth_date") or (inps or {}).get("birth_date"))
    if not birth:
        return {"available": False, "reason": "data_di_nascita_mancante"}
    ps = sorted_payslips(payslips)
    rows = (inps or {}).get("rows") or []
    r = float(prof["real_montante_reval"])
    g = float(prof["real_salary_growth"])

    by_year: dict[int, float] = {}
    weeks_by_year: dict[int, float] = {}
    for row in rows:
        y = row.get("year")
        if not isinstance(y, int):
            continue
        by_year[y] = by_year.get(y, 0.0) + (_f(row.get("taxable_pay")) or 0.0)
        weeks_by_year[y] = weeks_by_year.get(y, 0.0) + (_f(row.get("weeks")) or 0.0)
    inps_years = set(by_year)
    for p in ps:
        y = int(str(p.get("period"))[:4]) if str(p.get("period") or "")[:4].isdigit() else None
        if y is None or y in inps_years:
            continue
        by_year[y] = by_year.get(y, 0.0) + (_f(p.get("social_taxable")) or 0.0)
        if p.get("kind") == "mensile":
            weeks_by_year[y] = weeks_by_year.get(y, 0.0) + WEEKS_PER_YEAR / 12

    base = annual_social_base(ps, inps, today)
    # The current year: what is already paid plus the rest of the year at the base.
    paid_this_year = by_year.get(today.year, 0.0)
    rest_share = (12 - today.month) / 12
    by_year[today.year] = paid_this_year + base["amount"] * rest_share
    weeks_by_year[today.year] = (
        max(weeks_by_year.get(today.year, 0.0), WEEKS_PER_YEAR * today.month / 12)
        + WEEKS_PER_YEAR * rest_share
    )

    montante_now = sum(
        IVS_RATE * pay * (1 + r) ** (today.year - y)
        for y, pay in by_year.items()
        if y <= today.year
    )
    weeks_now = sum(w for y, w in weeks_by_year.items() if y <= today.year)

    def project(retire: date) -> dict[str, Any]:
        mont, pay = montante_now, base["amount"]
        for _ in range(today.year + 1, retire.year + 1):
            pay *= 1 + g
            mont = mont * (1 + r) + IVS_RATE * pay
        age = _months_between(birth, retire)
        coeff = coefficient(age)
        gross = mont * coeff
        net = gross - max(0.0, irpef(gross) - pensioner_deduction(gross))
        return {
            "date": retire.isoformat(),
            "age_months": age,
            "montante": round(mont, 2),
            "coefficient": round(coeff, 5),
            "gross_yearly": round(gross, 2),
            "gross_monthly": round(gross / 13, 2),
            "net_monthly": round(net / 13, 2),
            "replacement_rate": round(gross / pay, 3) if pay else None,
            "last_pay": round(pay, 2),
        }

    old_age = project(_add_months(birth, int(prof["retirement_age_months"])))
    early_weeks = EARLY_MONTHS_2028[prof.get("sex", "M")] * WEEKS_PER_YEAR / 12
    missing = max(0.0, early_weeks - weeks_now)
    early_date = _add_months(today, round(missing / WEEKS_PER_YEAR * 12))
    early = project(early_date) if early_date < date.fromisoformat(old_age["date"]) else None

    return {
        "available": True,
        "birth_date": birth.isoformat(),
        "base": base,
        "years_from_inps": sorted(inps_years),
        "montante_now": round(montante_now, 2),
        "weeks_now": round(weeks_now),
        "years_now": round(weeks_now / WEEKS_PER_YEAR, 1),
        "old_age": old_age,
        "early": early,
        "assumptions": {
            "real_salary_growth": g,
            "real_montante_reval": r,
            "coefficients": "D.M. 436/2024 (2025-2026)",
            "irpef": "2026",
        },
    }


# ------------------------------------------------------------------ year


def tax_year(payslips: list[dict[str, Any]], today: date) -> dict[str, Any]:
    """This year's taxes so far, from the payslips, and where they are heading."""
    ps = [
        p
        for p in sorted_payslips(payslips)
        if str(p.get("period") or "").startswith(str(today.year))
    ]
    if not ps:
        return {"available": False}
    last = ps[-1]
    months = {p.get("period") for p in ps if p.get("kind") == "mensile"}
    taxable = _f(last.get("ytd_tax_taxable")) or sum(_f(p.get("tax_taxable")) or 0 for p in ps)
    irpef_paid = _f(last.get("ytd_irpef")) or sum(_f(p.get("irpef_net")) or 0 for p in ps)
    surtaxes = sum(
        (_f(p.get("regional_surtax")) or 0) + (_f(p.get("municipal_surtax")) or 0) for p in ps
    )
    contributions = sum(_f(p.get("social_contributions")) or 0 for p in ps)
    n = max(1, len(months))
    projected = taxable / n * 12
    return {
        "available": True,
        "year": today.year,
        "months": n,
        "taxable_ytd": round(taxable, 2),
        "irpef_ytd": round(irpef_paid, 2),
        "surtaxes_ytd": round(surtaxes, 2),
        "contributions_ytd": round(contributions, 2),
        "average_rate": round(irpef_paid / taxable, 4) if taxable else None,
        "projected_taxable": round(projected, 2),
        "marginal_rate": marginal_rate(projected),
        "gross_irpef_projected": irpef(projected),
    }


def estimates(
    payslips: list[dict[str, Any]],
    inps: dict[str, Any] | None,
    profile: dict[str, Any],
    today: date,
) -> dict[str, Any]:
    return {
        "tfr": tfr_estimate(payslips, inps, profile, today),
        "pension": pension_estimate(payslips, inps, profile, today),
        "taxes": tax_year(payslips, today),
    }
