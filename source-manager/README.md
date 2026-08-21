# Source Manager V1

Source Manager is packaged as an extension of upstream Javis, not as a Javis fork.

## Phase 2

Phase 2 installs:
- a cross-engine USER/GLOBAL plugin into `<JAVIS_STATE_DIR>/plugins/source-manager/`;
- Brain-owned routing skills and configuration;
- a native `Javis/loops/source-watch.md`, disabled by default.

It does **not** implement semantic ingestion yet. The production plugin only exposes
deterministic read-only `status`, `doctor`, and `probe_file` tools.

## Install

Dry-run first:

```bash
python source-manager/install_source_manager.py --brain <BRAIN> --state-dir <JAVIS_STATE_DIR> --json
```

Apply:

```bash
python source-manager/install_source_manager.py --brain <BRAIN> --state-dir <JAVIS_STATE_DIR> --apply --json
```

Then run Javis with both:

```text
JAVIS_STATE_DIR=<same persistent state directory>
JAVIS_ENABLE_USER_PLUGINS=true
```

The installer never accepts a Javis checkout path and never writes `server/`, `system/`,
app `.claude/skills`, `dashboard/`, or root `requirements.txt`.

## Collision policy

Unknown or user-modified target files fail closed. On first install, the only permitted
replacement is `skills/ingest-source/SKILL.md` when the Brain's own
`.javis/system-manifest.json` proves that exact file is still Javis-managed and unmodified.
