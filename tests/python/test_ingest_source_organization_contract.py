"""Regression contract for Gate 1 Second Brain source organization.

Chạy tay / CI:

    python tests/python/test_ingest_source_organization_contract.py

Test này cố ý KHÔNG giả lập chất lượng semantic của model. Nó khoá những kỷ luật bắt buộc
mà `ingest-source` phải mang theo: giữ nguyên body, một folder chính, reuse/create taxonomy,
tag có kiểm soát, collision-safe move và re-ingest bảo thủ.
"""
from _paths import ROOT  # noqa: E402
import sys


SKILL = ROOT / ".claude" / "skills" / "ingest-source" / "SKILL.md"
_fails = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        _fails.append(name)


def main():
    text = SKILL.read_text(encoding="utf-8")
    low = text.lower()

    # 1. Scope/ordering: organization is part of INGEST, before wiki distillation.
    heading = "## Kỷ luật tổ chức source trước khi tiêu hoá"
    steps = "## Các bước"
    check("1. co section ky luat to chuc source", heading in text)
    check("1b. ky luat nam truoc cac buoc ingest", text.find(heading) < text.find(steps))
    check("1c. chi ap dung quan ly source markdown", "source Markdown" in text)

    # 2. Original-content preservation and metadata safety.
    check("2. khoa giu nguyen than source",
          "Thân source của người dùng là dữ liệu gốc" in text and "KHÔNG tóm tắt" in text)
    check("2b. giu metadata khac cua user", "giữ nguyên mọi metadata khác của người dùng" in text)
    check("2c. giu tag user da dat", "Giữ các tag người dùng đã đặt" in text)

    # 3. Folder taxonomy: exactly one primary home, reuse first, create when needed.
    check("3. mot folder chinh", "MỘT folder chính" in text and "một vị trí chính" in text)
    check("3b. reuse folder co san", "Ưu tiên tái sử dụng nhánh hiện có" in text)
    check("3c. duoc tao folder moi", "ĐƯỢC tạo folder mới" in text)
    check("3d. khong dung unsorted lam mac dinh", "Không dùng `_Unsorted` như đường tắt mặc định" in text)
    check("3e. tranh synonym duplicate", "từ đồng nghĩa" in text and "Đừng tạo nhánh mới" in text)
    check("3f. tranh over-classify", "3-4 tầng" in text and "tránh over-classify" in text)
    check("3g. note da chu de khong bi nhan ban", "KHÔNG nhân bản cùng source vào nhiều folder" in text)

    # 4. Tags: reuse vocabulary, useful count, no duplicates, frontmatter only.
    check("4. doc tags hien co", "Đọc các `tags:` đang dùng" in text)
    check("4b. khoang 3-5 tag", "3-5 tag" in text)
    check("4c. tag khong lap folder", "Không cần lặp nguyên đường folder thành tag" in text)
    check("4d. tag moi co convention on dinh", "`kebab-case`" in text and "không dấu" in text)
    check("4e. tags nam trong frontmatter", "Ghi tag trong frontmatter `tags:`" in text)

    # 5. Move safety: no overwrite and deterministic collision suffix.
    check("5. cam overwrite", "TUYỆT ĐỐI KHÔNG overwrite" in text)
    check("5b. collision suffix an toan", "`ten-2.md`" in text and "`ten-3.md`" in text)
    check("5c. citation dung ten cuoi", "không để link trỏ tên cũ" in text)

    # 6. Re-ingest stability: no taxonomy churn/mass moves.
    check("6. reingest khong tu move", "re-ingest KHÔNG tự động chuyển folder" in text)
    check("6b. restructure phai de xuat", "đề xuất cho người dùng duyệt" in text)
    check("6c. khong mass edit taxonomy", "Không tự đổi tên/gộp/xoá hàng loạt folder hoặc tag" in text)

    # 7. Preserve the existing Javis Wiki compounding behavior.
    check("7. van dung wiki index", "`wiki/index.md`" in text)
    check("7b. van co citation cung", "Citation cứng" in text)
    check("7c. van ghi wiki links", "`wiki_links: [...]`" in text)
    check("7d. processed gate van con", "`status: processed` -> DỪNG" in text)

    # Guard against accidentally turning Gate 1 into Gate 2/Drive work.
    check("8. gate1 chua dua hash guard vao skill", "javis_last_ingested_hash" not in low)
    check("8b. gate1 chua dua drive persistence vao skill", "drive_file_id" not in low)

    print()
    if _fails:
        print(f"{len(_fails)} test FAIL: " + ", ".join(_fails))
        sys.exit(1)
    print("Tat ca test PASS")


if __name__ == "__main__":
    main()
