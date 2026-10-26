"""The directory read through Keycloak's Admin REST API: read requests only, all or nothing."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

import httpx

from agent_gateway.adapters.outbound.identity.keycloak.settings import KeycloakSettings
from agent_gateway.adapters.outbound.identity.keycloak.verifier import SERVICE_ACCOUNT_PREFIX
from agent_gateway.application.identity_ports import Account
from agent_gateway.domain.identity import clean_groups, clean_roles

PAGE = 100
CONCURRENCY = 8


class DirectoryUnavailable(RuntimeError):
    """A read failed. The message names the request, never a credential."""


class KeycloakDirectory:
    """Implements ``DirectorySource``."""

    def __init__(self, settings: KeycloakSettings, http: httpx.AsyncClient,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._s, self._http, self._clock = settings, http, clock
        self._token: str | None = None
        self._token_until = 0.0
        self._client_uuid: str | None = None

    # ---- transport

    async def _service_token(self) -> str:
        if self._token and self._clock() < self._token_until:
            return self._token
        try:
            response = await self._http.post(self._s.token_url, data={
                "grant_type": "client_credentials", "client_id": self._s.directory_client_id,
                "client_secret": self._s.directory_client_secret})
            if response.status_code != 200:
                raise DirectoryUnavailable(f"token request for the directory client failed ({response.status_code})")
            body = response.json()
            self._token = body["access_token"]
            self._token_until = self._clock() + max(int(body.get("expires_in", 60)) - 30, 5)
        except DirectoryUnavailable:
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise DirectoryUnavailable("token request for the directory client failed") from None
        return self._token

    async def _get(self, path: str, **params: Any) -> Any:
        token = await self._service_token()
        try:
            response = await self._http.get(f"{self._s.admin_url}{path}", params=params,
                                            headers={"Authorization": f"Bearer {token}"})
            if response.status_code != 200:
                raise DirectoryUnavailable(f"GET {path} failed ({response.status_code})")
            return response.json()
        except DirectoryUnavailable:
            raise
        except (httpx.HTTPError, ValueError):
            raise DirectoryUnavailable(f"GET {path} failed") from None

    async def _paged(self, path: str, **params: Any) -> list[dict]:
        out: list[dict] = []
        while True:
            page = await self._get(path, first=len(out), max=PAGE, **params)
            if not isinstance(page, list):
                raise DirectoryUnavailable(f"GET {path} returned an unexpected body")
            out.extend(page)
            if len(page) < PAGE:
                return out

    # ---- reads

    async def _gateway_client_uuid(self) -> str:
        if self._client_uuid is None:
            found = await self._get("/clients", clientId=self._s.audience)
            if not isinstance(found, list) or not found or not found[0].get("id"):
                raise DirectoryUnavailable(f"the client {self._s.audience!r} was not found")
            self._client_uuid = found[0]["id"]
        return self._client_uuid

    async def _service_accounts(self) -> dict[str, dict]:
        """Keycloak leaves service-account users out of the user list; read them through their clients."""
        found: dict[str, dict] = {}
        for client in await self._paged("/clients"):
            if not client.get("serviceAccountsEnabled"):
                continue
            user = await self._get(f"/clients/{client['id']}/service-account-user")
            if isinstance(user, dict) and user.get("id"):
                found[user["id"]] = {**user, "serviceAccountClientId": client.get("clientId")}
        return found

    async def _group_members(self) -> dict[str, list[str]]:
        """user id -> leaf names of the groups they belong to directly."""
        members: dict[str, list[str]] = {}
        pending = [g for g in await self._paged("/groups", briefRepresentation="true")]
        while pending:
            group = pending.pop()
            for user in await self._paged(f"/groups/{group['id']}/members", briefRepresentation="true"):
                members.setdefault(user["id"], []).append(group["name"])
            if group.get("subGroupCount", len(group.get("subGroups") or ())):
                pending.extend(await self._paged(f"/groups/{group['id']}/children", briefRepresentation="true"))
        return members

    async def _roles(self, client_uuid: str, user_id: str, gate: asyncio.Semaphore) -> list[str]:
        async with gate:
            mapped = await self._get(f"/users/{user_id}/role-mappings/clients/{client_uuid}/composite")
        if not isinstance(mapped, list):
            raise DirectoryUnavailable("effective role mappings returned an unexpected body")
        return [r.get("name") for r in mapped]

    async def fetch_all(self) -> list[Account]:
        client_uuid = await self._gateway_client_uuid()
        users = {u["id"]: u for u in await self._paged("/users", briefRepresentation="true")}
        users.update(await self._service_accounts())
        groups = await self._group_members()
        gate = asyncio.Semaphore(CONCURRENCY)
        ids = list(users)
        roles = await asyncio.gather(*(self._roles(client_uuid, i, gate) for i in ids))
        accounts = []
        for user_id, user_roles in zip(ids, roles):
            u = users[user_id]
            username = u.get("username") or user_id
            kind = "service" if u.get("serviceAccountClientId") or username.startswith(SERVICE_ACCOUNT_PREFIX) else "user"
            accounts.append(Account(user_id, username, bool(u.get("enabled", True)),
                                    clean_groups(sorted(groups.get(user_id, []))), clean_roles(sorted(user_roles)), kind))
        return accounts
