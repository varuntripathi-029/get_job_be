"""Application-level exceptions, mapped to HTTP responses in main.py."""


class AppError(Exception):
    """Base class for expected, user-facing failures."""

    status_code: int = 400
    code: str = "app_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"


class AuthenticationError(AppError):
    status_code = 401
    code = "authentication_error"


class AuthorizationError(AppError):
    status_code = 403
    code = "authorization_error"


class SSRFError(ValidationError):
    """A submitted URL resolved somewhere we refuse to fetch."""

    code = "ssrf_blocked"


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"


class FetchError(AppError):
    """A source could not be fetched. Retryable."""

    status_code = 502
    code = "fetch_error"


class ExtractionError(AppError):
    """The LLM pipeline failed for one piece of content. Non-fatal."""

    status_code = 502
    code = "extraction_error"
