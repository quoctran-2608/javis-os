---
name: Javis Upstream Update
description: "Cập nhật fork Javis OS từ upstream, giữ Claude Code, Codex và Antigravity; chặn release lỗi và kiểm soát deploy VPS."
group: Vận hành
---

# Javis Upstream Update

## Khi nào dùng

Dùng khi người dùng báo upstream Javis OS có bản mới, yêu cầu đồng bộ
`blogminhquy/javis-os` vào fork production `quoctran-2608/javis-os`, hoặc muốn phát hành
một bản fork mới mà không làm mất Claude Code, Codex CLI và Google Antigravity CLI.

Đây là quy trình release production. Không rút gọn cổng kiểm tra, không tự deploy, và
không coi một merge không conflict là bằng chứng hệ thống còn hoạt động.

## Hợp đồng

- Đầu vào: repo fork hiện tại, upstream mới, và số phiên bản release nếu người dùng đã chốt.
- Đầu ra trước deploy: `main` đã merge, tag bất biến, CI xanh, ba tag GHCR cùng digest.
- Đầu ra sau deploy: đúng version, server khỏe, các CLI đã kết nối trước đó vẫn kết nối.
- Lỗi chặn: Git bẩn, sai remote/branch, conflict chưa giải quyết, test đỏ, CI đỏ, thiếu image.
- Tác động được phép trước xác nhận deploy: tạo nhánh, sửa source, commit, tag và push fork.
- Tác động chỉ được phép sau xác nhận rõ: SSH/VPS, sửa cấu hình image, pull và restart container.
- Quyền cần có: đọc/ghi Git và push `origin`; quyền Docker/VPS chỉ dùng sau khi được xác nhận.

## Chuẩn bị

1. Làm ở checkout development của fork, không merge source trực tiếp trên VPS.
2. Đọc `tools/sync_upstream.py`, `docs/27-van-hanh-fork.md`, `VERSION`, đầu
   `CHANGELOG.md`, `.github/workflows/ci.yml` và `.github/workflows/docker-publish.yml`.
3. Ghi lại:

   ```bash
   BASE_SHA=$(git rev-parse HEAD)
   BASE_VERSION=$(cat VERSION | tr -d ' \r\n')
   git branch --show-current
   git remote -v
   git status --porcelain --untracked-files=normal
   ```

4. Cây Git phải sạch tuyệt đối, kể cả file untracked. Nếu có output từ `git status`, DỪNG.
   Báo từng file và yêu cầu người dùng commit/push, chuyển file ra ngoài repo, hoặc tự stash.
   Không tự stash và không xóa file.
5. Phải đang ở `main`, với:
   - `origin` = `quoctran-2608/javis-os`
   - `upstream` = `blogminhquy/javis-os`
6. Chạy cổng kiểm tra có sẵn:

   ```bash
   python tools/sync_upstream.py --check
   ```

   Lệnh đỏ thì DỪNG. Không sửa remote hoặc bỏ qua cây bẩn một cách âm thầm.

## Quy trình

### 1. Tạo nhánh sync bằng tool của repo

Chạy:

```bash
python tools/sync_upstream.py
```

Tool tự fetch hai remote, fast-forward `main` theo `origin/main`, tạo
`sync/upstream-<VERSION>` và merge `upstream/main` trên nhánh đó. Không viết lại chuỗi
lệnh này bằng một quy trình Git khác nếu tool vẫn dùng được.

Sau lệnh, xác nhận:

```bash
SYNC_BRANCH=$(git branch --show-current)
git status --short --branch
git log --oneline --decorate -8
```

Nếu tool lỗi vì lý do khác conflict, DỪNG. Nếu nhánh sync cũ đã tồn tại, không tự xóa;
kiểm tra lịch sử và hỏi người dùng cách xử lý.

### 2. Xử lý conflict mà không bỏ custom provider

Liệt kê conflict:

```bash
git diff --name-only --diff-filter=U
```

Với từng file, đọc đủ ba phía khi cần:

```bash
git show :1:<path>   # base
git show :2:<path>   # fork hiện tại
git show :3:<path>   # upstream mới
```

