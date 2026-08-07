"""Application-level exceptions, mapped to HTTP responses in main.py.

Every one serialises to the same body:

    {"error": "NOT_FOUND", "message": "No company with slug 'acme'."}

so the frontend has one error shape to branch on. `error` is a stable machine
code; `message` is written for a human and may change.
"""


class AppError(Exception):
    """Base class for expected, user-facing failures."""

    status_code: int = 400
    error_code: str = "BAD_REQUEST"

    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if error_code:
            self.error_code = error_code
        if status_code:
            self.status_code = status_code

    @property
    def code(self) -> str:
        """Backwards-compatible alias for `error_code`."""
        return self.error_code

    def to_dict(self) -> dict[str, str]:
        return {"error": self.error_code, "message": self.message}


class NotFoundError(AppError):
    status_code = 404
    error_code = "NOT_FOUND"

    @classmethod
    def for_resource(cls, resource: str, identifier: object) -> "NotFoundError":
        """`NotFoundError.for_resource("Company", slug)` -> consistent phrasing."""
        return cls(f"{resource} '{identifier}' not found.")


class ConflictError(AppError):
    status_code = 409
    error_code = "CONFLICT"


class ValidationError(AppError):
    status_code = 422
    error_code = "VALIDATION_ERROR"


class AuthenticationError(AppError):
    status_code = 401
    error_code = "UNAUTHENTICATED"


class AuthorizationError(AppError):
    status_code = 403
    error_code = "FORBIDDEN"


# The spec calls this ForbiddenError; the codebase already raises
# AuthorizationError. Same class, both names.
ForbiddenError = AuthorizationError


class RateLimitedError(AppError):
    status_code = 429
    error_code = "RATE_LIMITED"


class SSRFError(ValidationError):
    """A submitted URL resolved somewhere we refuse to fetch."""

    error_code = "SSRF_BLOCKED"


class FetchError(AppError):
    """A source could not be fetched. Retryable."""

    status_code = 502
    error_code = "FETCH_ERROR"


class ExtractionError(AppError):
    """The LLM pipeline failed for one piece of content. Non-fatal."""

    status_code = 502
    error_code = "EXTRACTION_ERROR"
