"""Kiểm tra ba lớp Visual Brain và node tag ảo, không cần mạng."""
from _paths import ROOT, SERVER  # noqa: E402,F401

import tempfile
from pathlib import Path

from graph_builder import build_graph, _frontmatter_tags


_fails = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        _fails.append(name)


root = Path(tempfile.mkdtemp(prefix="javis-graph-topics-"))


def write(name, body):
    (root / name).write_text(body, encoding="utf-8")


write("A.md", "---\ntags: [wiki, ai, workflow]\n---\nNối [[B]]")
write("B.md", "---\ntags:\n  - wiki\n  - AI\n  - riêng\n---\nNối [[A]]")
write("C.md", "---\ntags: [wiki, content]\n---\nĐộc lập")
write("_catalog.md", "[[A]]\n[[B]]\n[[C]]")
write("index.md", "[[A]]")

check("đọc tag YAML một dòng",
      _frontmatter_tags("---\ntags: [wiki, ai, workflow]\n---\n") == ["wiki", "ai", "workflow"])
check("đọc tag YAML nhiều dòng và bỏ trùng không phân biệt hoa thường",
      _frontmatter_tags("---\ntags:\n - AI\n - ai\n---\n") == ["AI"])

all_graph = build_graph([str(root)], include_orphans=True)
all_labels = {n["label"] for n in all_graph["nodes"]}
check("graph toàn bộ loại catalog, index và file hệ thống", all_labels == {"A", "B", "C"})
check("catalog không tạo siêu cạnh", len(all_graph["edges"]) == 1)

topics = build_graph([str(root)], include_orphans=True, include_tag_nodes=True)
topic_nodes = [n for n in topics["nodes"] if n.get("kind") == "tag"]
check("chỉ tạo node cho tag nối ít nhất hai note",
      {(n["tag"].casefold(), n["links"]) for n in topic_nodes} == {("ai", 2)})
check("tag phân loại wiki không thành node", all(n.get("tag") != "wiki" for n in topic_nodes))
check("cạnh giữ được loại wikilink và tag",
      {e.get("kind") for e in topics["edges"]} == {"wikilink", "tag"})
check("thống kê tách note và chủ đề",
      topics["stats"]["note_nodes"] == 3 and topics["stats"]["tag_nodes"] == 1)

print()
if _fails:
    print(f"FAIL {len(_fails)}: " + ", ".join(_fails))
    raise SystemExit(1)
print("TẤT CẢ PASS")
