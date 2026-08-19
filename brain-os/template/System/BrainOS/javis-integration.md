# Brain OS ↔ Javis Integration Contract

Brain OS is the governance + lifecycle layer for a Brain. Javis remains the AI/execution layer.

## Activation

This contract is active when the current Brain contains both:

- `System/BrainOS/config.yml`
- `skills/brain-manager/scripts/brain_os.py`

Javis skills that create, import, ingest, compound, move, rename, or mutate note metadata MUST read and obey this contract first. If Brain OS is absent, legacy Javis behavior may be used.

## Boundary

Brain OS decides/records:

- stable identity (`javis_id`)
- document type and lifecycle
- import/original provenance
- deterministic change detection and incremental state
- taxonomy/category/tag planning
- ingestion policy/routing

Javis executes:

- AI reasoning
- INGEST
- Wiki/Memory/Knowledge Graph writes
- skills and scheduled loops

Short form:

> Brain OS decides what should be learned and tracks lifecycle. Javis performs the learning/compounding.

## Mandatory preflight before Javis INGEST

1. Resolve the exact current Brain and target file.
2. If the target is outside the Brain, import it through Brain OS before reading it as a managed source:
   - Amplenote export directory/ZIP/single Markdown identified by the user as Amplenote: `import_amplenote.py ... --apply`.
   - Other Markdown: `brain_os.py import ... --apply`.
   - PDF/DOCX/XLSX/CSV/TSV: `import_document.py ... --apply`.
3. Continue from the returned editable `working_path` / `normalized_working_path`, never from the external original.
4. Never silently copy an external Markdown file straight into `sources/`.
5. Existing Brain files must be scanned/classified/taxonomy-planned before automatic routing decisions when current state is missing/stale.
6. Respect hard safety/manual overrides such as `javis: ignore` and never ingest `wiki/**`, `.javis/**`, `System/**`, or other ignored operational areas.

## Living Notes

A Living Note is long-lived user-owned Markdown and remains editable.

- Keep it under the Living Note scope (`Notes/...`), not `sources/` merely because Javis is ingesting it.
- Do not split, replace, move repeatedly, or convert it into a static source.
- Do not write `status: processed` / `processed_at` as lifecycle truth.
- Do not treat ingest as done forever. Brain OS tracks `last_ingested_hash`; later edits become stale/incremental work.
- Compound selectively. Personal reflection that is not reusable knowledge may remain only in the Living Note; uncertain reusable insight should prefer candidate/review over creating many Wiki pages.

## Reference Sources

Reference sources may live under `sources/`; binary originals stay under `Library/` and are ingested only through their normalized Markdown source.

## Metadata and taxonomy

- Technical lifecycle state belongs in `.javis/brain-index.db`, not user frontmatter.
- Preserve unknown user metadata.
- Reuse canonical categories/tags; do not create folder/tag taxonomy ad hoc.
- Amplenote legacy tags are preserved as `legacy_tags` when canonicalized.

## Recording a completed manual INGEST

After Javis successfully ingests a Brain OS-managed Markdown file, record the exact current source hash/state in derived DB state:

```bash
python skills/brain-manager/scripts/record_ingest.py --path "<brain-relative-working-path>"
```

If Wiki/Memory compounding was actually written, add `--compounded`.

This command must never rewrite the source note.

## `dry_run` and explicit user commands

`dry_run: true` keeps Brain OS automation/structural mutations conservative: no autonomous move, rename, taxonomy creation, user-note rewrite, or automatic Javis execution.

A direct user command such as “tiêu hoá file X” is an explicit Javis execution request. It may write derived Wiki/Memory after this Brain OS preflight succeeds, while Brain OS structural safety rules remain in force. Do not interpret a direct ingest request as permission to bypass ignored zones, provenance, stable identity, or Living Note rules.

## Wiki rules

Wiki is derived knowledge, never the original source of truth.

- Do not ingest Wiki back into itself.
- Keep source provenance/backlinks.
- Do not delete Wiki automatically when a source disappears.
- Preserve contradictions instead of silently overwriting prior sourced claims.
