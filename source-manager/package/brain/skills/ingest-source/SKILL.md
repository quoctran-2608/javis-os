---
name: Source Manager ingest route
description: Route ingest requests through Source Manager. Phase 2 only probes and validates; semantic ingest is intentionally disabled.
---
# SOURCE_MANAGER_PHASE2_ROUTE_ONLY
# PHASE2_NO_LEGACY_INGEST

This Brain uses Source Manager as the authority for source lifecycle.

When the user asks to ingest, import, digest, classify, or add a source:

1. Call `source_manager_status`.
2. If readiness is uncertain, call `source_manager_doctor`.
3. For an existing file under `Notes/`, `sources/`, or `Library/`, call
   `source_manager_probe_file` with a Brain-relative path.
4. Report the deterministic probe result and STOP.

Phase 2 boundary:
- DO NOT copy or move the source.
- DO NOT classify it as Living Note vs Reference Source.
- DO NOT edit frontmatter, tags, category, or `javis_id`.
- DO NOT call the legacy `Sources -> INGEST -> Wiki` workflow.
- DO NOT create or update Wiki/Memory.
- DO NOT run AI classification as a substitute for a missing Source Manager operation.

If the user requested actual ingestion, say that Source Manager Phase 2 has validated the
source but semantic ingestion is not enabled in this phase. A later phase must add that
deterministic operation before it is allowed.
