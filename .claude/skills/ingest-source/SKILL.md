---
name: Ingest Source
description: Tiêu hoá một source thô vào Second Brain, chưng cất thành tri thức wiki tích luỹ.
description_en: "Digest one raw source into the Second Brain and distil it into wiki knowledge that compounds."
group: AI
---

# INGEST - tiêu hoá 1 source thành wiki (compounding)

## Khi nào dùng

Kích hoạt khi người dùng nói những câu như: "tiêu hoá source này", "xử lý bài này vào
wiki", "đọc file này rồi ghi lại kiến thức", hoặc khi có file mới thả vào `sources/`.

Skill làm theo đúng 3 kỷ luật của vault, đồng thời tổ chức source theo cây thư mục + hashtag
để con người duyệt được bằng Obsidian mà không phụ thuộc vào Javis.

Đọc schema vault (`CLAUDE.md`/`AGENTS.md` ở gốc brain) trước; đây là bản thao tác của phép INGEST.

## Trước khi làm
- Kiểm frontmatter source: `status: processed` -> DỪNG, báo đã xử lý, hỏi có re-ingest không. `unprocessed`/chưa có -> làm.
- Phân loại độ dài. Source dài (>= ~10.000 dòng / sách / transcript) -> BẮT BUỘC 3-pass:
  1. Đọc lướt, lập mục lục theo số dòng (vd "1-1300: giới thiệu"). Báo người dùng xác nhận trọng tâm.
  2. Đọc sâu từng đoạn ~1.000-1.500 dòng, viết wiki NGAY từng đoạn (đừng nén cả file 1 lần - mất 25-40% chi tiết).
  3. Tự hỏi 5 câu về các vùng khác nhau; wiki không trả lời được câu nào -> quét bổ sung vùng đó.

## Kỷ luật tổ chức source trước khi tiêu hoá

Áp dụng cho source Markdown người dùng yêu cầu tiêu hoá. Mục tiêu là MỘT vị trí chính trong
cây `sources/` + một số hashtag liên quan; đây là lớp tổ chức cho con người, KHÔNG thay thế
`[[wikilink]]` của mạng tri thức.

### 1. Giữ nguyên nội dung nguồn
- Thân source của người dùng là dữ liệu gốc: KHÔNG tóm tắt, viết lại, cắt bớt hay thay câu chữ khi phân loại.
- Chỉ được merge/cập nhật frontmatter cần cho quản lý source; giữ nguyên mọi metadata khác của người dùng nếu không có lý do bắt buộc phải đổi.
- Giữ các tag người dùng đã đặt; chỉ bổ sung/chuẩn hoá khi chắc chắn không làm mất ý nghĩa.

### 2. Chọn đúng MỘT folder chính
- Trước khi chọn, đọc cây thư mục hiện có bên dưới `sources/`; hiểu tên các nhánh cha/con thay vì chỉ nhìn tên folder cuối.
- Ưu tiên tái sử dụng nhánh hiện có có cùng nghĩa hoặc đủ phù hợp. Đừng tạo nhánh mới chỉ vì khác cách viết, ngôn ngữ, số ít/số nhiều hay từ đồng nghĩa.
- Nếu chưa có folder phù hợp thì ĐƯỢC tạo folder mới theo chủ đề thực sự của source. Không dùng `_Unsorted` như đường tắt mặc định.
- Cây được phép phát triển theo tri thức thực tế nhưng tránh over-classify: thông thường giữ khoảng 3-4 tầng phân loại có nghĩa tính từ `sources/`; chỉ sâu hơn khi cây hiện hữu và nội dung thực sự đòi hỏi.
- Nếu source đa chủ đề, chọn nơi ở chính theo mục đích/nội dung trung tâm; các quan hệ phụ đi vào tag và wiki link, KHÔNG nhân bản cùng source vào nhiều folder.
- Nếu bằng chứng chưa đủ để tạo một nhánh hẹp, đặt source vào nhánh rộng hợp lý nhất thay vì bịa một taxonomy quá chi tiết.

