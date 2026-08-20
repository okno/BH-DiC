"""Typed export artifacts; payload bytes are deliberately excluded from repr."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum


class ExportFormat(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"


@dataclass(frozen=True, slots=True)
class EmployeeExportRow:
    first_name: str | None
    last_name: str | None
    employee_id: str
    status: str
    contract_expiry: date | None
    contract_type: str | None
    monthly_net: Decimal | None = None


@dataclass(frozen=True, slots=True)
class GeneratedExport:
    filename: str
    media_type: str
    content: bytes = field(repr=False)
    record_count: int = 0
