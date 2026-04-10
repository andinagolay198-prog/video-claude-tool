#!/bin/bash
# ============================================================
# Deploy Video-Claude Tool on Proxmox LXC (docker-server)
# Tested: CT100, Debian, Docker đã có sẵn
# Ports: 8765 (backend API), 8766 (frontend)
# ============================================================
set -e

REPO="https://github.com/andinagolay198-prog/video-claude-tool.git"
APP_DIR="/opt/video-claude-tool"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()    { echo -e "${GREEN}[✓]${NC} $1"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
error()   { echo -e "${RED}[✗]${NC} $1"; exit 1; }

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║    Video × Claude - Deploy to Docker     ║"
echo "║    Backend: :8765  Frontend: :8766       ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# Check Docker
command -v docker &>/dev/null || error "Docker chưa cài. Chạy: curl -fsSL https://get.docker.com | sh"
info "Docker: $(docker --version | cut -d' ' -f3 | tr -d ',')"

# Check ports free
for port in 8765 8766; do
  if ss -tlnp | grep -q ":$port "; then
    warn "Port $port đang bị dùng — kiểm tra: ss -tlnp | grep $port"
  fi
done

# Clone or update
if [ -d "$APP_DIR/.git" ]; then
    info "Cập nhật repo..."
    cd "$APP_DIR"
    git pull origin main
else
    info "Clone repo..."
    git clone "$REPO" "$APP_DIR"
    cd "$APP_DIR"
fi

# Setup .env
if [ ! -f "$APP_DIR/.env" ]; then
    if [ -n "$ANTHROPIC_API_KEY" ]; then
        echo "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY" > "$APP_DIR/.env"
        info "Dùng ANTHROPIC_API_KEY từ environment"
    else
        echo ""
        read -p "🔑 Nhập Anthropic API key (sk-ant-...): " KEY
        [ -z "$KEY" ] && error "API key không được trống"
        echo "ANTHROPIC_API_KEY=$KEY" > "$APP_DIR/.env"
        info "Đã lưu .env"
    fi
else
    info ".env đã tồn tại"
fi

# Build
info "Build Docker image (lần đầu ~5-10 phút do tải Whisper)..."
docker compose build

# Stop old containers nếu có
docker compose down 2>/dev/null || true

# Start
info "Khởi động services..."
docker compose up -d

# Wait for health
echo ""
warn "Chờ backend sẵn sàng (Whisper model loading ~60s)..."
for i in $(seq 1 24); do
    if curl -sf http://localhost:8765/health > /dev/null 2>&1; then
        echo ""
        info "Backend healthy!"
        break
    fi
    printf "."
    sleep 5
done

# Show status
echo ""
docker compose ps
echo ""

IP=$(hostname -I | awk '{print $1}')
echo "╔══════════════════════════════════════════════╗"
echo "║              DEPLOY THÀNH CÔNG!              ║"
echo "╠══════════════════════════════════════════════╣"
printf "║  🌐 Frontend : http://%-22s║\n" "$IP:8766"
printf "║  ⚙  Backend  : http://%-22s║\n" "$IP:8765"
printf "║  💊 Health   : http://%-22s║\n" "$IP:8765/health"
echo "║                                              ║"
echo "║  Nginx Proxy Manager → thêm proxy host:     ║"
echo "║  domain → http://docker-server-ip:8766      ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "Lệnh quản lý:"
echo "  cd $APP_DIR"
echo "  docker compose logs -f              # xem logs"
echo "  docker compose restart              # restart"
echo "  docker compose down                 # dừng"
echo "  git pull && docker compose up -d --build  # update"
echo ""
