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
    NotificationsPage,
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
    "NotificationsPage",
    "PageLike",
    "TimestampEmployeesPage",
    "VerifiedUploadPayload",
]