Không dùng `--ours`, `--theirs`, chép đè toàn file, hoặc nhận toàn bộ một phía cho cả repo.
Phải hiểu ý nghĩa từng hunk rồi hợp nhất cả sửa lỗi upstream lẫn hợp đồng custom.

Các bất biến phải giữ:

- Ba provider vẫn tồn tại: `anthropic-cli`, `openai-oauth`, `antigravity-cli`.
- Claude Code vẫn có auth, model, stream, resume, MCP native và volume
  `/home/javis/.claude`.
- Codex CLI vẫn có OAuth, model live, stream, resume, MCP và volume
  `/home/javis/.codex`.
- Antigravity vẫn có `server/antigravity_cli.py`,
  `server/antigravity_mcp_proxy.py`, OAuth PTY Windows/Linux/macOS, URL PKCE đầy đủ,
  model live, stream, resume, MCP proxy, logout và volume `/home/javis/.gemini`.
- Backend, dashboard Models, việc nền, chatbot, Telegram, usage và connect health vẫn nhận
  đủ ba CLI.
- Compose vẫn dùng `ghcr.io/quoctran-2608/javis-os` và giữ ba auth volume.
- Windows CI vẫn cài `pywinpty` và chạy canary OAuth Antigravity.
- Updater và `tools/sync_upstream.py` vẫn fail-closed khi Git bẩn; không tự stash custom source.

Những vùng cần đọc kỹ nếu bị chạm gồm `server/main.py`, `server/config.py`,
`server/sessions.py`, `server/claude_cli.py`, `server/antigravity_cli.py`,
`server/aux_engine.py`, `server/connect_health.py`, `server/usage_index.py`,
`dashboard/`, `requirements.txt`, `Dockerfile`, `docker-compose*.yml`,
`.github/workflows/` và các test provider.

Sau khi sửa:

```bash
git diff --check
git grep -n -E '^(<<<<<<< |>>>>>>> |\|\|\|\|\|\|\| )' -- . || true
git diff --name-only --diff-filter=U
```

Không được còn file `U` hoặc marker conflict. Review diff đầy đủ, `git add` đúng các file đã
giải quyết, rồi hoàn tất merge bằng commit. Không release khi conflict chỉ được che đi.

### 3. Chạy toàn bộ test trên nhánh sync

Chạy đúng runner của repo:

```bash
python tests/run.py -v
```

Toàn bộ Python và JavaScript test phải xanh. Thiếu dependency, lỗi filesystem, test flaky
hoặc test chỉ đỏ trên Windows đều là lỗi cần xử lý, không phải lý do để bỏ qua. Có thể sửa
môi trường hoặc code trên nhánh sync, nhưng sau mỗi sửa phải chạy lại toàn bộ suite.

Đặc biệt không được mất các canary:

```bash
python tests/run.py release_channel -v
python tests/run.py antigravity_cli -v
python tests/run.py luot_chat_antigravity -v
python tests/run.py luot_chat_codex -v
python tests/run.py connect_health -v
python tests/run.py sync_upstream -v
python tests/run.py update -v
```

Nếu bất kỳ test nào đỏ, DỪNG release. Không bump version, không merge `main`, không tag,
không push production.

### 4. Cập nhật VERSION và CHANGELOG

Chỉ làm bước này sau khi full suite xanh.

1. Đọc version fork cũ và version upstream:

   ```bash
   git show "$BASE_SHA":VERSION
   git show upstream/main:VERSION
   git tag --list 'v*' --sort=-version:refname | head
   ```

2. Chọn `NEW_VERSION` theo SemVer, lớn hơn cả release fork cũ và version upstream, chưa tồn
   tại ở local hoặc `origin`. Không tái dùng tag upstream cho một cây source có custom code.
   Nếu chính sách version không suy ra rõ, DỪNG và hỏi người dùng đúng một số version.
3. Ghi `VERSION` chỉ gồm `X.Y.Z`.
4. Thêm mục mới trên cùng `CHANGELOG.md` theo đúng format hiện có, dùng ngày thực tế. Ghi rõ:
   - upstream version hoặc dải commit đã nhập;
   - conflict quan trọng đã giải quyết;
   - xác nhận giữ Claude Code, Codex và Antigravity;
   - test đã chạy.
