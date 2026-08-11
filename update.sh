#!/usr/bin/env bash
# ============================================================================
# Javis OS - cập nhật lên bản mới nhất từ GitHub.
#   ./update.sh            (tự nhận Docker hay native)
#   ./update.sh docker     (ép chế độ Docker)
#   ./update.sh native     (ép chế độ native/systemd)
# ============================================================================
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
MODE="${1:-auto}"
SUDO=""; [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && SUDO="sudo"

if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
  echo "LỖI: Cây Git có sửa đổi chưa commit."
  echo "Javis không tự stash vì provider có thể gồm cả file mới."
  echo "Hãy commit/push hoặc tự cất thay đổi rồi chạy update lại."
  exit 1
fi

echo "==> Kéo code mới từ GitHub..."
git pull --ff-only

is_docker() {
  command -v docker >/dev/null 2>&1 && [ -f docker-compose.yml ] && \
  docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx javis
}

if [ "$MODE" = "docker" ] || { [ "$MODE" = "auto" ] && is_docker; }; then
  echo "==> Docker → pull image mới từ GHCR + restart..."
  # Đang bật HTTPS (Caddy)? Giữ nguyên override để cập nhật KHÔNG gỡ mất Caddy.
  HTTPS_ARGS=""
  if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx javis-caddy; then
    HTTPS_ARGS="-f docker-compose.yml -f docker-compose.https.yml"
    echo "==> Phát hiện Caddy (HTTPS) → giữ nguyên cấu hình HTTPS khi cập nhật."
  fi
  docker compose $HTTPS_ARGS pull
  docker compose $HTTPS_ARGS up -d
  echo "==> Xong. Theo dõi:  docker compose logs -f"
else
  echo "==> Native → cập nhật thư viện Python + restart dịch vụ..."
  [ -d .venv ] && ./.venv/bin/pip install -r requirements.txt -q || true
  if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q '^javis\.service'; then
    $SUDO systemctl restart javis
    echo "==> Đã restart. Theo dõi:  journalctl -u javis -f"
  else
    # Mac / Linux không systemd: kill tiến trình đang giữ cổng rồi chạy lại nền (như install.sh)
    PORT="${JAVIS_PORT:-7777}"
    PIDS="$(lsof -ti tcp:"$PORT" 2>/dev/null || true)"
    if [ -n "$PIDS" ]; then
      echo "==> Dừng tiến trình cũ (PID: $PIDS)..."
      kill $PIDS 2>/dev/null || true
      sleep 2
    fi
    ( cd server && JAVIS_STATE_DIR="$PWD" nohup ../.venv/bin/python -m uvicorn main:app \
        --host "${JAVIS_HOST:-127.0.0.1}" --port "$PORT" > javis.log 2>&1 & )
    echo "==> Đã khởi động lại (nohup). Logs: server/javis.log"
  fi
fi
