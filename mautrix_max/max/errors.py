"""Max API error types."""

from __future__ import annotations


class MaxAPIError(Exception):
    """Base exception for Max API errors."""

    def __init__(self, code: str, message: str, status: int = 0) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(f"Max API error {code} (HTTP {status}): {message}")


class AuthError(MaxAPIError):
    """Authentication/authorization error."""

    def __init__(self, message: str = "Authentication failed", status: int = 401) -> None:
        super().__init__("auth.failed", message, status)


class RateLimitError(MaxAPIError):
    """Rate limit exceeded."""

    def __init__(self, retry_after: int = 0) -> None:
        self.retry_after = retry_after
        super().__init__("rate_limited", f"Rate limited, retry after {retry_after}s", 429)


class NotFoundError(MaxAPIError):
    """Resource not found."""

    def __init__(self, resource: str = "resource") -> None:
        super().__init__("not_found", f"{resource} not found", 404)
