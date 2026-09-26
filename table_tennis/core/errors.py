"""Domain errors mapped 1:1 to ErrorResponse + HTTP status codes."""

from __future__ import annotations

from typing import Any, Optional

from table_tennis.contracts import ErrorResponse


class RefereeError(Exception):
    http_status = 409

    def __init__(
        self,
        code: str,
        message: str,
        *,
        current_revision: Optional[int] = None,
        details: Optional[dict[str, Any]] = None,
        http_status: Optional[int] = None,
    ):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.current_revision = current_revision
        self.details = details
        if http_status is not None:
            self.http_status = http_status

    def to_response(self) -> ErrorResponse:
        return ErrorResponse(
            code=self.code,
            message=self.message,
            current_revision=self.current_revision,
            details=self.details,
        )


class ConflictError(RefereeError):
    """409: stale revision, wrong state, stale rally/proposal/calibration, idempotency mismatch."""

    http_status = 409


class UnsupportedError(RefereeError):
    """422: well-formed but explicitly unsupported in v1 (best_of != 1, automatic scoring)."""

    http_status = 422


class NotFoundError(RefereeError):
    http_status = 404


class ForbiddenError(RefereeError):
    http_status = 403


class UnauthorizedError(RefereeError):
    http_status = 401
