# Maintenance Scripts

These are canonical fixed scripts for independent service actions and the system-upgrade oneshot. They retain only Chub-specific sequencing, deferred-restart handling, state cleanup, and final health checks; every fixed `launchctl` or `systemctl` operation is delegated to `scripts/platform/service-management.sh`. Invoke them through `chub` or their fixed Chub callers. Root-level legacy maintenance paths are not provided.
