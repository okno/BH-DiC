"""High-level audit verification helper used by CLI and operations scripts."""

from __future__ import annotations

from bh_dic.audit.models import AuditVerificationResult
from bh_dic.audit.service import AuditService
from bh_dic.database.engine import Database


class AuditVerifier:
    def __init__(self, database: Database, hmac_key: bytes | str) -> None:
        self.service = AuditService(database, hmac_key)

    async def verify(self) -> AuditVerificationResult:
        return await self.service.verify()

    async def verify_or_raise(self) -> AuditVerificationResult:
        return await self.service.verify_or_raise()


async def verify_audit_chain(database: Database, hmac_key: bytes | str) -> AuditVerificationResult:
    return await AuditVerifier(database, hmac_key).verify()
