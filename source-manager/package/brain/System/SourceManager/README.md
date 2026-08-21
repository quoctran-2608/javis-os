# Source Manager — Phase 2 contract

Source Manager owns source lifecycle decisions for this Brain, while Javis remains the AI
runtime. Phase 2 only establishes the integration boundary and deterministic read-only
evidence.

## Ownership

- Executable plugin: `<JAVIS_STATE_DIR>/plugins/source-manager/` (USER/GLOBAL plugin).
- Brain routing skills: `skills/ingest-source/` and `skills/source-manager/`.
- Native scheduler definition: `Javis/loops/source-watch.md`.
- Brain configuration: `System/SourceManager/config.yml`.
- Future derived state: `.javis/source-manager.db`.

There must not be a duplicate `<Brain>/plugins/source-manager/`: Claude intentionally loads
with vault plugins out of scope, while other paths may include them, which would create
cross-engine split authority.

## Phase 2 safety boundary

Phase 2 may inspect status, run doctor checks, and fingerprint an existing file. It may not
classify, ingest, move, rename, edit note metadata, create taxonomy, write Wiki/Memory, or
execute Source Watch automatically.

The `source-watch` loop is therefore shipped with `enabled: false`.
