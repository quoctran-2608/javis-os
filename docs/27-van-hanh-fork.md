# Vận hành fork & cập nhật upstream

Fork production là `quoctran-2608/javis-os`. Repo gốc là
`blogminhquy/javis-os`. Mục tiêu là nhận sửa lỗi mới từ upstream mà không làm
mất ba engine CLI: Claude Code, Codex và Google Antigravity.

## Nguồn chuẩn

- `origin`: fork production của bạn.
- `upstream`: repo gốc.
- `main`: bản đã kiểm thử và dùng để build Docker image.
- `sync/upstream-*`: nhánh tạm để merge source mới.

Kiểm tra:

```bash
git remote -v
```

Kết quả cần có:

```text
origin   git@github.com:quoctran-2608/javis-os.git
upstream git@github.com:blogminhquy/javis-os.git
```

## Nhận bản upstream mới

Từ cây Git sạch trên nhánh `main`:

```bash
python tools/sync_upstream.py
```

Script tự:

1. Kiểm tra cả file tracked và untracked.
2. Kiểm tra đúng hai remote.
3. Fetch `origin` và `upstream`.
4. Fast-forward `main` theo fork.
5. Tạo nhánh `sync/upstream-<VERSION>`.
6. Merge `upstream/main`.

Nếu có conflict, script dừng nguyên tại nhánh sync. Không sửa trực tiếp trên
VPS và không chép đè toàn file. Giữ cả thay đổi upstream lẫn hợp đồng ba CLI,
sau đó chạy test:

```bash
python tests/python/test_antigravity_cli.py
python tests/python/test_luot_chat_antigravity.py
python tests/python/test_update.py
python tests/python/test_connect_health.py
```

Khi đạt:

```bash
git switch main
git merge --no-ff sync/upstream-<VERSION>
git push origin main
```

## Deploy VPS

CI của fork build:

```text
ghcr.io/quoctran-2608/javis-os:latest
ghcr.io/quoctran-2608/javis-os:<VERSION>
ghcr.io/quoctran-2608/javis-os:<commit-sha>
```

VPS cập nhật:

```bash
docker compose pull
docker compose up -d
docker compose ps
```

Không chạy `docker compose down -v`: cờ `-v` xóa cả volume credential.

## Vì sao đăng nhập không mất

Source nằm trong image. Credential nằm ngoài image:

```text
Claude Code     /home/javis/.claude  -> volume claude-auth
Codex CLI       /home/javis/.codex   -> volume codex-auth
Antigravity CLI /home/javis/.gemini  -> volume antigravity-auth
```

Thay image, restart hoặc rollback không xóa ba volume này.

## Rollback

Đặt trong `.env`:

```env
JAVIS_IMAGE=ghcr.io/quoctran-2608/javis-os:<VERSION-CU>
```

Rồi:

```bash
docker compose pull
docker compose up -d
```

## Đổi kênh phát hành

Mặc định app kiểm tra version từ fork. Có thể đổi:

```env
JAVIS_UPDATE_REPO=owner/repo
JAVIS_IMAGE_REPO=ghcr.io/owner/repo
JAVIS_IMAGE=ghcr.io/owner/repo:tag
```
