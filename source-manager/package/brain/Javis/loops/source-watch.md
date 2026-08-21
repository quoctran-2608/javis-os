---
type: loop
name: Source Watch
slug: source-watch
enabled: false
goal: custom
mode: suggest
interval_min: 15
workspace: vault
tools_profile: vault-safe
notify: false
---

SOURCE_MANAGER_PHASE2_DISABLED_LOOP

Phase 2 intentionally keeps Source Watch disabled. When a later phase enables it, the loop
must use Source Manager deterministic operations first and must not perform legacy automatic
`Sources -> INGEST -> Wiki` processing.
