import logging
from collections.abc import Callable

from vector_core.domain import ValidationError

INVALID_CREDENTIALS = "invalid credentials"
LEASE_EXPIRED_ERROR = "lease expired"

logger = logging.getLogger(__name__)


def clean_name(name: str, what: str = "project name") -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ValidationError(f"{what} must not be empty")
    return cleaned


class TransactionHooks:
    """Side effects outside the database that must follow the outcome of the surrounding transaction.

    `after_commit` actions run only once the transaction is committed (e.g. deleting blobs);
    `after_rollback` actions undo external writes made during a transaction that was rolled back.
    With `immediate=True` (no transaction, in-memory repositories) commit actions run at once
    and rollback actions are dropped.
    """

    def __init__(self, immediate: bool = False) -> None:
        self._immediate = immediate
        self._on_commit: list[Callable[[], None]] = []
        self._on_rollback: list[Callable[[], None]] = []

    def after_commit(self, action: Callable[[], None]) -> None:
        if self._immediate:
            action()
            return
        self._on_commit.append(action)

    def after_rollback(self, action: Callable[[], None]) -> None:
        if not self._immediate:
            self._on_rollback.append(action)

    def commit(self) -> None:
        actions, self._on_commit, self._on_rollback = self._on_commit, [], []
        self._run(actions, "after-commit")

    def rollback(self) -> None:
        actions, self._on_commit, self._on_rollback = self._on_rollback, [], []
        self._run(actions, "after-rollback")

    @staticmethod
    def _run(actions: list[Callable[[], None]], phase: str) -> None:
        for action in actions:
            try:
                action()
            except Exception:
                logger.exception("%s action failed", phase)