### 3. Hashtag có kiểm soát
- Đọc các `tags:` đang dùng trong source hiện có trước khi tạo tag mới; ưu tiên từ vựng đã có của vault.
- Thông thường dùng khoảng 3-5 tag có giá trị tìm kiếm xuyên cây. Đây là hướng dẫn chất lượng, không phải quota để nhồi tag.
- Không tạo hai tag chỉ khác hoa/thường, dấu cách/gạch nối, ngôn ngữ hoặc từ đồng nghĩa nếu chúng cùng một khái niệm trong vault.
- Không cần lặp nguyên đường folder thành tag. Ưu tiên tag bổ sung các chiều liên quan như đối tượng, vấn đề, công nghệ, quy trình, thời điểm hoặc ngữ cảnh.
- Nếu vault chưa có quy ước tag rõ ràng, tag mới dùng chữ thường, ngắn gọn, dạng `kebab-case`, không dấu để ổn định tìm kiếm.
- Ghi tag trong frontmatter `tags:` để Obsidian nhận là hashtag; không cần chèn thêm hashtag vào thân bài chỉ để phân loại.

### 4. Di chuyển an toàn, tuyệt đối không ghi đè
- Tạo folder đích nếu cần rồi di chuyển chính file source sang vị trí đã chọn; ưu tiên giữ nguyên tên file.
- Trước khi move, kiểm tra collision. Nếu đường dẫn đích đã có file khác cùng tên thì TUYỆT ĐỐI KHÔNG overwrite/xoá/merge ngầm.
- Khi collision, chọn tên an toàn có hậu tố số nhỏ nhất chưa tồn tại (`ten.md` -> `ten-2.md` -> `ten-3.md`...) và báo tên cuối cùng trong kết quả.
- Sau khi move/rename, mọi citation, `wiki_links`, log và báo cáo phải tham chiếu source ở tên/vị trí cuối cùng; không để link trỏ tên cũ.

### 5. Phân loại ổn định sau lần đầu
- Với source đã có một folder hợp lý, re-ingest KHÔNG tự động chuyển folder chỉ vì lần đọc mới nghĩ ra một taxonomy "hay hơn".
- Chỉ tự move khi vị trí hiện tại rõ ràng sai hoặc người dùng yêu cầu tái tổ chức. Trường hợp chỉ là cải tiến taxonomy, đề xuất cho người dùng duyệt thay vì mass-move.
- Không tự đổi tên/gộp/xoá hàng loạt folder hoặc tag trong lúc ingest một source.

## Các bước
1. Đọc source (kèm ảnh nếu có).
2. Nếu là Markdown người dùng cần quản lý trong Second Brain: áp dụng đầy đủ **Kỷ luật tổ chức source trước khi tiêu hoá**; sau bước này dùng tên/vị trí source cuối cùng cho toàn bộ phần còn lại.
3. Tóm tắt 3-5 ý chính; rút insight/framework; liên hệ khái niệm đã có.
4. Xác định trang wiki: mới cần tạo / cần cập nhật / cần merge (đọc `wiki/index.md` để dedup).
5. Viết/cập nhật wiki (1 trang = 1 ý, có `[[...]]` ngược lại trang liên quan) - TUÂN THỦ 3 KỶ LUẬT:
   - Citation cứng: mỗi câu cụ thể kết bằng `[[Nguồn]]`.
   - Mục tiêu vs thực tế: gắn nhãn "(mục tiêu)" / "(thực tế tính đến ...)" / "(cần xác minh)".
   - Mâu thuẫn với trang cũ: thêm `## Mâu thuẫn` (giữ cả 2 quan điểm + nguồn) + append `wiki/_open-questions.md`, KHÔNG ghi đè.
   Ngoài 3 kỷ luật, mỗi trang phải TỰ ĐỦ NGỮ CẢNH (contextual retrieval - trang tách khỏi source vẫn hiểu và tìm thấy được):
   - Mở đầu trang bằng 1-2 câu định vị: khái niệm này là gì, thuộc chủ đề/nguồn nào, dùng khi nào. Nhất là khi ingest source dài theo từng đoạn - đoạn giữa sách mà thiếu câu định vị là sau này đọc lẻ không hiểu.
   - Frontmatter thêm `aliases: [tên gọi khác, viết tắt, thuật ngữ tiếng Anh]` - để sau này hỏi bằng từ khác thì grep/search vẫn bắt trúng trang.
6. Cập nhật `wiki/index.md` (thêm dòng link + mô tả 1 dòng).
7. Set source `status: processed`, `processed_at`, `wiki_links: [...]`. Không đáng vào wiki -> `status: skipped` + `note`.
8. Append `wiki/log.md`: `## [YYYY-MM-DD] ingest | <tên source>` + nguồn/đã tạo/đã cập nhật/insight.
9. Đề xuất task nếu source mở ra hành động (chỉ đề xuất). Báo cáo ngắn: vị trí source cuối cùng + tags + tóm tắt + trang wiki đã chạm + insight + task.