5. Tìm và cập nhật các canary cố ý ghim version cũ, nhất là
   `tests/python/test_release_channel.py`. Không thay lịch sử release cũ.
6. Chạy lại:

   ```bash
   python tests/run.py version_khop -v
   python tests/run.py release_channel -v
   python tests/run.py -v
   git diff --check
   ```

Test đỏ thì DỪNG. Khi xanh, commit release metadata trên nhánh sync và lưu:

```bash
SYNC_TIP=$(git rev-parse HEAD)
git status --porcelain --untracked-files=normal
```

Trạng thái phải sạch.

### 5. Merge vào main và kiểm thử commit sẽ phát hành

Fetch `origin`. Nếu `origin/main` đã tiến thêm trong lúc làm, quay lại nhánh sync, merge thay
đổi mới, xử lý conflict và chạy lại toàn bộ test. Không ép push đè người khác.

Khi `main` còn khớp `origin/main`:

```bash
git switch main
git merge --ff-only origin/main
git merge --no-ff "$SYNC_BRANCH" -m "release: sync upstream as $NEW_VERSION"
git diff --exit-code "$SYNC_TIP" HEAD
python tests/run.py -v
git status --porcelain --untracked-files=normal
```

Diff cây phải rỗng, full suite phải xanh và Git phải sạch. Nếu không, DỪNG.

### 6. Tag và push

Tạo annotated tag trên đúng commit `main`:

```bash
RELEASE_SHA=$(git rev-parse HEAD)
git tag -a "v$NEW_VERSION" -m "Javis OS $NEW_VERSION"
git rev-list -n1 "v$NEW_VERSION"
git push --atomic origin main "v$NEW_VERSION"
```

SHA của tag phải dereference về `RELEASE_SHA`. Push bị từ chối thì DỪNG; không force-push,
không di chuyển tag đã công bố và không amend release đã push.

### 7. Kiểm tra GitHub Actions

Theo dõi đúng `RELEASE_SHA` cho tới khi cả hai workflow hoàn tất:

- `CI`: job Ubuntu và `Windows CLI auth` đều `success`.
- `Build & Publish Docker image (GHCR)`: `success`.

Ưu tiên `gh run list`, `gh run view` và `gh run watch --exit-status` nếu máy đã đăng nhập
GitHub CLI. Nếu không có `gh`, dùng GitHub Actions API hoặc tab Actions và đối chiếu chính
xác workflow, branch `main`, commit SHA, trạng thái `completed`, kết luận `success`.

CI đỏ thì release không được deploy. Sửa trên nhánh mới, tăng patch version và phát hành tag
mới. Không sửa nội dung của tag đã push để làm nó "xanh lại".

### 8. Xác nhận Docker image GHCR

Workflow phải tạo đủ:

```text
ghcr.io/quoctran-2608/javis-os:latest
ghcr.io/quoctran-2608/javis-os:<NEW_VERSION>
ghcr.io/quoctran-2608/javis-os:<RELEASE_SHA>
```

Dùng `docker buildx imagetools inspect` hoặc GHCR Registry API để lấy digest của cả ba tag.
Ba tag phải pull được công khai và trỏ cùng một digest do cùng một build sinh ra. Nếu thiếu
tag, package private, digest lệch hoặc manifest lỗi, DỪNG và sửa pipeline/package trước deploy.

### 9. Điểm dừng bắt buộc trước production

Trình người dùng:

- version và `RELEASE_SHA`;
- commit/tag đã push;
- link hoặc ID hai workflow xanh;
- digest GHCR của ba tag;
- tóm tắt thay đổi và conflict đã xử lý;
- kết quả full suite.

Sau đó hỏi đúng một câu: **"Bản `<NEW_VERSION>` đã sẵn sàng. Anh có xác nhận deploy production
VPS không?"**

DỪNG và chờ trả lời. Không SSH, không pull image, không gọi Watchtower, không redeploy và
không chạy Docker production khi chưa có xác nhận rõ.

### 10. Deploy VPS sau khi được xác nhận

Chỉ dùng đúng host và thư mục compose production đã được người dùng xác nhận. Trước deploy:

1. Ghi `OLD_VERSION`, image/digest đang chạy, kết quả `/health`, `/version`,
   `/claude/status`, `/oauth/openai/status`, `/antigravity/status`.
