import os, base64, tempfile, subprocess, json, asyncio
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import httpx

app = FastAPI(title="Video-Claude Tool")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-20250514"

def extract_frames(video_path: str, fps: float = 1.0, max_frames: int = 20) -> list[str]:
    """Extract frames from video using ffmpeg, returns list of base64 images"""
    with tempfile.TemporaryDirectory() as tmpdir:
        frame_pattern = os.path.join(tmpdir, "frame_%04d.jpg")
        cmd = [
            "ffmpeg", "-i", video_path,
            "-vf", f"fps={fps},scale=768:-1",
            "-q:v", "3",
            "-frames:v", str(max_frames),
            frame_pattern,
            "-y", "-loglevel", "error"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg error: {result.stderr}")

        frames = []
        frame_files = sorted(Path(tmpdir).glob("frame_*.jpg"))[:max_frames]
        for f in frame_files:
            with open(f, "rb") as img_file:
                b64 = base64.b64encode(img_file.read()).decode()
                frames.append(b64)
        return frames

def transcribe_audio(video_path: str) -> str:
    """Transcribe audio using whisper CLI"""
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, "audio.wav")
            # Extract audio
            cmd_audio = [
                "ffmpeg", "-i", video_path,
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                audio_path, "-y", "-loglevel", "error"
            ]
            subprocess.run(cmd_audio, check=True, capture_output=True)

            # Try whisper
            cmd_whisper = ["whisper", audio_path, "--model", "base", "--output_dir", tmpdir,
                           "--output_format", "txt", "--language", "auto"]
            result = subprocess.run(cmd_whisper, capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                txt_files = list(Path(tmpdir).glob("*.txt"))
                if txt_files:
                    return txt_files[0].read_text().strip()
            return ""
    except Exception as e:
        return f"[Transcription unavailable: {str(e)}]"

def get_video_info(video_path: str) -> dict:
    """Get video metadata using ffprobe"""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration,r_frame_rate",
            "-of", "json", video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        data = json.loads(result.stdout)
        streams = data.get("streams", [{}])
        s = streams[0] if streams else {}
        fps_str = s.get("r_frame_rate", "25/1")
        num, den = fps_str.split("/") if "/" in fps_str else (fps_str, "1")
        fps = round(int(num) / int(den), 2)
        return {
            "width": s.get("width", 0),
            "height": s.get("height", 0),
            "duration": round(float(s.get("duration", 0)), 2),
            "fps": fps
        }
    except:
        return {}

@app.get("/health")
def health():
    # Check dependencies
    deps = {}
    for tool in ["ffmpeg", "ffprobe", "whisper"]:
        r = subprocess.run(["which", tool], capture_output=True)
        deps[tool] = r.returncode == 0
    return {"status": "ok", "dependencies": deps, "api_key_set": bool(ANTHROPIC_API_KEY)}

@app.post("/analyze")
async def analyze_video(
    file: UploadFile = File(...),
    question: str = Form("Hãy mô tả nội dung video này"),
    fps: float = Form(0.5),
    max_frames: int = Form(16),
    use_transcript: bool = Form(True),
    api_key: str = Form("")
):
    key = api_key or ANTHROPIC_API_KEY
    if not key:
        raise HTTPException(400, "Cần cung cấp Anthropic API key")

    suffix = Path(file.filename).suffix or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # Get video info
        info = get_video_info(tmp_path)

        # Extract frames
        frames = extract_frames(tmp_path, fps=fps, max_frames=max_frames)

        # Transcribe
        transcript = ""
        if use_transcript:
            transcript = transcribe_audio(tmp_path)

        # Build Claude messages
        content_parts = []

        if transcript and not transcript.startswith("[Transcription unavailable"):
            content_parts.append({
                "type": "text",
                "text": f"**Transcript âm thanh từ video:**\n{transcript}\n\n---\n"
            })

        content_parts.append({
            "type": "text",
            "text": f"Dưới đây là {len(frames)} frame được trích xuất từ video (mỗi {1/fps:.1f}s một frame):"
        })

        for i, frame_b64 in enumerate(frames):
            content_parts.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": frame_b64
                }
            })

        content_parts.append({
            "type": "text",
            "text": f"\n**Câu hỏi:** {question}"
        })

        payload = {
            "model": CLAUDE_MODEL,
            "max_tokens": 2048,
            "system": "Bạn là trợ lý AI phân tích video thông qua các frame ảnh và transcript. Hãy trả lời bằng tiếng Việt (hoặc ngôn ngữ của người dùng), chi tiết và chính xác.",
            "messages": [{"role": "user", "content": content_parts}]
        }

        async def stream_response():
            yield json.dumps({
                "type": "info",
                "frames": len(frames),
                "transcript_available": bool(transcript and not transcript.startswith("[Transcription")),
                "video_info": info
            }) + "\n"

            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST",
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json"
                    },
                    json={**payload, "stream": True}
                ) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        yield json.dumps({"type": "error", "message": body.decode()}) + "\n"
                        return
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                break
                            try:
                                event = json.loads(data_str)
                                if event.get("type") == "content_block_delta":
                                    delta = event.get("delta", {})
                                    if delta.get("type") == "text_delta":
                                        yield json.dumps({
                                            "type": "text",
                                            "text": delta.get("text", "")
                                        }) + "\n"
                            except:
                                pass

            yield json.dumps({"type": "done"}) + "\n"

        return StreamingResponse(stream_response(), media_type="application/x-ndjson")

    finally:
        os.unlink(tmp_path)

@app.post("/chat")
async def chat_followup(
    question: str = Form(...),
    context: str = Form(""),
    api_key: str = Form("")
):
    """Follow-up chat after video analysis"""
    key = api_key or ANTHROPIC_API_KEY
    if not key:
        raise HTTPException(400, "Cần API key")

    messages = []
    if context:
        messages.append({"role": "assistant", "content": context})
    messages.append({"role": "user", "content": question})

    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": 1024,
        "system": "Bạn là trợ lý AI đã phân tích một video. Tiếp tục trả lời các câu hỏi liên quan.",
        "messages": messages,
        "stream": True
    }

    async def stream():
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream("POST",
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json=payload
            ) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]": break
                        try:
                            ev = json.loads(data_str)
                            if ev.get("type") == "content_block_delta":
                                d = ev.get("delta", {})
                                if d.get("type") == "text_delta":
                                    yield json.dumps({"type": "text", "text": d.get("text", "")}) + "\n"
                        except: pass
        yield json.dumps({"type": "done"}) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")
