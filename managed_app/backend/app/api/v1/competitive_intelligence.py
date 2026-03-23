"""Stable API for the Competitive Intelligence desktop app.

The evolved frontend currently expects a small set of endpoints under
`/api/v1/competitive-intelligence/*`. Generated backend modules have drifted
and may fail to import, which leaves the app mounted in the desktop but unable
to load. This module provides a minimal, framework-owned contract so the app
can always render a clean empty state until real company persistence lands.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/competitive-intelligence", tags=["competitive-intelligence"])


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CompanySearchFilters(BaseModel):
    industry: str | None = None
    country: str | None = None
    min_employees: int | None = None
    max_employees: int | None = None
    min_revenue: float | None = None
    max_revenue: float | None = None
    is_verified: bool | None = None
    search_query: str | None = None


class CompanySearchRequest(BaseModel):
    filters: CompanySearchFilters | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=1000)


class CompanySummary(BaseModel):
    id: str
    name: str
    domain: str | None = None
    industry: str | None = None
    country: str | None = None
    description: str | None = None
    employee_count: int | None = None
    revenue: float | None = None
    founded_year: int | None = None
    website: str | None = None
    linkedin_url: str | None = None
    is_verified: bool = False
    confidence_score: float | None = None
    last_updated: str | None = None
    created_at: str | None = None
    metadata: dict[str, Any] | None = None


class CompanySearchResponse(BaseModel):
    companies: list[CompanySummary]
    total: int
    page: int
    page_size: int


class CompanyStatistics(BaseModel):
    total_companies: int
    verified_companies: int
    industries_count: int
    countries_count: int
    avg_confidence_score: float
    recent_discoveries: int


_EMPTY_RESULTS: list[CompanySummary] = []


@router.get("/statistics", response_model=CompanyStatistics)
async def get_statistics() -> CompanyStatistics:
    """Return an empty but valid statistics payload until company storage exists."""
    return CompanyStatistics(
        total_companies=0,
        verified_companies=0,
        industries_count=0,
        countries_count=0,
        avg_confidence_score=0.0,
        recent_discoveries=0,
    )


@router.post("/companies/search", response_model=CompanySearchResponse)
async def search_companies(payload: CompanySearchRequest) -> CompanySearchResponse:
    """Return a stable empty result set instead of 404 when no company backend exists."""
    return CompanySearchResponse(
        companies=_EMPTY_RESULTS,
        total=0,
        page=payload.page,
        page_size=payload.page_size,
    )


@router.get("/companies/{company_id}/full", response_model=CompanySummary)
async def get_company_full(company_id: str) -> CompanySummary:
    """There is no persisted company dataset yet, so detail lookups are unavailable."""
    raise HTTPException(status_code=404, detail=f"Company {company_id} not found")


@router.get("/companies/{company_id}", response_model=CompanySummary)
async def get_company(company_id: str) -> CompanySummary:
    """Keep the compact company route aligned with the detail route contract."""
    raise HTTPException(status_code=404, detail=f"Company {company_id} not found")
