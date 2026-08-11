"""
Graph builder - quét các file markdown, parse [[wikilink]], dựng đồ thị kết nối.
Đây là lớp "Graphify" - visualize mạng lưới note như Obsidian graph view.
"""
import os
import re
import glob
from pathlib import Path
from typing import List, Dict

# Match [[Note]] và [[folder/Note|alias]]
WIKILINK_RE = re.compile(r"\[\[([^\]\|#]+)(?:[#\|][^\]]*)?\]\]")
INLINE_TAGS_RE = re.compile(r"^tags\s*:\s*\[(.*)\]\s*$", re.IGNORECASE)
BLOCK_TAGS_RE = re.compile(r"^tags\s*:\s*$", re.IGNORECASE)
LIST_ITEM_RE = re.compile(r"^\s*-\s*(.+?)\s*$")

# Palette tinh vân tím (như V.A.U.L.T) - tím chủ đạo + vài tông phụ, lõi trắng nóng
FOLDER_COLORS = {
    "00": "#c77dff", "01": "#a96bff", "02": "#7c5cff", "03": "#d98cff",
    "04": "#8a9bff", "05": "#e07ad1", "06": "#b07aff", "07": "#9b8cff",
    "08": "#c9a3ff", "brain": "#c77dff", "wiki": "#7c5cff",
}

def _color_for(rel_path: str) -> str:
    top = rel_path.split("/")[0].lower()
    for key, color in FOLDER_COLORS.items():
        if top.startswith(key):
            return color
    return "#9d7aff"  # tím mặc định

def _top_folder(rel_path: str) -> str:
    parts = rel_path.split("/")
    return parts[0] if len(parts) > 1 else "root"


def _knowledge_file_visible(fpath: str, root: str) -> bool:
    """Loại trang điều hành khỏi sơ đồ tri thức mà không ảnh hưởng dữ liệu trên đĩa."""
    try:
        rel = Path(fpath).relative_to(root)
    except ValueError:
        rel = Path(fpath)
    if any(part.startswith("_") for part in rel.parts):
        return False
    return rel.name.casefold() not in {"index.md", "log.md"}


def _graph_file_visible(fpath: str, root: str) -> bool:
    """Loại file điều hành và danh mục máy sinh khỏi mọi chế độ sơ đồ.

    Các file vẫn còn nguyên trên đĩa và vẫn dùng được trong Obsidian. Chúng chỉ không được
    coi là node tri thức, vì liên kết liệt kê trong catalog/index làm sai hình dạng mạng.
    """
    try:
        rel = Path(fpath).relative_to(root)
    except ValueError:
        rel = Path(fpath)
    if any(part.startswith(".") for part in rel.parts):
        return False
    name = rel.name.casefold()
    return not (name.startswith("_") or name in {"index.md", "log.md"})


def _frontmatter_tags(content: str) -> List[str]:
    """Đọc tags YAML dạng danh sách trên một dòng hoặc nhiều dòng.

    Chỉ đọc frontmatter đầu file, không coi hashtag trong thân bài là metadata. Cách này giữ
    tương thích với Obsidian mà không cần kéo thêm một YAML parser vào đường nóng của graph.
    """
    if not content.startswith("---"):
        return []
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    tags = []
    reading_block = False
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        inline = INLINE_TAGS_RE.match(stripped)
        if inline:
            reading_block = False
            raw = inline.group(1)
            tags.extend(part.strip().strip("'\"") for part in raw.split(","))
            continue
        if BLOCK_TAGS_RE.match(stripped):
            reading_block = True
            continue
        if reading_block:
            item = LIST_ITEM_RE.match(line)
            if item:
                tags.append(item.group(1).strip().strip("'\""))
                continue
            if stripped and not line[:1].isspace():
                reading_block = False
    clean = []
    seen = set()
    for tag in tags:
        tag = tag.strip().lstrip("#").strip()
        key = tag.casefold()
        if tag and key not in seen:
            seen.add(key)
            clean.append(tag)
    return clean


