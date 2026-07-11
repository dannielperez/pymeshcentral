"""Bounded synchronous client for MeshCentral's control WebSocket API."""

from __future__ import annotations

import json
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4


class MeshCentralError(RuntimeError):
    """Base error returned by the MeshCentral client."""


class MeshAuthError(MeshCentralError):
    """Authentication or authorization failed."""


class MeshTimeout(MeshCentralError):
    """MeshCentral didn't return a matching response before the deadline."""


class MeshProtocolError(MeshCentralError):
    """MeshCentral returned an unexpected response."""


class _Connection(Protocol):
    def __enter__(self) -> _Connection: ...
    def __exit__(self, *args: object) -> None: ...
    def send(self, message: str) -> None: ...
    def recv(self, timeout: float | None = None) -> str | bytes: ...


Connector = Callable[..., _Connection]


@dataclass(frozen=True)
class Device:
    id: str
    mesh_id: str
    name: str
    hostname: str | None
    connected: bool
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class DeviceGroup:
    id: str
    name: str
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class DeviceShare:
    public_id: str
    node_id: str
    guest_name: str
    url: str | None
    view_only: bool
    consent: int
    expires_at_ms: int | None
    raw: Mapping[str, Any]


class MeshCentralClient:
    """A fail-closed client using an opaque MeshCentral login cookie."""

    def __init__(
        self,
        base_url: str,
        auth_cookie: str,
        *,
        timeout: float = 10.0,
        connector: Connector | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if not auth_cookie or any(char.isspace() for char in auth_cookie):
            raise ValueError("auth_cookie must be a non-empty opaque token")
        self._url = _control_url(base_url, auth_cookie)
        self._timeout = timeout
        self._connector = connector or _default_connector

    def list_devices(self) -> list[Device]:
        response = self._request("nodes")
        groups = response.get("nodes")
        if not isinstance(groups, dict):
            raise MeshProtocolError("nodes response did not contain a group mapping")
        devices: list[Device] = []
        for mesh_id, entries in groups.items():
            if not isinstance(entries, list):
                raise MeshProtocolError("nodes response contained a non-list group")
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("_id"), str):
                    raise MeshProtocolError(
                        "nodes response contained an invalid device"
                    )
                devices.append(_device(entry, str(mesh_id)))
        return devices

    def lookup_devices(self, query: str, *, limit: int = 25) -> list[Device]:
        """Return bounded exact ID/name/hostname matches for a PC identifier."""
        needle = query.strip().casefold()
        if not needle:
            raise ValueError("query must not be empty")
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        matches = []
        for device in self.list_devices():
            candidates = {
                device.id.casefold(),
                device.id.rsplit("/", 1)[-1].casefold(),
                device.name.casefold(),
            }
            if device.hostname:
                candidates.add(device.hostname.casefold())
            if needle in candidates:
                matches.append(device)
                if len(matches) == limit:
                    break
        return matches

    def list_device_groups(self) -> list[DeviceGroup]:
        response = self._request("meshes", correlate_with_tag=True)
        meshes = response.get("meshes")
        if not isinstance(meshes, list):
            raise MeshProtocolError("meshes response did not contain a list")
        result = []
        for mesh in meshes:
            if not isinstance(mesh, dict) or not isinstance(mesh.get("_id"), str):
                raise MeshProtocolError("meshes response contained an invalid group")
            result.append(DeviceGroup(mesh["_id"], str(mesh.get("name", "")), mesh))
        return result

    def list_device_shares(self, node_id: str) -> list[DeviceShare]:
        response = self._request(
            "deviceShares", correlate_with_node=True, nodeid=_node_id(node_id)
        )
        shares = response.get("deviceShares")
        if not isinstance(shares, list):
            raise MeshProtocolError("deviceShares response did not contain a list")
        return [_share(item, node_id) for item in shares]

    def create_desktop_share(
        self,
        node_id: str,
        guest_name: str,
        *,
        duration: timedelta = timedelta(hours=1),
    ) -> DeviceShare:
        """Create a view-only desktop share that requires on-machine consent."""
        minutes = int(duration.total_seconds() // 60)
        if not guest_name.strip():
            raise ValueError("guest_name must not be empty")
        if minutes < 1 or minutes > 24 * 60:
            raise ValueError("duration must be between 1 minute and 24 hours")
        response = self._request(
            "createDeviceShareLink",
            nodeid=_node_id(node_id),
            guestname=guest_name.strip(),
            p=2,
            consent=0x0008,
            expire=minutes,
            viewOnly=True,
        )
        return _share(response, node_id)

    def revoke_device_share(self, node_id: str, public_id: str) -> None:
        if not public_id.strip():
            raise ValueError("public_id must not be empty")
        response = self._request(
            "removeDeviceShare", nodeid=_node_id(node_id), publicid=public_id.strip()
        )
        if response.get("result") != "OK" and not response.get("removed"):
            raise MeshCentralError(str(response.get("result", "share was not removed")))

    def _request(
        self,
        action: str,
        *,
        correlate_with_tag: bool = False,
        correlate_with_node: bool = False,
        **payload: Any,
    ) -> dict[str, Any]:
        correlation = uuid4().hex
        correlation_key = "tag" if correlate_with_tag else "responseid"
        command = {"action": action, correlation_key: correlation, **payload}
        try:
            with self._connector(
                self._url,
                open_timeout=self._timeout,
                close_timeout=self._timeout,
                ssl=ssl.create_default_context(),
            ) as websocket:
                websocket.send(json.dumps(command, separators=(",", ":")))
                while True:
                    raw = websocket.recv(timeout=self._timeout)
                    response = json.loads(raw)
                    if not isinstance(response, dict):
                        continue
                    correlated = response.get(correlation_key) == correlation
                    if correlate_with_node and response.get(correlation_key) is None:
                        correlated = response.get("action") == action and response.get(
                            "nodeid"
                        ) == payload.get("nodeid")
                    if not correlated:
                        continue
                    result = response.get("result")
                    if isinstance(result, str) and result.lower() not in {"ok", ""}:
                        if (
                            "access denied" in result.lower()
                            or "auth" in result.lower()
                        ):
                            raise MeshAuthError(result)
                        raise MeshCentralError(result)
                    return response
        except MeshCentralError:
            raise
        except TimeoutError as exc:
            raise MeshTimeout(f"MeshCentral {action} timed out") from exc
        except (json.JSONDecodeError, TypeError) as exc:
            raise MeshProtocolError(f"invalid MeshCentral {action} response") from exc
        except OSError as exc:
            raise MeshCentralError(f"MeshCentral {action} transport failed") from exc


def _control_url(base_url: str, auth_cookie: str) -> str:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        raise ValueError("base_url must be an HTTPS URL without embedded credentials")
    path = parsed.path.rstrip("/") + "/control.ashx"
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("auth", auth_cookie))
    return urlunsplit(("wss", parsed.netloc, path, urlencode(query), ""))


def _node_id(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("node_id must not be empty")
    return value


def _device(entry: dict[str, Any], mesh_id: str) -> Device:
    return Device(
        id=entry["_id"],
        mesh_id=str(entry.get("meshid", mesh_id)),
        name=str(entry.get("name", "")),
        hostname=str(entry["host"]) if entry.get("host") else None,
        connected=bool(entry.get("conn")),
        raw=entry,
    )


def _share(item: Any, node_id: str) -> DeviceShare:
    if not isinstance(item, dict) or not isinstance(item.get("publicid"), str):
        raise MeshProtocolError("device share response was invalid")
    return DeviceShare(
        public_id=item["publicid"],
        node_id=str(item.get("nodeid", node_id)),
        guest_name=str(item.get("guestName", item.get("guestname", ""))),
        url=str(item["url"]) if item.get("url") else None,
        view_only=item.get("viewOnly") is True,
        consent=int(item.get("consent", 0)),
        expires_at_ms=int(item["expireTime"]) if item.get("expireTime") else None,
        raw=item,
    )


def _default_connector(url: str, **kwargs: Any) -> _Connection:
    from websockets.sync.client import connect

    return connect(url, **kwargs)
