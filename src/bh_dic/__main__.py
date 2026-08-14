"""Module entry point for ``python -m bh_dic``."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from typing import Any, cast


def main() -> None:
    """Load the CLI lazily so importing :mod:`bh_dic` has no side effects."""

    cli = import_module("bh_dic.cli")
    target: Any = getattr(cli, "main", None) or getattr(cli, "app", None)
    if not callable(target):
        raise SystemExit("BH-DiC CLI is unavailable: bh_dic.cli exposes neither main nor app")
    cast(Callable[[], object], target)()


if __name__ == "__main__":  # pragma: no cover - exercised through the installed entry point
    main()
