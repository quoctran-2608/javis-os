from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from pathlib import PurePosixPath
from typing import Any, Iterable

from .config import BrainOSConfig
from .db import BrainIndex, utc_now
from .metadata import MarkdownProbe, read_markdown_probe
from .models import BrainFile, DocumentType, ProcessingState
from .paths import is_under, normalize_rel_path


TAXONOMY_VERSION = 1


class TaxonomyError(RuntimeError):
    pass


@dataclass(frozen=True)
class CategoryDefinition:
    scope: str
    id: str
    label: str
    path: str
    aliases: tuple[str, ...]
    depth: int
    slug_path: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["aliases"] = list(self.aliases)
        return data


@dataclass(frozen=True)
class TagDefinition:
    name: str
    id: str
    label: str
    description: str = ""


@dataclass(frozen=True)
class TaxonomyDecision:
    applicable: bool
    scope: str
    status: str
    accepted: bool
    candidate: bool
    ambiguous: bool
    confidence: float
    category_id: str
    proposed_category_id: str
    current_location_category_id: str
    target_directory: str
    fallback_directory: str
    would_move_to: str
    canonical_existing_tags: tuple[str, ...]
    proposed_tags: tuple[str, ...]
    legacy_tags: tuple[str, ...]
    tag_suggestions: tuple[dict[str, Any], ...]
    ranking: tuple[dict[str, Any], ...]
    reason_codes: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in (
            "canonical_existing_tags",
            "proposed_tags",
            "legacy_tags",
            "tag_suggestions",
            "ranking",
            "reason_codes",
            "warnings",
        ):
            data[key] = list(data[key])
        return data


@dataclass
class TaxonomyReport:
    ok: bool = True
    initialized: bool = True
    scanned_records: int = 0
    analyzed: int = 0
    cached: int = 0
    missing: int = 0
    not_applicable: int = 0
    accepted: int = 0
    candidates: int = 0
    ambiguous: int = 0
    unsorted: int = 0
    would_move: int = 0
    tag_suggestions: int = 0
    path_filtered_out: int = 0
    status_counts: dict[str, int] = field(default_factory=dict)
    category_counts: dict[str, int] = field(default_factory=dict)
    would_move_paths: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ascii_text(value: Any) -> str:
    text = str(value or "").casefold().replace("đ", "d")
    normalized = unicodedata.normalize("NFKD", text)
    chars = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", chars).strip()


def _slug_segment(value: str) -> str:
    return _ascii_text(value).replace(" ", "-")


def _slug_path(value: str) -> str:
    parts = [_slug_segment(part) for part in PurePosixPath(value).parts]
    return "/".join(part for part in parts if part)


def _tag_key(value: Any) -> str:
    return str(value or "").strip().lstrip("#").strip().casefold()


