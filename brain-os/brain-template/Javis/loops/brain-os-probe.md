---
type: loop
name: Brain OS Runtime Probe
slug: brain-os-probe
enabled: false
goal: custom
mode: auto
interval_min: 60
workspace: vault
tools_profile: code
quiet_hours: ""
max_runs_per_day: 1
notify: false
updated: 2026-08-17
---

Chạy đúng một lần lệnh sau bằng Bash từ root của brain hiện tại:

`python skills/brain-manager/scripts/probe_runtime.py --compact`

Mục tiêu duy nhất là xác minh runtime cho Brain OS V1.

Yêu cầu:
- Không sửa nội dung note của người dùng.
- Không di chuyển hoặc đổi tên file.
- Không tạo Wiki, Memory, tag hoặc category.
- Probe được phép tạo file tạm bên dưới `.javis/.brain-os-probe/` và phải tự dọn chúng trước khi kết thúc.
- Báo lại `ok`, các `required_failures`, `optional_failures`, Python version, SQLite version, FTS5, YAML và brain root.
- Nếu command trả exit code khác 0, báo nguyên nhân ngắn gọn và dừng. Không tự sửa hệ thống.
