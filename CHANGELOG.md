# Changelog

## v2.0.0 (2026-05-14)

### Added
- `POST /sync-srt` — Sync timestamp subtitle bằng Whisper word-level
- `POST /split-video` — Phân tích điểm cắt theo silence detection
- `POST /cut-segment` — Cắt đoạn video bằng ffmpeg
- `POST /download-video` — Download YouTube/TikTok async (polling)
- `GET /download-status/{job_id}` — Poll trạng thái download
- `POST /convert-video` — Chuyển codec H.264/H.265/VP9, tỷ lệ 16:9/9:16/1:1
- `POST /merge-videos` — Ghép nhiều video
- `POST /add-music` — Ghép nhạc nền với volume control
- `POST /compress-video` — Nén cho YouTube/Facebook/Mobile
- Session memory: `/chat` gửi lại frames để Claude nhớ video
- Node.js runtime cho yt-dlp (không cần deno)

### Fixed
- CORS từ `allow_origins=["*"]` → chỉ origins nội bộ
- Frontend hardcode IP → relative `/api/`
- Download async thay vì blocking (timeout fix)
- Uvicorn 1 worker để tránh job store bị split

### Security
- API key từ `.env`, không hardcode
- CORS restrict theo `ALLOWED_ORIGINS` env var
- Session auto-expire sau 1 giờ

## v1.0.0 (2026-04-11)
- Base version: analyze video + chat follow-up
- ffmpeg frame extraction + Whisper transcription
- Claude Vision API integration
