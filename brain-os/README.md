# Brain OS V1

Brain OS là lớp mở rộng chạy trên Brain của Javis, không sửa core `server/` hoặc `dashboard/`.

## Quy ước thư mục trong repo

`brain-os/template/` là **cây file mẫu để cài vào một Brain thật**. Nó không phải là một folder cần xuất hiện nguyên khối trong Obsidian/Javis Brain.

Ví dụ file trong repo:

```text
brain-os/template/skills/brain-manager/scripts/probe_runtime.py
```

khi cài vào Brain sẽ trở thành:

```text
<Brain>/skills/brain-manager/scripts/probe_runtime.py
```

Tương tự:

```text
brain-os/template/Javis/loops/brain-os-probe.md
```

sẽ trở thành:

```text
<Brain>/Javis/loops/brain-os-probe.md
```

Dấu `/` trong tài liệu chỉ là ký hiệu phân cách các thư mục lồng nhau; nó không nằm trong tên folder.

## Ranh giới an toàn

- `brain-os/template/`: file sẽ được cài vào Brain.
- `brain-os/tools/`: công cụ dev/validator/installer, không phải dữ liệu Brain.
- `brain-os/tests/`: test của Brain OS.
- Không đưa dữ liệu cá nhân thật vào repo code này.
- Không sửa core Javis nếu Skill/Loop/Script trong Brain giải quyết được.

## Trạng thái hiện tại

Brain OS V1 đang được phát triển theo từng chặng. Mặc định `System/BrainOS/config.yml` để `dry_run: true`, tắt auto-move và auto-create taxonomy cho tới khi các gate kiểm thử tương ứng đạt.