def build_graph(roots: List[str], max_files: int = 2000, include_orphans: bool = False,
                knowledge_only: bool = False, include_tag_nodes: bool = False) -> Dict:
    """
    Quét nhiều thư mục root, dựng graph.
    roots: list các đường dẫn thư mục chứa .md
    Trả về: {nodes: [...], edges: [...], stats: {...}}
    """
    nodes = {}          # stem (lowercase) -> node dict
    stem_to_id = {}     # stem lowercase -> node id
    edges = []
    file_count = 0

    # Pass 1: thu thập tất cả file -> tạo node
    all_files = []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        root_name = Path(root).name
        for fpath in glob.glob(f"{root}/**/*.md", recursive=True):
            if not _graph_file_visible(fpath, root):
                continue
            if knowledge_only and not _knowledge_file_visible(fpath, root):
                continue
            if file_count >= max_files:
                break
            try:
                rel = Path(fpath).relative_to(root).as_posix()
            except ValueError:
                rel = Path(fpath).name
            stem = Path(fpath).stem
            key = stem.lower()
            node_id = key
            if node_id in stem_to_id:
                continue  # trùng tên -> bỏ qua bản sau
            stem_to_id[key] = node_id
            # Mốc "note ra đời" cho timelapse: birthtime (macOS) > ctime (Windows = creation) >
            # mtime; lấy min với mtime vì file copy/sync có thể mang mtime gốc cũ hơn ctime.
            try:
                st = os.stat(fpath)
                born = min(getattr(st, "st_birthtime", st.st_ctime), st.st_mtime)
            except OSError:
                born = 0
            nodes[node_id] = {
                "id": node_id,
                "label": stem,
                "folder": _top_folder(rel),
                "color": _color_for(rel),
                "path": f"{root_name}/{rel}",
                "fullpath": fpath,
                "links": 0,
                "t": born,
                "kind": "note",
                "tags": [],
            }
            all_files.append((node_id, fpath))
            file_count += 1

    # Pass 2: parse wikilink -> tạo edge
    for node_id, fpath in all_files:
        try:
            content = Path(fpath).read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        nodes[node_id]["tags"] = _frontmatter_tags(content)
        for match in WIKILINK_RE.finditer(content):
            target_raw = match.group(1).strip()
            target_stem = target_raw.split("/")[-1].strip().lower()
            if target_stem in stem_to_id and target_stem != node_id:
                edges.append({"source": node_id, "target": stem_to_id[target_stem], "kind": "wikilink"})
                nodes[node_id]["links"] += 1
                nodes[stem_to_id[target_stem]]["links"] += 1

    # Chế độ Chủ đề: tag là node ảo, không tạo file Markdown và không làm bẩn Obsidian.
    # Tag `wiki` chỉ mô tả loại tài liệu nên không có giá trị khám phá, vì vậy loại khỏi graph.
    if include_tag_nodes:
        tag_nodes = {}
        tag_edges = []
        for note_id, note in list(nodes.items()):
            for raw_tag in note.get("tags", []):
                tag_key = raw_tag.casefold()
                if tag_key == "wiki":
                    continue
                tag_id = f"tag:{tag_key}"
                if tag_id not in tag_nodes:
                    tag_nodes[tag_id] = {
                        "id": tag_id,
                        "label": f"#{raw_tag}",
                        "folder": "Chủ đề",
                        "color": "#f0c853",
                        "path": "",
                        "fullpath": "",
                        "links": 0,
                        "t": 0,
                        "kind": "tag",
                        "tag": raw_tag,
                        "tags": [],
                    }
                tag_edges.append({"source": note_id, "target": tag_id, "kind": "tag"})
                tag_nodes[tag_id]["links"] += 1
        # Tag chỉ gắn một trang không tạo ra một cụm và làm graph nhiễu. Giữ tag có ít nhất
        # hai trang Wiki, đúng vai trò kết nối chủ đề mà người dùng có thể khám phá.
        useful_tags = {tag_id for tag_id, tag_node in tag_nodes.items() if tag_node["links"] >= 2}
        for edge in tag_edges:
            if edge["target"] in useful_tags:
                edges.append(edge)
                nodes[edge["source"]]["links"] += 1
        nodes.update({tag_id: tag_nodes[tag_id] for tag_id in useful_tags})

    # Loại edge trùng
    seen = set()
    unique_edges = []
    for e in edges:
        k = (e.get("kind", "wikilink"), *sorted([e["source"], e["target"]]))
        if k not in seen:
            seen.add(k)
            unique_edges.append(e)

    orphan_count = len([n for n in nodes.values() if n["links"] == 0])

    # Giữ node: mặc định bỏ note cô đơn (0 kết nối); include_orphans=True thì giữ HẾT (như Obsidian).
    if include_orphans:
        keep = set(nodes.keys())
    else:
        keep = {nid for nid, n in nodes.items() if n["links"] > 0}
        if not keep:
            keep = set(nodes.keys())
    node_list = [n for nid, n in nodes.items() if nid in keep]
    edge_list = [e for e in unique_edges if e["source"] in keep and e["target"] in keep]

    # Đếm concept theo nhóm (folder cha trực tiếp của file) - cho nhãn HUD
    from collections import Counter
    cat_counter = Counter()
    for n in node_list:
        if n.get("kind") == "tag":
            cat_counter["Chủ đề"] += 1
            continue
        segs = n["path"].split("/")
        cat = segs[-2] if len(segs) >= 2 else "root"
        cat = re.sub(r"^\d+\s*[-_.]\s*", "", cat).strip()  # bỏ tiền tố "07 - "
        if cat:
            cat_counter[cat] += 1
    categories = [{"name": c, "count": cnt} for c, cnt in cat_counter.most_common(8)]

    return {
        "nodes": node_list,
        "edges": edge_list,
        "categories": categories,
        "stats": {
            "total_notes": len(node_list),
            "total_links": len(edge_list),
            "note_nodes": len([n for n in node_list if n.get("kind") == "note"]),
            "tag_nodes": len([n for n in node_list if n.get("kind") == "tag"]),
            "orphans": orphan_count,
            "hidden": len(nodes) - len(node_list),
        }
    }
