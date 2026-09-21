"""Local, fail-closed document extraction for employee onboarding drafts."""

from __future__ import annotations

from bh_dic.onboarding.drafts import (
    OnboardingDraftError,
    OnboardingDraftKey,
    OnboardingDraftStore,
    StoredOnboardingDraft,
)
from bh_dic.onboarding.ocr import (
    DEFAULT_REQUIRED_FIELDS,
    DocumentDraft,
    FieldConflict,
    FieldEvidence,
    LocalTesseractOcr,
    OcrError,
    OcrInputError,
    OcrOutputError,
    OcrText,
    OcrTimeoutError,
    OcrUnavailableError,
    OcrUnsupportedFormatError,
    merge_ocr_texts,
    parse_italian_document,
)

__all__ = [
    "DEFAULT_REQUIRED_FIELDS",
    "DocumentDraft",
    "FieldConflict",
    "FieldEvidence",
    "LocalTesseractOcr",
    "OcrError",
    "OcrInputError",
    "OcrOutputError",
    "OcrText",
    "OcrTimeoutError",
    "OcrUnavailableError",
    "OcrUnsupportedFormatError",
    "OnboardingDraftError",
    "OnboardingDraftKey",
    "OnboardingDraftStore",
    "StoredOnboardingDraft",
    "merge_ocr_texts",
    "parse_italian_document",
]
