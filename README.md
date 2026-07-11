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
