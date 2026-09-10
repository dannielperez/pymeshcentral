import json
from datetime import timedelta

import pytest

from pymeshcentral import (
    Device,
    MeshAuthError,
    MeshCentralClient,
    MeshProtocolError,
    MeshTimeout,
)


class FakeConnection:
    def __init__(self, response_factory):
        self.response_factory = response_factory
        self.sent = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def send(self, message):
        self.sent = json.loads(message)

    def recv(self, timeout=None):
        return json.dumps(self.response_factory(self.sent))


class FakeConnector:
    def __init__(self, response_factory):
        self.response_factory = response_factory
        self.calls = []
        self.connection = None

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        self.connection = FakeConnection(self.response_factory)
        return self.connection


def response(command, **payload):
    key = "tag" if "tag" in command else "responseid"
    return {"action": command["action"], key: command[key], **payload}


def client(response_factory):
    connector = FakeConnector(response_factory)
    return MeshCentralClient(
        "https://mesh.example.test/tenant", "opaque-cookie", connector=connector
    ), connector


def test_list_devices_flattens_groups_and_uses_bounded_exact_lookup():
    def factory(command):
        return response(
            command,
            nodes={
                "mesh/domain/group": [
                    {
                        "_id": "node/domain/abc",
                        "name": "Front Desk",
                        "host": "PC-001",
                        "conn": 1,
                    },
                    {"_id": "node/domain/def", "name": "Front Desk Backup"},
                ]
            },
        )

    mesh, connector = client(factory)

    devices = mesh.lookup_devices("pc-001")

    assert [device.id for device in devices] == ["node/domain/abc"]
    assert devices[0].connected is True
    assert connector.connection.sent["action"] == "nodes"
    assert connector.calls[0][0] == (
        "wss://mesh.example.test/tenant/control.ashx?auth=opaque-cookie"
    )
    assert connector.calls[0][1]["open_timeout"] == 10.0


def test_list_device_groups_uses_upstream_tag_correlation():
    mesh, connector = client(
        lambda command: response(
            command, meshes=[{"_id": "mesh/domain/one", "name": "Default"}]
        )
    )

    groups = mesh.list_device_groups()

    assert groups[0].name == "Default"
    assert "tag" in connector.connection.sent
    assert "responseid" not in connector.connection.sent


def test_create_share_is_desktop_view_only_prompt_and_one_hour_by_default():
    mesh, connector = client(
        lambda command: response(
            command,
            result="OK",
            publicid="share-123",
            nodeid=command["nodeid"],
            guestname=command["guestname"],
            url="https://mesh.example.test/sharing?c=redacted",
            viewOnly=command["viewOnly"],
            consent=command["consent"],
        )
    )

    share = mesh.create_desktop_share("node/domain/abc", "Vendor")

    assert share.public_id == "share-123"
    assert share.view_only is True
    assert share.consent == 0x0008
    assert connector.connection.sent == {
        "action": "createDeviceShareLink",
        "responseid": connector.connection.sent["responseid"],
        "nodeid": "node/domain/abc",
        "guestname": "Vendor",
        "p": 2,
        "consent": 0x0008,
        "expire": 60,
        "viewOnly": True,
    }


def test_create_share_rejects_unbounded_or_empty_input():
    mesh, _ = client(lambda command: response(command, result="OK"))

    with pytest.raises(ValueError, match="guest_name"):
        mesh.create_desktop_share("node/domain/abc", " ")
    with pytest.raises(ValueError, match="24 hours"):
        mesh.create_desktop_share(
            "node/domain/abc", "Vendor", duration=timedelta(days=2)
        )


def test_list_and_revoke_share_pin_upstream_action_names():
    seen = []

    def factory(command):
        seen.append(command["action"])
        if command["action"] == "deviceShares":
            # Upstream omits responseid on the successful deviceShares response.
            return {
                "action": "deviceShares",
                "nodeid": command["nodeid"],
                "deviceShares": [
                    {
                        "publicid": "share-123",
                        "nodeid": command["nodeid"],
                        "guestName": "Vendor",
                        "viewOnly": True,
                        "consent": 8,
                    }
                ],
            }
        return response(command, removed={"publicid": command["publicid"]})

    mesh, _ = client(factory)

    assert mesh.list_device_shares("node/domain/abc")[0].public_id == "share-123"
    mesh.revoke_device_share("node/domain/abc", "share-123")

    assert seen == ["deviceShares", "removeDeviceShare"]


def test_access_denied_is_typed_auth_error():
    mesh, _ = client(lambda command: response(command, result="Access denied"))

    with pytest.raises(MeshAuthError):
        mesh.list_devices()


