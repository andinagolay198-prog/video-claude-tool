# 🎬 Video × Claude Tool

Công cụ phân tích video thông qua Claude AI — trích xuất frames bằng ffmpeg, transcribe âm thanh bằng Whisper, sau đó gửi tới Claude API.

## Workflow
```
Video Upload
    │
    ├─→ ffmpeg → Trích xuất frames (ảnh)  ──┐
    │                                        ├─→ Claude API → Phân tích + Trả lời
    └─→ Whisper → Transcribe âm thanh ──────┘
```

## Cài đặt

### 1. Cài dependencies hệ thống

**macOS:**
```bash
brew install ffmpeg
pip3 install openai-whisper
```

**Ubuntu/Debian:**
```bash
sudo apt-get install ffmpeg
pip3 install openai-whisper
```

**Windows:**
- Tải ffmpeg: https://ffmpeg.org/download.html (thêm vào PATH)
- `pip install openai-whisper`

### 2. Cài Python packages
```bash
cd backend
pip install -r requirements.txt
```

### 3. Chạy backend
```bash
# Cách 1: Script tự động
chmod +x start.sh && ./start.sh

# Cách 2: Thủ công
cd backend
export ANTHROPIC_API_KEY="sk-ant-..."
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 4. Mở frontend
```
Mở file: frontend/index.html trong trình duyệt
```

## Sử dụng

1. **Upload video** — kéo thả hoặc click chọn file (MP4, MOV, AVI, MKV...)
2. **Nhập API key** — Anthropic API key (sk-ant-...)
3. **Cài đặt** — điều chỉnh frame rate và số frames tối đa
4. **Đặt câu hỏi** — VD: "Mô tả nội dung video", "Có bao nhiêu người?"
5. **Phân tích** — click nút hoặc Ctrl+Enter
6. **Hỏi thêm** — tiếp tục chat về video

## API Endpoints

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| GET | /health | Kiểm tra trạng thái |
| POST | /analyze | Phân tích video (streaming) |
| POST | /chat | Chat follow-up |

## Cấu trúc project

```
video-claude-tool/
├── backend/
│   ├── main.py          # FastAPI server
│   └── requirements.txt
├── frontend/
│   └── index.html       # UI
├── start.sh             # Script khởi động
└── README.md
```

## Lưu ý

- API key chỉ dùng tại client, không lưu trên server
- Whisper model `base` được dùng mặc định (đủ tốt cho hầu hết video)
- Nếu thiếu Whisper, transcript sẽ bỏ qua nhưng phân tích frames vẫn hoạt động
- Mỗi frame ~100KB — 16 frames ≈ 1.6MB gửi lên Claude
