"""Strict capture and validation for the DIC top-bar notification collection."""

from __future__ import annotations

from datetime import datetime

from pydantic import ValidationError

from bh_dic.dic.errors import DicUiChangedError
from bh_dic.dic.models import NotificationRecord
from bh_dic.dic.paginated_capture import PaginatedEndpointContract

NOTIFICATIONS_ENDPOINT = PaginatedEndpointContract(
    resource="notifications",
    endpoint_path="/backend_apiV2/notifications",
    paginator_path="/notifications",
    allowed_query_keys=frozenset(
        {
            "page",
            "per_page",
            "sort",
            "filter[0][field]",
            "filter[0][op]",
            "filter[0][value]",
        }
    ),
    employee_query_key=None,
    required_item_keys=frozenset(
        {
            "additional_text",
            "created_at",
            "employee",
            "id",
            "parameters",
            "read",
            "received",
            "recipient_id",
            "recipient_type",
            "resource_id",
            "sender",
            "sender_id",
            "sent",
            "text",
            "type",
            "updated_at",
        }
    ),
)


def _failure(reason: str) -> DicUiChangedError:
    return DicUiChangedError(f"notifications response validation failed: {reason}")


def _bounded_text(value: object, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise _failure("invalid notification text")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > 4_096:
        raise _failure("invalid notification text")
    return normalized


def _valid_discarded_parameters(value: object) -> bool:
    if isinstance(value, list):
        return len(value) <= 100
    if isinstance(value, dict):
        return len(value) <= 100 and all(
            isinstance(key, str) and 0 < len(key) <= 128 for key in value
        )
    return False


def notifications_from_items(
    items: tuple[dict[str, object], ...],
) -> tuple[NotificationRecord, ...]:
    """Project only fields needed by Discord and discard sender/employee payloads."""

    records: list[NotificationRecord] = []
    seen: set[int] = set()
    for item in items:
        notification_id = item["id"]
        read = item["read"]
        created_at = item["created_at"]
        notification_type = item["type"]
        if (
            type(notification_id) is not int
            or notification_id < 1
            or type(read) is not bool
            or not isinstance(created_at, str)
            or len(created_at) > 64
            or not isinstance(notification_type, dict)
            or not _valid_discarded_parameters(item["parameters"])
            or not (item["employee"] is None or isinstance(item["employee"], dict))
            or not (item["sender"] is None or isinstance(item["sender"], dict))
            or not isinstance(item["received"], bool)
            or not isinstance(item["sent"], bool)
        ):
            raise _failure("invalid notification schema")
        try:
            datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            raise _failure("invalid notification timestamp") from None
        raw_type = notification_type.get("id")
        if not isinstance(raw_type, (str, int)) or isinstance(raw_type, bool):
            raise _failure("invalid notification type")
        type_text = str(raw_type)
        if not type_text or len(type_text) > 128:
            raise _failure("invalid notification type")
        if notification_id in seen:
            raise _failure("duplicate notification identifier")
        seen.add(notification_id)
        text = _bounded_text(item["text"])
        if text is None:
            raise _failure("invalid notification text")
        try:
            records.append(
                NotificationRecord(
                    notification_id=notification_id,
                    notification_type=type_text,
                    text=text,
                    additional_text=_bounded_text(item["additional_text"], optional=True),
                    created_at=created_at,
                    read=read,
                )
            )
        except ValidationError:
            raise _failure("invalid notification projection") from None
    return tuple(records)


__all__ = ["NOTIFICATIONS_ENDPOINT", "notifications_from_items"]