def test_timeout_is_typed():
    class TimeoutConnection(FakeConnection):
        def recv(self, timeout=None):
            raise TimeoutError

    connector = FakeConnector(lambda command: {})
    connector.connection = None

    def connect(url, **kwargs):
        connector.calls.append((url, kwargs))
        connector.connection = TimeoutConnection(lambda command: {})
        return connector.connection

    mesh = MeshCentralClient(
        "https://mesh.example.test", "opaque-cookie", connector=connect, timeout=2
    )

    with pytest.raises(MeshTimeout):
        mesh.list_devices()


@pytest.mark.parametrize(
    "url",
    [
        "http://mesh.example.test",
        "https://user:pass@mesh.example.test",
        "javascript:alert(1)",
        "https:///missing-host",
    ],
)
def test_client_rejects_unsafe_base_urls(url):
    with pytest.raises(ValueError, match="HTTPS"):
        MeshCentralClient(url, "opaque-cookie")


def test_protocol_shape_failure_is_typed():
    mesh, _ = client(lambda command: response(command, nodes=[]))

    with pytest.raises(MeshProtocolError):
        mesh.list_devices()


class DeadlineConnection(FakeConnection):
    def __init__(self, clock, messages):
        super().__init__(None)
        self.clock = clock
        self.messages = iter(messages)
        self.budgets = []
        self.closed = False

    def __exit__(self, *args):
        self.closed = True

    def recv(self, timeout=None):
        self.budgets.append(timeout)
        elapsed, message = next(self.messages)
        self.clock[0] += elapsed
        return json.dumps(message(self.sent) if callable(message) else message)


def deadline_client(monkeypatch, messages):
    import pymeshcentral.client as client_module

    clock = [100.0]
    monkeypatch.setattr(client_module, "monotonic", lambda: clock[0], raising=False)
    connection = DeadlineConnection(clock, messages)
    mesh = MeshCentralClient(
        "https://mesh.example.test",
        "opaque-cookie",
        timeout=2,
        connector=lambda *args, **kwargs: connection,
    )
    return mesh, connection


@pytest.mark.parametrize(
    "event", [{"action": "event"}, ["event"], {"responseid": "other"}]
)
def test_unrelated_events_consume_one_response_deadline(monkeypatch, event):
    mesh, connection = deadline_client(
        monkeypatch,
        [(0.75, event)] * 3 + [(0, lambda command: response(command, nodes={}))],
    )

    with pytest.raises(MeshTimeout):
        mesh.list_devices()

    assert connection.budgets == pytest.approx([2, 1.25, 0.5])
    assert connection.closed is True


def test_correlated_response_before_deadline_succeeds_with_remaining_budget(
    monkeypatch,
):
    mesh, connection = deadline_client(
        monkeypatch,
        [
            (0.75, {"action": "event"}),
            (0.5, lambda command: response(command, nodes={})),
        ],
    )

    assert mesh.list_devices() == []
    assert connection.budgets == pytest.approx([2, 1.25])
    assert connection.closed is True


def test_correlated_response_at_deadline_is_not_accepted(monkeypatch):
    mesh, connection = deadline_client(
        monkeypatch,
        [(2, lambda command: response(command, nodes={}))],
    )

    with pytest.raises(MeshTimeout):
        mesh.list_devices()

    assert connection.closed is True


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({}, None),
        ({"users": None}, None),
        ({"users": []}, ()),
        (
            {"users": ["DOMAIN\\Alex", "OTHER\\Alex", "alex@example.invalid"]},
            ("DOMAIN\\Alex", "OTHER\\Alex", "alex@example.invalid"),
        ),
    ],
)
def test_device_exposes_reported_users_without_guessing_identity(fields, expected):
    mesh, _ = client(
        lambda command: response(
            command,
            nodes={"mesh/domain/group": [{"_id": "node/domain/pc", **fields}]},
        )
    )
    device = mesh.list_devices()[0]
    assert getattr(device, "logged_on_users", None) == expected
    if expected:
        device.raw["users"].append("later-user")
        assert device.logged_on_users == expected


@pytest.mark.parametrize("users", ["DOMAIN\\Alex", {"name": "Alex"}, [7], [" "]])
def test_malformed_reported_users_fail_closed_without_disclosing_payload(users):
    mesh, _ = client(
        lambda command: response(
            command,
            nodes={"mesh/domain/group": [{"_id": "node/domain/pc", "users": users}]},
        )
    )
    with pytest.raises(
        MeshProtocolError, match="^nodes response contained invalid users$"
    ):
        mesh.list_devices()


def test_device_existing_constructor_defaults_to_unknown_user_evidence():
    device = Device("node/domain/pc", "mesh/domain/group", "PC", None, False, {})
    assert device.logged_on_users is None
