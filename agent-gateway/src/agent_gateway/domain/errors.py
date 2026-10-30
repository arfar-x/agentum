"""Errors that cross layers. Adapters map them to wire errors."""

from __future__ import annotations

from agent_gateway.domain.identity import NoUpstreamKey


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


class ModelNotFound(GatewayError):
    """No such model, or an agent the caller may not use: the two are indistinguishable on purpose."""

    status = 404
    type = "invalid_request_error"
    code = "model_not_found"
    param = "model"

    def __init__(self, model: str) -> None:
        super().__init__(f"The model `{model}` does not exist or you do not have access to it.")


class AuthenticationFailed(GatewayError):
    status = 401
    type = "invalid_request_error"
    code = "invalid_api_key"


class IdentityUnavailable(GatewayError):
    status = 503
    type = "server_error"
    code = "identity_unavailable"
    retry_after = 30


class PermissionDenied(GatewayError):
    """The caller is known but its key does not allow this."""

    status = 403
    type = "invalid_request_error"
    code = "permission_denied"


class InvalidUrl(GatewayError):
    """A pass-through path that could leave the upstream's API prefix."""

    status = 404
    type = "invalid_request_error"
    code = "invalid_url"


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


class UpstreamRejected(GatewayError):
    """The upstream refused the request; its status and error body are passed on."""

    def __init__(self, status: int, body: dict | None, message: str = "") -> None:
        err = (body or {}).get("error") if isinstance(body, dict) else None
        text = (err.get("message") if isinstance(err, dict) else None) or message or "upstream error"
        super().__init__(text)
        self.status = status
        self.body = body
        if isinstance(err, dict):
            self.type = err.get("type") or self.type
            self.code = err.get("code")
            self.param = err.get("param")


class UpstreamKeyRequired(GatewayError, NoUpstreamKey):
    """The caller has registered no model key."""

    status = 403
    type = "invalid_request_error"
    code = "upstream_key_required"


class UpstreamKeyRejected(GatewayError):
    """The model provider refused the key a request was made with."""

    status = 502
    type = "server_error"
    code = "upstream_key_rejected"
