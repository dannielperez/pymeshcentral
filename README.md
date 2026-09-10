# pymeshcentral

Python client for the MeshCentral control API. Owns opaque login-cookie auth and
the control-WebSocket protocol. Consumed by UniqueOS as a `vendor/` submodule.

The public surface is intentionally bounded:

- list device groups and devices;
- exact PC lookup by node ID, display name, or hostname;
- list device shares;
- create view-only desktop shares that require on-machine consent and expire;
- revoke a specific share by its public ID.

Every call has connect/receive timeouts and TLS verification enabled. The SDK
does not install agents, send email, persist workflow state, or retry mutations;
those responsibilities belong to the UniqueOS adapter and worker.

### Response timeout

`timeout` is one monotonic response-wait budget after sending a command. Unrelated
events, non-object messages and mismatched responses consume that same budget;
they do not restart it. A response returned at or after the deadline raises
`MeshTimeout`, and the connection is closed. Commands are not retried. Connection
opening and closing retain their separate timeout bounds; this is not a single
wall-clock deadline covering connection setup, sending, response processing and
teardown.

### Reported device users

`Device.logged_on_users` exposes the node's `users` list as an immutable tuple.
Missing or null data returns `None` (unknown); an empty list returns `()`
(reported empty). Domain qualifiers and case are preserved. Malformed data
raises `MeshProtocolError` without including the payload in the error.

This is reported evidence, without a freshness guarantee. It does not establish
workstation ownership or authorize remote access. Consumers must apply their own
identity, ambiguity, freshness and access policies. The existing six-argument
`Device` constructor remains valid and defaults this field to `None`.
