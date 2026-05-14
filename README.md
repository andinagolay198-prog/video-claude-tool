# 🎬 Video × Claude Tool v2

Công cụ phân tích và xử lý video thông qua Claude AI.

## 🚀 Stack
- **Backend**: FastAPI + uvicorn (port 8765)
- **Frontend**: Nginx static (port 8766)
- **AI**: Claude Sonnet 4 (Anthropic API)
- **Tools**: ffmpeg, ffprobe, Whisper, yt-dlp, Node.js

## 📁 Cấu trúc
video-claude-tool/
├── backend/
│   ├── main.py          # FastAPI app - tất cả endpoints
│   └── requirements.txt # Python dependencies
├── frontend/
│   └── index.html       # Single-page app
├── docker-compose.yml   # Docker orchestration
├── Dockerfile           # Backend image
├── nginx.conf           # Frontend proxy config
└── .env                 # API keys (không commit)
## 🔌 API Endpoints

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| GET | `/health` | Health check + dependencies |
| POST | `/analyze` | Phân tích video qua frames + Whisper |
| POST | `/chat` | Follow-up chat (nhớ video qua session) |
| POST | `/sync-srt` | Sync timestamp subtitle bằng Whisper |
| POST | `/split-video` | Phân tích điểm cắt theo silence |
| POST | `/cut-segment` | Cắt đoạn video |
| POST | `/download-video` | Download YouTube/TikTok (async) |
| GET | `/download-status/{job_id}` | Poll trạng thái download |
| GET | `/downloads/{filename}` | Serve file đã download |
| POST | `/convert-video` | Chuyển codec/tỷ lệ |
| POST | `/merge-videos` | Ghép nhiều video |
| POST | `/add-music` | Ghép nhạc nền |
| POST | `/compress-video` | Nén cho YT/FB/Mobile |
| DELETE | `/session/{session_id}` | Xóa session |

## 🔐 Bảo mật
- CORS chỉ cho phép origins nội bộ (`ALLOWED_ORIGINS`)
- API key Anthropic từ `.env` (không hardcode)
- Session TTL 1 giờ, auto-cleanup

## ⚙️ Cài đặt

```bash
# Clone & cấu hình
cp .env.example .env
# Điền ANTHROPIC_API_KEY vào .env

# Start
docker compose up -d

# Copy Node.js runtime (cần cho yt-dlp)
docker cp /usr/bin/node video-claude-backend:/usr/local/bin/node
```

## 🔄 Download Video (Async)
```bash
# 1. Start download → nhận job_id ngay
curl -X POST http://localhost:8765/download-video -F "url=URL"
# → {"status":"started","job_id":"abc123"}

# 2. Poll status
curl http://localhost:8765/download-status/abc123
# → {"status":"ok","filename":"...","size_mb":28.6,"download_url":"/downloads/..."}
```

## 📦 Dependencies
## 🔧 Troubleshooting
- **yt-dlp chậm**: Node.js cần được copy vào container sau restart
- **Whisper chậm**: Dùng model `tiny` cho sync-srt, `base` cho analyze
- **CORS error**: Kiểm tra `ALLOWED_ORIGINS` trong docker-compose.yml

## 📅 Changelog
- **v2** (2026-05-14): Async download, session memory, CORS fix, thêm 8 endpoints mới
- **v1** (2026-04-11): Base version với analyze + chat
