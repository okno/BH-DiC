"""Route-scoped DIC page objects."""

from bh_dic.dic.pages.base import (
    BaseDicPage,
    LocatorLike,
    PageLike,
    VerifiedUploadPayload,
)
from bh_dic.dic.pages.routes import (
    EmployeeBalancePage,
    EmployeeContractsPage,
    EmployeeDocumentsPage,
    EmployeeMaturationsPage,
    EmployeePayrollsPage,
    EmployeeRolesPage,
    EmployeesListPage,
    EmployeeSummaryPage,
    LoginPage,
    TimestampEmployeesPage,
)

__all__ = [
    "BaseDicPage",
    "EmployeeBalancePage",
    "EmployeeContractsPage",
    "EmployeeDocumentsPage",
    "EmployeeMaturationsPage",
    "EmployeePayrollsPage",
    "EmployeeRolesPage",
    "EmployeeSummaryPage",
    "EmployeesListPage",
    "LocatorLike",
    "LoginPage",
    "PageLike",
    "TimestampEmployeesPage",
    "VerifiedUploadPayload",
]
