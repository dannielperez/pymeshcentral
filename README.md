# pymeshcentral

Python client for the MeshCentral control API. Owns login-token auth and the
control-websocket protocol (list_devices, list_device_groups). Read-only first;
write paths are individually gated. Consumed by UniqueOS as a vendor/ submodule.
