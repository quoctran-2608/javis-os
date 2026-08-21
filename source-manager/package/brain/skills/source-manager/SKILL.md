---
name: Source Manager
description: Inspect Source Manager status, architecture health, and deterministic source fingerprints without mutating the Brain.
---
# SOURCE_MANAGER_PHASE2

Source Manager Phase 2 is a read-only compatibility and provenance foundation.

Available operations:
- `source_manager_status`: report active Brain, plugin ownership, phase and capabilities.
- `source_manager_doctor`: verify the global USER plugin and Brain-owned route assets.
- `source_manager_probe_file`: SHA-256 fingerprint one file under `Notes/`, `sources/`, or
  `Library/` using a Brain-relative path.

Hard boundary in Phase 2:
- no semantic classification;
- no move/rename;
- no frontmatter/tag/category write;
- no ingest/compound;
- no Wiki/Memory write;
- no autonomous source-watch execution.

Deterministic evidence always precedes any later AI reasoning.
