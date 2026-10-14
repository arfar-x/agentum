"""Errors that cross layers. Adapters map them to wire errors."""

from __future__ import annotations


class GatewayError(Exception):
    """Base class. ``status`` and ``code`` follow OpenAI's error object."""

    status = 500
    type = "server_error"
    code: str | None = None
    param: str | None = None

    def __init__(self, message: str, *, param: str | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if param is not None:
            self.param = param
        if code is not None:
            self.code = code


class InvalidRequest(GatewayError):
    status = 400
    type = "invalid_request_error"


class UnsupportedParameter(InvalidRequest):
    code = "unsupported_parameter"


class ModelNotAllowed(InvalidRequest):
    code = "model_not_found"
    param = "model"


class AuthenticationFailed(GatewayError):
    status = 401
    type = "invalid_request_error"
    code = "invalid_api_key"


class RateLimited(GatewayError):
    status = 429
    type = "rate_limit_error"
    code = "rate_limit_exceeded"


class UpstreamUnavailable(GatewayError):
    status = 502
    type = "server_error"
    code = "upstream_unavailable"


class LoopExhausted(GatewayError):
    status = 502
    type = "server_error"
    code = "agent_loop_exhausted"


class ConfigError(Exception):
    """A configuration problem; always names the file."""