def _safe_taxonomy_path(value: Any, *, field_name: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/"):
        raise TaxonomyError(f"{field_name} phải là relative path không rỗng: {value!r}")
    parts = PurePosixPath(raw).parts
    if any(part in ("", ".", "..") for part in parts):
        raise TaxonomyError(f"{field_name} chứa path không an toàn: {value!r}")
    return PurePosixPath(*parts).as_posix()


def _phrase_count(normalized_text: str, normalized_phrase: str) -> int:
    if not normalized_text or not normalized_phrase:
        return 0
    haystack = f" {normalized_text} "
    needle = f" {normalized_phrase} "
    return haystack.count(needle)


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return tuple(result)


class TaxonomyRegistry:
    """Validated view of the folder/tag registries shipped in System/Taxonomy."""

    def __init__(self, config: BrainOSConfig):
        self.config = config
        self.categories: dict[str, CategoryDefinition] = {}
        self.categories_by_scope: dict[str, list[CategoryDefinition]] = {}
        self.scope_roots: dict[str, tuple[str, ...]] = {}
        self.scope_fallbacks: dict[str, str] = {}
        self._category_aliases: dict[str, dict[str, set[str]]] = {}

        self.tags: dict[str, TagDefinition] = {}
        self._tag_exact: dict[str, str] = {}
        self._tag_phrases: dict[str, str] = {}

        self._load_tags()
        self._load_categories()
        self._load_aliases()

    @classmethod
    def from_config(cls, config: BrainOSConfig) -> "TaxonomyRegistry":
        return cls(config)

    def _load_tags(self) -> None:
        raw = self.config.tags.get("canonical_tags") or {}
        if not isinstance(raw, dict):
            raise TaxonomyError("tags.yml: canonical_tags phải là mapping")

        config_tags = self.config.core.get("tags") or {}
        policy = self.config.tags.get("policy") or {}
        max_depth = min(
            int(config_tags.get("max_depth", 3)),
            int(policy.get("max_depth", config_tags.get("max_depth", 3))),
        )

        ids: set[str] = set()
        for name, payload in raw.items():
            canonical = _tag_key(name)
            if not canonical or any(part in ("", ".", "..") for part in canonical.split("/")):
                raise TaxonomyError(f"Canonical tag không hợp lệ: {name!r}")
            if len(canonical.split("/")) > max_depth:
                raise TaxonomyError(
                    f"Canonical tag vượt max_depth={max_depth}: {canonical!r}"
                )
            if not isinstance(payload, dict):
                raise TaxonomyError(f"Tag {name!r} phải là mapping")
            tag_id = str(payload.get("id") or "").strip()
            if not tag_id or tag_id in ids:
                raise TaxonomyError(f"Tag id thiếu hoặc trùng: {tag_id!r}")
            ids.add(tag_id)
            if canonical in self.tags:
                raise TaxonomyError(f"Canonical tag trùng: {canonical}")

            definition = TagDefinition(
                name=canonical,
                id=tag_id,
                label=str(payload.get("label") or canonical).strip(),
                description=str(payload.get("description") or "").strip(),
            )
            self.tags[canonical] = definition
            self._tag_exact[canonical] = canonical
            for phrase in (_ascii_text(canonical), _ascii_text(definition.label)):
                if phrase:
                    previous = self._tag_phrases.get(phrase)
                    if previous and previous != canonical:
                        # Labels may be human-friendly and accidentally collide.
                        # Exact canonical tag still remains resolvable; ambiguous
                        # text phrase evidence is simply dropped.
                        self._tag_phrases.pop(phrase, None)
                    elif phrase not in self._tag_phrases:
                        self._tag_phrases[phrase] = canonical

    def _load_categories(self) -> None:
        scopes = self.config.folders.get("scopes") or {}
        if not isinstance(scopes, dict) or not scopes:
            raise TaxonomyError("folders.yml: scopes phải là mapping không rỗng")

        folder_cfg = self.config.core.get("folders") or {}
        creation = self.config.folders.get("creation_policy") or {}
        max_depth = min(
            int(folder_cfg.get("max_depth", 3)),
            int(creation.get("max_depth", folder_cfg.get("max_depth", 3))),
        )

        global_ids: set[str] = set()

        for scope, payload in scopes.items():
            if not isinstance(payload, dict):
                raise TaxonomyError(f"Scope {scope!r} phải là mapping")
            roots_raw = payload.get("roots") or []
            if not isinstance(roots_raw, list) or not roots_raw:
                raise TaxonomyError(f"Scope {scope!r} phải có roots")
            roots = tuple(
                _safe_taxonomy_path(value, field_name=f"scopes.{scope}.roots[]")
                for value in roots_raw
            )
            fallback = _safe_taxonomy_path(
                payload.get("fallback", "_Unsorted"),
                field_name=f"scopes.{scope}.fallback",
            )
            self.scope_roots[str(scope)] = roots
            self.scope_fallbacks[str(scope)] = fallback
            self.categories_by_scope[str(scope)] = []
            self._category_aliases[str(scope)] = {}

            categories = payload.get("categories") or {}
            if not isinstance(categories, dict):
                raise TaxonomyError(f"Scope {scope!r}: categories phải là mapping")

            def walk(
                nodes: dict[str, Any],
                *,
                parent_path: str = "",
            ) -> None:
                for key, node in nodes.items():
                    if not isinstance(node, dict):
                        raise TaxonomyError(
                            f"Category {scope}.{key} phải là mapping"
                        )
                    category_id = str(node.get("id") or "").strip()
                    if not category_id or category_id in global_ids:
                        raise TaxonomyError(
                            f"Category id thiếu hoặc trùng: {category_id!r}"
                        )
                    global_ids.add(category_id)

                    category_path = _safe_taxonomy_path(
                        node.get("path"),
                        field_name=f"category.{category_id}.path",
                    )
                    depth = len(PurePosixPath(category_path).parts)
                    if depth > max_depth:
                        raise TaxonomyError(
                            f"Category {category_id} vượt max_depth={max_depth}: {category_path}"
                        )
                    if parent_path and not (
                        category_path == parent_path
                        or category_path.startswith(parent_path + "/")
                    ):
                        raise TaxonomyError(
                            f"Child category {category_id} path={category_path!r} "
                            f"không nằm dưới parent={parent_path!r}"
                        )

                    aliases_raw = node.get("aliases") or []
                    if not isinstance(aliases_raw, list):
                        raise TaxonomyError(
                            f"Category {category_id}: aliases phải là list"
                        )
                    aliases = _unique(str(v).strip() for v in aliases_raw if str(v).strip())
                    definition = CategoryDefinition(
                        scope=str(scope),
                        id=category_id,
                        label=str(node.get("label") or key).strip(),
                        path=category_path,
                        aliases=aliases,
                        depth=depth,
                        slug_path=_slug_path(category_path),
                    )
                    self.categories[category_id] = definition
                    self.categories_by_scope[str(scope)].append(definition)

                    lookup = self._category_aliases[str(scope)]
                    values = (
                        category_id,
                        definition.label,
                        definition.path,
                        PurePosixPath(definition.path).name,
                        *definition.aliases,
                    )
                    for raw_value in values:
                        phrase = _ascii_text(raw_value)
                        if phrase:
                            lookup.setdefault(phrase, set()).add(category_id)

                    children = node.get("children") or {}
                    if children:
                        if not isinstance(children, dict):
                            raise TaxonomyError(
                                f"Category {category_id}: children phải là mapping"
                            )
                        walk(children, parent_path=category_path)

            walk(categories)
            self.categories_by_scope[str(scope)].sort(
                key=lambda category: (category.depth, category.path.casefold())
            )

    def _load_aliases(self) -> None:
        raw = self.config.tag_aliases.get("aliases") or {}
        if not isinstance(raw, dict):
            raise TaxonomyError("tag-aliases.yml: aliases phải là mapping")

        for alias, target in raw.items():
            canonical = _tag_key(target)
            if canonical not in self.tags:
                raise TaxonomyError(
                    f"Tag alias {alias!r} trỏ tới canonical tag không tồn tại: {target!r}"
                )
            phrase = _ascii_text(alias)
            if not phrase:
                raise TaxonomyError(f"Tag alias rỗng/không hợp lệ: {alias!r}")
            previous = self._tag_phrases.get(phrase)
            if previous and previous != canonical:
                raise TaxonomyError(
                    f"Tag alias collision sau normalize: {alias!r} -> {canonical}, "
                    f"đã map tới {previous}"
                )
            self._tag_phrases[phrase] = canonical

    @property
    def tag_phrase_map(self) -> dict[str, str]:
        return dict(self._tag_phrases)

    def resolve_tag(self, value: Any) -> str:
        exact = _tag_key(value)
        if exact in self._tag_exact:
            return self._tag_exact[exact]
        return self._tag_phrases.get(_ascii_text(value), "")

    def resolve_category(self, scope: str, value: Any) -> CategoryDefinition | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        direct = self.categories.get(raw)
        if direct is not None and direct.scope == scope:
            return direct
        phrase = _ascii_text(raw)
        ids = self._category_aliases.get(scope, {}).get(phrase, set())
        if len(ids) != 1:
            return None
        return self.categories[next(iter(ids))]

    def roots_for(self, scope: str) -> tuple[str, ...]:
        return self.scope_roots.get(scope, ())

    def fallback_for(self, scope: str) -> str:
        return self.scope_fallbacks.get(scope, "_Unsorted")

    def category_for_location(
        self,
        scope: str,
        rel_path: str,
    ) -> tuple[CategoryDefinition | None, str]:
        rel = normalize_rel_path(rel_path)
        matching_root = ""
        for root in self.roots_for(scope):
            if is_under(rel, root):
                matching_root = root
                break
        if not matching_root:
            return None, ""

        parts = PurePosixPath(rel).parts
        root_parts = PurePosixPath(matching_root).parts
        remainder = parts[len(root_parts) :]
        parent = PurePosixPath(*remainder[:-1]).as_posix() if len(remainder) > 1 else ""
        if not parent:
            return None, matching_root

        matches = [
            category
            for category in self.categories_by_scope.get(scope, [])
            if parent == category.path or parent.startswith(category.path + "/")
        ]
        if not matches:
            return None, matching_root
        return max(matches, key=lambda category: category.depth), matching_root

    def categories_for_tag(self, scope: str, canonical_tag: str) -> list[CategoryDefinition]:
        matches = [
            category
            for category in self.categories_by_scope.get(scope, [])
            if canonical_tag == category.slug_path
            or canonical_tag.startswith(category.slug_path + "/")
        ]
        if not matches:
            return []
        deepest = max(category.depth for category in matches)
        return [category for category in matches if category.depth == deepest]

    def default_tag_for_category(self, category: CategoryDefinition) -> str:
        return category.slug_path if category.slug_path in self.tags else ""


def _taxonomy_options(config: BrainOSConfig) -> dict[str, Any]:
    taxonomy = config.core.get("taxonomy") or {}
    classification = config.core.get("classification") or {}
    tags = config.core.get("tags") or {}
    if taxonomy and not isinstance(taxonomy, dict):
        raise TaxonomyError("config taxonomy phải là mapping")

    max_text = int(taxonomy.get("max_text_probe_bytes", 131072))
    if not 4096 <= max_text <= 1024 * 1024:
        raise TaxonomyError(
            "taxonomy.max_text_probe_bytes phải trong 4096..1048576"
        )
    ambiguity_margin = int(taxonomy.get("ambiguity_margin", 2))
    if not 0 <= ambiguity_margin <= 20:
        raise TaxonomyError("taxonomy.ambiguity_margin phải trong 0..20")

    fields = taxonomy.get("tag_fields") or ["tags", "tag"]
    if not isinstance(fields, list) or not fields:
        raise TaxonomyError("taxonomy.tag_fields phải là list không rỗng")
    tag_fields = tuple(str(value).strip() for value in fields if str(value).strip())
    if not tag_fields:
        raise TaxonomyError("taxonomy.tag_fields không có field hợp lệ")

    return {
        "accept_confidence": float(classification.get("accept_confidence", 0.80)),
        "candidate_confidence": float(classification.get("candidate_confidence", 0.55)),
        "category_field": str(taxonomy.get("category_field", "javis_category") or "javis_category"),
        "fallback_category_field": str(taxonomy.get("fallback_category_field", "category") or "category"),
        "max_text_probe_bytes": max_text,
        "max_frontmatter_bytes": int(classification.get("max_frontmatter_bytes", 65536)),
        "ambiguity_margin": ambiguity_margin,
        "tag_fields": tag_fields,
        "max_tags": int(tags.get("max_per_note", 6)),
    }


def taxonomy_policy_id(config: BrainOSConfig) -> str:
    payload = {
        "taxonomy_version": TAXONOMY_VERSION,
        "folders_registry": config.folders,
        "tags_registry": config.tags,
        "tag_aliases": config.tag_aliases,
        "folder_policy": config.core.get("folders") or {},
        "tag_policy": config.core.get("tags") or {},
        "taxonomy": config.core.get("taxonomy") or {},
        "classification_thresholds": config.core.get("classification") or {},
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _scope_for(item: BrainFile) -> str:
    if item.document_type == DocumentType.LIVING_NOTE:
        return "living_notes"
    if item.document_type in {
        DocumentType.REFERENCE_SOURCE,
        DocumentType.BINARY_SOURCE,
    }:
        return "knowledge"
    return ""


def _manual_mode(item: BrainFile) -> str:
    raw = (item.metadata or {}).get("classification") or {}
    if isinstance(raw, dict):
        return str(raw.get("manual_mode") or "auto").strip().casefold()
    return "auto"


def _explicit_category(
    probe: MarkdownProbe,
    registry: TaxonomyRegistry,
    scope: str,
    *,
    category_field: str,
    fallback_field: str,
) -> tuple[CategoryDefinition | None, str, list[str]]:
    warnings: list[str] = []
    metadata = probe.metadata

    if category_field in metadata:
        value = metadata.get(category_field)
        resolved = registry.resolve_category(scope, value)
        if resolved is not None:
            return resolved, category_field, warnings
        if str(value or "").strip():
            warnings.append(
                f"unknown_explicit_category:{category_field}={value!r}"
            )

    if fallback_field != category_field and fallback_field in metadata:
        value = metadata.get(fallback_field)
        resolved = registry.resolve_category(scope, value)
        if resolved is not None:
            return resolved, fallback_field, warnings
        # Generic `category` may belong to another plugin/workflow. Just like
        # Stage 4's generic `type`, Brain OS ignores unknown values instead of
        # claiming their semantics.

    return None, "", warnings


def _canonicalize_existing_tags(
    registry: TaxonomyRegistry,
    raw_tags: Iterable[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    canonical: list[str] = []
    legacy: list[str] = []
    seen_canonical: set[str] = set()
    seen_legacy: set[str] = set()

    for raw in raw_tags:
        target = registry.resolve_tag(raw)
        if target:
            if target not in seen_canonical:
                seen_canonical.add(target)
                canonical.append(target)
        else:
            cleaned = str(raw).strip().lstrip("#").strip()
            key = cleaned.casefold()
            if cleaned and key not in seen_legacy:
                seen_legacy.add(key)
                legacy.append(cleaned)
    return tuple(canonical), tuple(legacy)


def _add_score(
    scores: dict[str, int],
    reasons: dict[str, list[str]],
    category_id: str,
    points: int,
    reason: str,
) -> None:
    if points <= 0:
        return
    scores[category_id] = scores.get(category_id, 0) + points
    bucket = reasons.setdefault(category_id, [])
    if reason not in bucket:
        bucket.append(reason)


def _category_phrases(category: CategoryDefinition) -> tuple[str, ...]:
    values = [
        category.label,
        PurePosixPath(category.path).name,
        *category.aliases,
    ]
    return _unique(_ascii_text(value) for value in values if _ascii_text(value))


def _collect_category_scores(
    registry: TaxonomyRegistry,
    scope: str,
    probe: MarkdownProbe,
    canonical_existing_tags: tuple[str, ...],
) -> tuple[dict[str, int], dict[str, list[str]], dict[str, dict[str, Any]]]:
    scores: dict[str, int] = {}
    reasons: dict[str, list[str]] = {}
    tag_evidence: dict[str, dict[str, Any]] = {}

    title = _ascii_text(probe.title)
    headings = _ascii_text("\n".join(probe.headings))
    body = _ascii_text(probe.body_excerpt)

    # Existing user tags are strong deterministic evidence. Only the deepest
    # category supported by a canonical tag receives points, so a VAT tag
    # supports Accounting/Tax rather than creating a parent/child tie.
    for tag in canonical_existing_tags:
        for category in registry.categories_for_tag(scope, tag):
            _add_score(scores, reasons, category.id, 8, f"existing_tag:{tag}")

    for category in registry.categories_by_scope.get(scope, []):
        phrases = _category_phrases(category)
        if any(_phrase_count(title, phrase) for phrase in phrases):
            _add_score(scores, reasons, category.id, 6, "title_category_alias")
        if any(_phrase_count(headings, phrase) for phrase in phrases):
            _add_score(scores, reasons, category.id, 3, "heading_category_alias")

        body_hits = sum(min(_phrase_count(body, phrase), 2) for phrase in phrases)
        if body_hits >= 2:
            _add_score(scores, reasons, category.id, 2, "body_category_alias_repeated")
        elif body_hits == 1:
            _add_score(scores, reasons, category.id, 1, "body_category_alias")

    # Canonical tag aliases provide cross-cutting topic evidence. They are also
    # retained separately so Stage 5 can propose tags without writing them.
    for phrase, canonical in registry.tag_phrase_map.items():
        title_hits = _phrase_count(title, phrase)
        heading_hits = _phrase_count(headings, phrase)
        body_hits = _phrase_count(body, phrase)
        if not (title_hits or heading_hits or body_hits):
            continue

        evidence = tag_evidence.setdefault(
            canonical,
            {"tag": canonical, "confidence": 0.0, "reason_codes": []},
        )
        if title_hits:
            evidence["confidence"] = max(float(evidence["confidence"]), 0.92)
            evidence["reason_codes"].append(f"title:{phrase}")
            for category in registry.categories_for_tag(scope, canonical):
                _add_score(scores, reasons, category.id, 5, f"title_tag:{canonical}")
        if heading_hits:
            evidence["confidence"] = max(float(evidence["confidence"]), 0.82)
            evidence["reason_codes"].append(f"heading:{phrase}")
            for category in registry.categories_for_tag(scope, canonical):
                _add_score(scores, reasons, category.id, 2, f"heading_tag:{canonical}")
        if body_hits >= 2:
            evidence["confidence"] = max(float(evidence["confidence"]), 0.72)
            evidence["reason_codes"].append(f"body_repeated:{phrase}")
            for category in registry.categories_for_tag(scope, canonical):
                _add_score(scores, reasons, category.id, 2, f"body_tag_repeated:{canonical}")
        elif body_hits == 1:
            evidence["confidence"] = max(float(evidence["confidence"]), 0.58)
            evidence["reason_codes"].append(f"body:{phrase}")
            for category in registry.categories_for_tag(scope, canonical):
                _add_score(scores, reasons, category.id, 1, f"body_tag:{canonical}")

    return scores, reasons, tag_evidence


def _confidence_from_score(score: int) -> float:
    if score >= 12:
        return 0.96
    if score >= 8:
        return 0.90
    if score >= 6:
        return 0.84
    if score >= 4:
        return 0.72
    if score >= 2:
        return 0.60
    if score >= 1:
        return 0.45
    return 0.0


def _same_branch(a: CategoryDefinition, b: CategoryDefinition) -> bool:
    return (
        a.path == b.path
        or a.path.startswith(b.path + "/")
        or b.path.startswith(a.path + "/")
    )


def _rank_categories(
    registry: TaxonomyRegistry,
    scope: str,
    scores: dict[str, int],
    reasons: dict[str, list[str]],
    *,
    ambiguity_margin: int,
) -> tuple[
    CategoryDefinition | None,
    float,
    bool,
    tuple[dict[str, Any], ...],
]:
    candidates = [
        registry.categories[category_id]
        for category_id, score in scores.items()
        if score > 0 and registry.categories[category_id].scope == scope
    ]
    if not candidates:
        return None, 0.0, False, ()

    ranked = sorted(
        candidates,
        key=lambda category: (
            scores.get(category.id, 0),
            category.depth,
            category.path.casefold(),
        ),
        reverse=True,
    )
    top = ranked[0]
    top_score = scores[top.id]

    # Parent/child evidence is not a cross-domain conflict. The ambiguity check
    # compares only the strongest category on a different branch.
    competitor_score = 0
    competitor_id = ""
    for candidate in ranked[1:]:
        if _same_branch(top, candidate):
            continue
        competitor_score = scores[candidate.id]
        competitor_id = candidate.id
        break

    ambiguous = bool(
        competitor_id
        and top_score - competitor_score < ambiguity_margin
    )
    confidence = _confidence_from_score(top_score)

    ranking = tuple(
        {
            "category_id": category.id,
            "path": category.path,
            "score": scores[category.id],
            "confidence": _confidence_from_score(scores[category.id]),
            "reason_codes": list(reasons.get(category.id, [])),
        }
        for category in ranked[:8]
    )
    return top, confidence, ambiguous, ranking


def _root_for_path(registry: TaxonomyRegistry, scope: str, path: str) -> str:
    for root in registry.roots_for(scope):
        if is_under(path, root):
            return root
    roots = registry.roots_for(scope)
    return roots[0] if roots else ""


def _join_rel(*parts: str) -> str:
    clean = [part.strip("/") for part in parts if part and part.strip("/")]
    return PurePosixPath(*clean).as_posix() if clean else ""


def _would_move_path(
    item: BrainFile,
    *,
    target_directory: str,
) -> str:
    if not target_directory:
        return ""
    current_parent = PurePosixPath(item.path).parent.as_posix()
    if current_parent == ".":
        current_parent = ""
    if current_parent and is_under(current_parent, target_directory):
        return ""
    return _join_rel(target_directory, PurePosixPath(item.path).name)


def _build_tag_plan(
    registry: TaxonomyRegistry,
    *,
    category: CategoryDefinition | None,
    accepted_category: bool,
    canonical_existing: tuple[str, ...],
    legacy_tags: tuple[str, ...],
    tag_evidence: dict[str, dict[str, Any]],
    candidate_confidence: float,
    max_tags: int,
) -> tuple[tuple[str, ...], tuple[dict[str, Any], ...], list[str]]:
    warnings: list[str] = []
    evidence = {
        tag: {
            "tag": tag,
            "confidence": float(payload.get("confidence", 0.0)),
            "reason_codes": list(dict.fromkeys(payload.get("reason_codes") or [])),
        }
        for tag, payload in tag_evidence.items()
        if tag in registry.tags
    }

    for tag in canonical_existing:
        payload = evidence.setdefault(
            tag,
            {"tag": tag, "confidence": 1.0, "reason_codes": []},
        )
        payload["confidence"] = 1.0
        if "existing_frontmatter_tag" not in payload["reason_codes"]:
            payload["reason_codes"].insert(0, "existing_frontmatter_tag")

    if accepted_category and category is not None:
        default_tag = registry.default_tag_for_category(category)
        if default_tag:
            payload = evidence.setdefault(
                default_tag,
                {"tag": default_tag, "confidence": 0.88, "reason_codes": []},
            )
            payload["confidence"] = max(float(payload["confidence"]), 0.88)
            if "accepted_category" not in payload["reason_codes"]:
                payload["reason_codes"].append("accepted_category")

    if len(canonical_existing) > max_tags:
        warnings.append(
            f"existing_canonical_tags_exceed_limit:{len(canonical_existing)}>{max_tags}; preserve_user_tags"
        )

    existing_set = set(canonical_existing)
    selectable = [
        payload
        for payload in evidence.values()
        if payload["tag"] in existing_set
        or float(payload["confidence"]) >= candidate_confidence
    ]
    selectable.sort(
        key=lambda payload: (
            float(payload["confidence"]),
            payload["tag"].count("/"),
            payload["tag"],
        ),
        reverse=True,
    )

    # Preserve every user-owned canonical tag. The max limit applies only to
    # Brain OS additions; Stage 5 never suggests deleting user metadata.
    proposed: list[str] = list(canonical_existing)
    remaining = max(0, max_tags - len(proposed))

    for payload in selectable:
        tag = str(payload["tag"])
        if tag in existing_set or tag in proposed:
            continue
        if remaining <= 0:
            break

        # If a more specific suggested tag already exists, do not add its
        # ancestor automatically. This keeps hierarchical tags informative
        # without producing tag explosion.
        if any(other.startswith(tag + "/") for other in proposed):
            continue
        descendants = [
            other
            for other in selectable
            if str(other["tag"]).startswith(tag + "/")
            and float(other["confidence"]) >= float(payload["confidence"])
        ]
        if descendants:
            continue

        proposed.append(tag)
        remaining -= 1

    suggestions = tuple(
        {
            "tag": payload["tag"],
            "confidence": round(float(payload["confidence"]), 4),
            "reason_codes": list(payload["reason_codes"]),
            "existing": payload["tag"] in existing_set,
        }
        for payload in selectable
    )
    return tuple(proposed), suggestions, warnings


def plan_taxonomy_for_file(
    config: BrainOSConfig,
    item: BrainFile,
    *,
    registry: TaxonomyRegistry | None = None,
) -> TaxonomyDecision:
    """Plan folder category + canonical tags without modifying the source file."""

    registry = registry or TaxonomyRegistry.from_config(config)
    opts = _taxonomy_options(config)
    scope = _scope_for(item)
    manual_mode = _manual_mode(item)

    if item.state == ProcessingState.MISSING:
        return TaxonomyDecision(
            applicable=False,
            scope=scope,
            status="missing",
            accepted=False,
            candidate=False,
            ambiguous=False,
            confidence=0.0,
            category_id=item.category_id,
            proposed_category_id="",
            current_location_category_id="",
            target_directory="",
            fallback_directory="",
            would_move_to="",
            canonical_existing_tags=(),
            proposed_tags=(),
            legacy_tags=(),
            tag_suggestions=(),
            ranking=(),
            reason_codes=("missing_record",),
            warnings=(),
        )

    if not scope:
        return TaxonomyDecision(
            applicable=False,
            scope="",
            status="not_applicable",
            accepted=False,
            candidate=False,
            ambiguous=False,
            confidence=0.0,
            category_id=item.category_id,
            proposed_category_id="",
            current_location_category_id="",
            target_directory="",
            fallback_directory="",
            would_move_to="",
            canonical_existing_tags=(),
            proposed_tags=(),
            legacy_tags=(),
            tag_suggestions=(),
            ranking=(),
            reason_codes=(f"document_type:{item.document_type.value}",),
            warnings=(),
        )

    if manual_mode == "ignore":
        return TaxonomyDecision(
            applicable=False,
            scope=scope,
            status="manual_ignore",
            accepted=False,
            candidate=False,
            ambiguous=False,
            confidence=0.0,
            category_id=item.category_id,
            proposed_category_id="",
            current_location_category_id="",
            target_directory="",
            fallback_directory="",
            would_move_to="",
            canonical_existing_tags=(),
            proposed_tags=(),
            legacy_tags=(),
            tag_suggestions=(),
            ranking=(),
            reason_codes=("manual_mode:ignore",),
            warnings=(),
        )

    absolute = config.brain_root / item.path
    probe = read_markdown_probe(
        absolute,
        max_frontmatter_bytes=int(opts["max_frontmatter_bytes"]),
        max_text_probe_bytes=int(opts["max_text_probe_bytes"]),
        tag_fields=opts["tag_fields"],
    )
    canonical_existing, legacy_tags = _canonicalize_existing_tags(
        registry, probe.raw_tags
    )
    warnings = list(probe.warnings)

    location_category, current_root = registry.category_for_location(scope, item.path)
    explicit, explicit_field, explicit_warnings = _explicit_category(
        probe,
        registry,
        scope,
        category_field=str(opts["category_field"]),
        fallback_field=str(opts["fallback_category_field"]),
    )
    warnings.extend(explicit_warnings)

    scores, score_reasons, tag_evidence = _collect_category_scores(
        registry,
        scope,
        probe,
        canonical_existing,
    )
    ranked_category, ranked_confidence, ambiguous, ranking = _rank_categories(
        registry,
        scope,
        scores,
        score_reasons,
        ambiguity_margin=int(opts["ambiguity_margin"]),
    )

    selected: CategoryDefinition | None = None
    proposed: CategoryDefinition | None = ranked_category
    confidence = ranked_confidence
    accepted = False
    candidate = False
    status = "unsorted"
    reasons: list[str] = []

    # User-visible location is the strongest stable signal. If frontmatter still
    # contains an older category after a manual move, do not drag the note back.
    if location_category is not None:
        selected = location_category
        proposed = location_category
        confidence = 1.0
        accepted = True
        ambiguous = False
        status = "location_locked"
        reasons.append(f"current_location:{location_category.id}")
        if explicit is not None and explicit.id != location_category.id:
            warnings.append(
                f"explicit_category_conflicts_with_location:{explicit.id}->{location_category.id}; location_wins"
            )
    elif explicit is not None:
        selected = explicit
        proposed = explicit
        confidence = 1.0
        accepted = True
        ambiguous = False
        status = "explicit_category"
        reasons.append(f"frontmatter:{explicit_field}")
    elif item.category_id and item.category_id in registry.categories:
        previous = registry.categories[item.category_id]
        if previous.scope == scope:
            # Category state is derived, but preserving a previously accepted
            # home prevents Living Notes/reference sources from thrashing between
            # folders when a few new lines shift keyword frequencies.
            selected = previous
            proposed = previous
            confidence = 0.90
            accepted = True
            ambiguous = False
            status = "stable_existing_category"
            reasons.append(f"previous_category:{previous.id}")
    elif ranked_category is not None:
        candidate = ranked_confidence >= float(opts["candidate_confidence"])
        if ambiguous:
            status = "ambiguous_unsorted"
            reasons.append("cross_branch_ambiguity")
        elif ranked_confidence >= float(opts["accept_confidence"]):
            selected = ranked_category
            accepted = True
            status = "accepted_proposal"
            reasons.extend(score_reasons.get(ranked_category.id, []))
        elif candidate:
            status = "candidate_unsorted"
            reasons.extend(score_reasons.get(ranked_category.id, []))
        else:
            status = "unsorted"
            reasons.extend(score_reasons.get(ranked_category.id, []))
    else:
        reasons.append("no_taxonomy_signal")

    root = current_root or _root_for_path(registry, scope, item.path)
    fallback_directory = _join_rel(root, registry.fallback_for(scope))
    target_directory = fallback_directory
    would_move_to = ""
    category_id = ""

    if accepted and selected is not None:
        category_id = selected.id
        target_directory = _join_rel(root, selected.path)
        would_move_to = _would_move_path(item, target_directory=target_directory)

    proposed_tags, tag_suggestions, tag_warnings = _build_tag_plan(
        registry,
        category=selected,
        accepted_category=accepted,
        canonical_existing=canonical_existing,
        legacy_tags=legacy_tags,
        tag_evidence=tag_evidence,
        candidate_confidence=float(opts["candidate_confidence"]),
        max_tags=int(opts["max_tags"]),
    )
    warnings.extend(tag_warnings)

    if not reasons:
        reasons.append("taxonomy_evidence")

    return TaxonomyDecision(
        applicable=True,
        scope=scope,
        status=status,
        accepted=accepted,
        candidate=candidate or (accepted and selected is not None),
        ambiguous=ambiguous,
        confidence=round(float(confidence), 4),
        category_id=category_id,
        proposed_category_id=proposed.id if proposed is not None else "",
        current_location_category_id=(
            location_category.id if location_category is not None else ""
        ),
        target_directory=target_directory,
        fallback_directory=fallback_directory,
        would_move_to=would_move_to,
        canonical_existing_tags=canonical_existing,
        proposed_tags=proposed_tags,
        legacy_tags=legacy_tags,
        tag_suggestions=tag_suggestions,
        ranking=ranking,
        reason_codes=tuple(reasons),
        warnings=tuple(warnings),
    )


def _list_files(index: BrainIndex) -> list[BrainFile]:
    if index.conn is None:
        return []
    rows = index.conn.execute("SELECT source_id FROM files ORDER BY path").fetchall()
    result: list[BrainFile] = []
    for row in rows:
        item = index.get_file(str(row["source_id"]))
        if item is not None:
            result.append(item)
    return result


def _taxonomy_cache_valid(
    item: BrainFile,
    *,
    policy_id: str,
) -> bool:
    raw = (item.metadata or {}).get("taxonomy") or {}
    return bool(
        isinstance(raw, dict)
        and int(raw.get("taxonomy_version", 0) or 0) == TAXONOMY_VERSION
        and str(raw.get("policy_id", "")) == policy_id
        and str(raw.get("content_hash", "")) == item.content_hash
        and str(raw.get("path", "")) == item.path
        and str(raw.get("document_type", "")) == item.document_type.value
        and str(raw.get("committed_category_id", "")) == item.category_id
    )


def plan_brain_taxonomy(
    config: BrainOSConfig,
    *,
    force: bool = False,
    paths: set[str] | None = None,
) -> TaxonomyReport:
    """Persist only a dry-run taxonomy plan in the rebuildable SQLite index."""

    if not config.db_path.is_file():
        return TaxonomyReport(initialized=False)

    registry = TaxonomyRegistry.from_config(config)
    policy_id = taxonomy_policy_id(config)
    normalized_paths = {
        str(PurePosixPath(path.replace("\\", "/")))
        for path in (paths or set())
    }
    report = TaxonomyReport()

    with BrainIndex(config.db_path) as index:
        for item in _list_files(index):
            if normalized_paths and item.path not in normalized_paths:
                report.path_filtered_out += 1
                continue

            report.scanned_records += 1
            if item.state == ProcessingState.MISSING:
                report.missing += 1
                continue
            if not (config.brain_root / item.path).is_file():
                report.missing += 1
                report.warnings.append(
                    f"{item.path}: file không tồn tại; bỏ taxonomy planning"
                )
                continue
            if not force and _taxonomy_cache_valid(item, policy_id=policy_id):
                report.cached += 1
                continue

            decision = plan_taxonomy_for_file(
                config,
                item,
                registry=registry,
            )
            report.analyzed += 1

            metadata = dict(item.metadata or {})
            payload = decision.to_dict()
            committed_category_id = (
                decision.category_id if decision.accepted else item.category_id
            )
            payload.update(
                {
                    "taxonomy_version": TAXONOMY_VERSION,
                    "policy_id": policy_id,
                    "content_hash": item.content_hash,
                    "path": item.path,
                    "document_type": item.document_type.value,
                    "committed_category_id": committed_category_id,
                    "planned_at": utc_now(),
                    "dry_run": True,
                    "writes_user_files": False,
                }
            )
            metadata["taxonomy"] = payload

            index.upsert_file(
                replace(
                    item,
                    category_id=committed_category_id,
                    metadata=metadata,
                    updated_at="",
                )
            )

            report.status_counts[decision.status] = (
                report.status_counts.get(decision.status, 0) + 1
            )
            if not decision.applicable:
                report.not_applicable += 1
            if decision.accepted:
                report.accepted += 1
                report.category_counts[decision.category_id] = (
                    report.category_counts.get(decision.category_id, 0) + 1
                )
            elif decision.candidate:
                report.candidates += 1
            if decision.ambiguous:
                report.ambiguous += 1
            if decision.applicable and not decision.accepted:
                report.unsorted += 1
            if decision.would_move_to:
                report.would_move += 1
                if len(report.would_move_paths) < 50:
                    report.would_move_paths.append(
                        {
                            "from": item.path,
                            "to": decision.would_move_to,
                            "category_id": decision.category_id,
                        }
                    )
            report.tag_suggestions += sum(
                1
                for suggestion in decision.tag_suggestions
                if not bool(suggestion.get("existing"))
            )
            report.warnings.extend(
                f"{item.path}: {warning}" for warning in decision.warnings
            )

    report.status_counts = dict(sorted(report.status_counts.items()))
    report.category_counts = dict(sorted(report.category_counts.items()))
    return report


def list_taxonomy_plans(
    config: BrainOSConfig,
    *,
    would_move_only: bool = False,
    unresolved_only: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if not config.db_path.is_file():
        return []

    rows: list[dict[str, Any]] = []
    with BrainIndex(config.db_path) as index:
        for item in _list_files(index):
            raw = (item.metadata or {}).get("taxonomy") or {}
            if not isinstance(raw, dict) or not raw:
                continue
            if would_move_only and not str(raw.get("would_move_to") or ""):
                continue
            if unresolved_only and bool(raw.get("accepted", False)):
                continue
            rows.append(
                {
                    "source_id": item.source_id,
                    "path": item.path,
                    "document_type": item.document_type.value,
                    "category_id": item.category_id,
                    "taxonomy": raw,
                }
            )
            if len(rows) >= max(1, min(int(limit), 500)):
                break
    return rows
