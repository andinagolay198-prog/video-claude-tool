#!/bin/bash
# Fix claude-bot container đang Restarting
# Chạy từ thư mục chứa claude-bot docker-compose

echo "=== Debug claude-bot ==="
echo ""

# Tìm thư mục claude-bot
BOT_DIR=$(find /root /opt /home -name "docker-compose.yml" 2>/dev/null | xargs grep -l "claude-bot" 2>/dev/null | head -1 | xargs dirname)

if [ -z "$BOT_DIR" ]; then
    echo "Không tìm thấy claude-bot compose file"
    echo "Xem logs trực tiếp:"
    docker logs claude-bot-claude-bot-1 --tail 30
    exit 1
fi

echo "Tìm thấy tại: $BOT_DIR"
cd "$BOT_DIR"

echo ""
echo "=== Logs gần nhất ==="
docker logs claude-bot-claude-bot-1 --tail 40

echo ""
echo "=== docker-compose.yml hiện tại ==="
cat docker-compose.yml
