class SerpApiError(Exception):
    """Base class. Messages are safe to show users (never contain the API key)."""

    retryable = False


class SerpApiConfigError(SerpApiError):
    """No API key configured and mock mode is off."""


class SerpApiAuthError(SerpApiError):
    """Invalid or unauthorized API key."""


class SerpApiQuotaError(SerpApiError):
    """Account has run out of searches. Retrying will not help."""


class SerpApiRateLimitError(SerpApiError):
    retryable = True


class SerpApiTimeoutError(SerpApiError):
    retryable = True


class SerpApiUpstreamError(SerpApiError):
    """5xx, network failure, malformed response, or an unrecognized API error."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class SerpApiFixtureNotFound(SerpApiError):
    """Mock mode is on and no fixture matches the request."""
