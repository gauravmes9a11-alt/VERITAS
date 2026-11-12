"""Statutory gratuity calculation logic grounded in:
- Payment of Gratuity Act, 1972 (Section 4(1), 4(2), 4(3))
- Code on Social Security, 2020 (Fixed-term contract proviso)
"""
from datetime import date
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class GratuityCalcRequest(BaseModel):
    basic_salary: float = Field(..., ge=0, description="Monthly basic salary in INR")
    dearness_allowance: float = Field(0.0, ge=0, description="Monthly DA in INR")
    date_of_joining: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    last_working_date: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    employment_type: str = Field("permanent", max_length=32)
    salary_type: str = Field("monthly", pattern=r"^(monthly|daily)$")


class GratuityCalcResponse(BaseModel):
    eligible_wage: float
    total_days: int
    total_years: float
    completed_years: int
    remaining_months: float
    effective_years: int
    formula: str
    raw_amount: float
    statutory_cap: float
    gratuity_payable: float
    is_eligible: bool
    eligibility_reason: str
    provisos: List[str]


def compute_gratuity(req: GratuityCalcRequest) -> GratuityCalcResponse:
    eligible_wage = float(req.basic_salary) + float(req.dearness_allowance)
    doj = date.fromisoformat(req.date_of_joining)
    lwd = date.fromisoformat(req.last_working_date) if req.last_working_date else date.today()

    total_days = max((lwd - doj).days, 0)
    total_years = total_days / 365.25
    completed_years = int(total_years)
    remaining_months = (total_years - completed_years) * 12

    # Section 4(2): 6-month rule
    effective_years = completed_years + (1 if remaining_months >= 6.0 else 0)

    # Statutory ceiling
    STATUTORY_CAP = 2000000.0  # INR 20 Lakhs under Section 4(3)

    emp_type = req.employment_type.lower()
    is_fixed_term = "fixed" in emp_type
    provisos = []

    # Eligibility determination
    if is_fixed_term:
        provisos.append(
            "Code on Social Security, 2020: Fixed-term contract employees are entitled to gratuity on a pro-rata basis for 1+ year service without the standard 5-year requirement."
        )
        is_eligible = total_years >= 1.0
        if is_eligible:
            eligibility_reason = f"Eligible under Fixed-term proviso ({total_years:.2f} yrs ≥ 1.0 yr statutory threshold)"
        else:
            eligibility_reason = f"Not eligible: Service tenure ({total_years:.2f} yrs) is under the 1-year fixed-term requirement"
    else:
        is_eligible = effective_years >= 5
        if is_eligible:
            eligibility_reason = f"Eligible under Section 4(1): Continuous service of {effective_years} effective years (≥ 5 years threshold)"
        else:
            eligibility_reason = f"Not eligible under Section 4(1): Requires at least 5 years of continuous service (current: {effective_years} effective years)"

    if req.salary_type == "daily":
        formula = f"15 × Daily Wage (₹{eligible_wage:,.0f}) × {effective_years} yrs"
        raw = 15.0 * eligible_wage * effective_years
    else:
        formula = f"(15 × ₹{eligible_wage:,.0f} × {effective_years} yrs) ÷ 26"
        raw = (15.0 * eligible_wage * effective_years) / 26.0

    payable = min(raw, STATUTORY_CAP) if is_eligible else 0.0

    if raw > STATUTORY_CAP and is_eligible:
        provisos.append(
            f"Statutory ceiling applied: Amount capped at INR {STATUTORY_CAP:,.0f} per Section 4(3) of the Payment of Gratuity Act, 1972."
        )

    return GratuityCalcResponse(
        eligible_wage=round(eligible_wage, 2),
        total_days=total_days,
        total_years=round(total_years, 2),
        completed_years=completed_years,
        remaining_months=round(remaining_months, 1),
        effective_years=effective_years,
        formula=formula,
        raw_amount=round(raw, 2),
        statutory_cap=STATUTORY_CAP,
        gratuity_payable=round(payable, 2),
        is_eligible=is_eligible,
        eligibility_reason=eligibility_reason,
        provisos=provisos,
    )
