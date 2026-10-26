"""The self-service portal, mounted at /portal."""

from agent_gateway.adapters.inbound.portal.app import PortalDeps, create_portal_app

__all__ = ["PortalDeps", "create_portal_app"]
