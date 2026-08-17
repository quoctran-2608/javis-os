# Brain OS - Ingest Policy

## Nguyên tắc

Brain OS không đồng nhất `note tồn tại` với `note cần Wiki`.

Các cấp xử lý:

```text
DISCOVER -> INDEX -> CLASSIFY -> INGEST -> COMPOUND
```

Mỗi cấp có chi phí và mức tác động khác nhau.

## DISCOVER

Mục tiêu:
- biết file tồn tại;
- biết path, size, mtime, hash, zone;
- không cần AI.

## INDEX

Mục tiêu:
- file có thể được tìm/đọc khi cần;
- không bắt buộc tạo Wiki;
- không thay đổi nội dung user note.

## CLASSIFY

Xác định:
- document type;
- primary category;
- canonical tags;
- hành động tiếp theo.

Ưu tiên deterministic rule trước AI.

## INGEST

Chỉ dùng cho nội dung đáng hiểu sâu hơn.

Có thể:
- rút insight;
- nhận diện framework/process;
- so với Wiki hiện hữu;
- tạo Memory/Wiki Candidate.

## COMPOUND

Chỉ khi tri thức đủ giá trị tái sử dụng.

Có thể:
- update Wiki page hiện hữu;
- tạo Wiki page mới;
- thêm provenance/backlink;
- ghi contradiction;
- tạo Memory candidate.

## Document type mặc định

### Scratch / Temporary

```text
DISCOVER: yes
INDEX: yes
INGEST: no by default
WIKI: no
```

### Daily

```text
DISCOVER: yes
INDEX: yes
INGEST: selective
WIKI: never by default
```

### Weekly / Monthly

```text
INDEX: yes
PATTERN DETECTION: yes
INGEST: selective
WIKI: candidate first
```

### Future / Planning

```text
INDEX: yes
TASK/PLANNING: yes
INGEST: no by default
WIKI: no by default
```

### Living Note

```text
INDEX: yes
INGEST: selective
COMPOUND: selective
EDITABLE BY USER: always
```

### Reference Source

```text
INDEX: yes
INGEST: auto by default
WIKI: selective based on reusable knowledge
```

### Wiki

```text
INDEX: yes
INGEST: never
```

### Library original

```text
INDEX: metadata only
DIRECT INGEST: no
INGEST THROUGH NORMALIZED SOURCE: yes
```

## Living Note rules

- Không tự chia Living Note thành nhiều note vật lý.
- Không coi ingest là trạng thái `done forever`.
- Sau khi user sửa, chỉ phần changed context nên đi vào incremental ingest khi có đủ state.
- Wiki sinh từ Living Note phải giữ provenance/backlink về note.
- Nếu một đoạn chỉ là cảm xúc/context tạm thời, index là đủ.

## Candidate trước Wiki

Khi insight có vẻ có giá trị nhưng chưa đủ chắc chắn:

```text
CANDIDATE
```

thay vì tạo Wiki ngay.

Candidate đặc biệt phù hợp với:
- reflection cá nhân;
- pattern mới xuất hiện một lần;
- conclusion cần thêm nguồn/bằng chứng;
- taxonomy/category mới.

## Incremental ingest

Khi note dài thay đổi:
- dùng content hash để biết có thay đổi thật;
- tính diff/changed sections deterministic;
- chỉ đưa phần thay đổi + context cần thiết vào AI;
- không gửi lại toàn note nếu không cần.

## Original documents

PDF/DOCX/Sheet:

```text
Original -> Library -> SHA-256 -> normalized source -> sources -> ingest
```

AI không sửa original.

Living Note import từ Amplenote:

```text
immutable snapshot + editable working note
```

Snapshot dùng audit/khôi phục; working note tiếp tục sống trong Obsidian.

## Provenance

Mọi Wiki/Memory do ingest tạo phải có khả năng truy ngược về source/Living Note. Không tạo claim không có nguồn khi policy yêu cầu citation/provenance.

## Delete

Source bị delete không đồng nghĩa tri thức phải bị delete ngay. Mark missing và chờ cleanup/review.

## Dry run

Khi `dry_run: true`:
- được tính proposed ingest action;
- được sinh report/candidate giả lập;
- không update Wiki/Memory thật;
- không rewrite user note;
- không move file.