2. Ghi lại các mount của container và xác nhận còn:
   `/home/javis/.claude`, `/home/javis/.codex`, `/home/javis/.gemini`.
3. Xác nhận tag rollback `ghcr.io/quoctran-2608/javis-os:<OLD_VERSION>` còn pull được.
4. Sao lưu file cấu hình môi trường hiện tại mà không in secret ra log.
5. Pin `JAVIS_IMAGE=ghcr.io/quoctran-2608/javis-os:<NEW_VERSION>` trong cấu hình VPS.

Deploy:

```bash
docker compose pull javis
docker compose up -d javis
docker compose ps
docker compose logs --tail=120 javis
```

Tuyệt đối không chạy `docker compose down -v`.

### 11. Health-check sau deploy

Chờ healthcheck xanh rồi kiểm:

```bash
curl -fsS http://127.0.0.1:7777/health
curl -fsS http://127.0.0.1:7777/version
curl -fsS http://127.0.0.1:7777/claude/status
curl -fsS http://127.0.0.1:7777/oauth/openai/status
curl -fsS http://127.0.0.1:7777/antigravity/status
docker compose ps
docker compose logs --tail=120 javis
```

Điều kiện đạt:

- `/health` trả HTTP 200 và `status: ok`;
- `/version.current` đúng `NEW_VERSION`, repo/image vẫn là fork;
- CLI nào `connected: true` trước deploy vẫn `connected: true` sau deploy;
- container không restart loop, health không đỏ, log không có traceback/lỗi auth mới;
- một smoke chat ngắn chạy được qua từng CLI đã kết nối, rồi trả model chính về cấu hình cũ.

Nếu endpoint status cần auth, dùng phiên admin hiện có hoặc kiểm từ localhost/container; không
in token, cookie hay nội dung credential.

### 12. Rollback nếu lỗi

Bất kỳ điều kiện bắt buộc nào ở trên hỏng thì rollback ngay:

1. Khôi phục cấu hình image cũ hoặc pin
   `JAVIS_IMAGE=ghcr.io/quoctran-2608/javis-os:<OLD_VERSION>`.
2. Chạy:

   ```bash
   docker compose pull javis
   docker compose up -d javis
   ```

3. Chạy lại `/health`, `/version`, ba endpoint trạng thái CLI, `docker compose ps` và log.
4. Không xóa volume, không `down -v`, không xóa credential để "thử lại".
5. Báo rõ release lỗi, bước lỗi, log đã lọc secret, version đã rollback và trạng thái ba CLI.

Rollback chỉ hoàn tất khi bản cũ khỏe và các kết nối trước deploy được giữ nguyên. Nếu rollback
cũng lỗi, DỪNG thay đổi thêm, giữ nguyên volume và đưa bằng chứng để người dùng quyết định.

## Bẫy

- Không dùng nút Update trong app để thay cho quy trình merge upstream của fork.
- Không release từ working tree bẩn hoặc detached HEAD.
- Không bỏ test đỏ vì cho rằng lỗi môi trường; sửa môi trường rồi chạy lại đủ suite.
- Không chỉ test Antigravity rồi suy ra Claude/Codex còn sống.
- Không lấy nguyên file upstream để giải conflict ở vùng có custom provider.
- Không push thẳng nhánh sync thành `main`, không force-push và không rewrite tag.
- Không deploy `latest` khi đã có tag version; pin version để rollback xác định.
- Không log secret OAuth, nội dung file auth, cookie admin hoặc biến môi trường nhạy cảm.

## Kiểm chứng

Kết thúc mỗi lần chạy bằng một báo cáo có thể audit:

```text
Upstream SHA/version:
Base fork SHA/version:
Sync branch/tip:
Release version/SHA/tag:
Conflict và cách giữ ba CLI:
Full test local:
CI Ubuntu:
CI Windows CLI auth:
Docker workflow:
GHCR digest latest/version/SHA:
Deploy được người dùng xác nhận: có/không
VPS old -> new version:
Health và trạng thái Claude/Codex/Antigravity:
Rollback: không cần/thành công/thất bại:
```

Không tuyên bố hoàn tất nếu thiếu bằng chứng cho bất kỳ cổng nào đã thực sự đi qua.
