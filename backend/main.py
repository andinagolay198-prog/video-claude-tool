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

@app.post("/sync-srt")
async def sync_srt(
    file: UploadFile = File(...),
    srt_content: str = Form(...),
    language: str = Form("auto")
):
    """Sync SRT timestamps với audio thật dùng Whisper word-level timestamps"""
    suffix = Path(file.filename).suffix or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Extract audio
            audio_path = os.path.join(tmpdir, "audio.wav")
            subprocess.run([
                "ffmpeg", "-i", tmp_path,
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                audio_path, "-y", "-loglevel", "error"
            ], check=True, capture_output=True)

            # Whisper với word timestamps
            lang_arg = [] if language == "auto" else ["--language", language]
            result = subprocess.run([
                "whisper", audio_path,
                "--model", "tiny",
                "--word_timestamps", "True",
                "--output_format", "srt",
                "--output_dir", tmpdir
            ] + lang_arg, capture_output=True, text=True, timeout=600)

            srt_files = list(Path(tmpdir).glob("*.srt"))
            if srt_files:
                whisper_srt = srt_files[0].read_text().strip()
                return {"status": "ok", "srt": whisper_srt}
            else:
                return {"status": "error", "message": "Whisper không tạo được SRT", "srt": srt_content}

    except Exception as e:
        return {"status": "error", "message": str(e), "srt": srt_content}
    finally:
        os.unlink(tmp_path)

@app.post("/split-video")
async def split_video(
    file: UploadFile = File(...),
    segment_minutes: float = Form(10.0),
    silence_threshold: str = Form("-35dB"),
    silence_duration: float = Form(0.3)
):
    suffix = Path(file.filename).suffix or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, dir="/tmp") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "csv=p=0", tmp_path
        ], capture_output=True, text=True)
        total_duration = float(probe.stdout.strip())

        silence_result = subprocess.run([
            "ffmpeg", "-i", tmp_path,
            "-af", f"silencedetect=noise={silence_threshold}:d={silence_duration}",
            "-f", "null", "-"
        ], capture_output=True, text=True, timeout=120)

        silence_ends = []
        for line in silence_result.stderr.split('\n'):
            if 'silence_end' in line:
                try:
                    t = float(line.split('silence_end:')[1].split()[0])
                    silence_ends.append(t)
                except:
                    pass

        segment_secs = segment_minutes * 60
        cut_points = [0.0]
        current = segment_secs

        while current < total_duration:
            best = min(silence_ends, key=lambda s: abs(s - current), default=None)
            if best and abs(best - current) < 30:
                cut_points.append(round(best, 2))
            else:
                cut_points.append(round(current, 2))
            current += segment_secs

        cut_points.append(round(total_duration, 2))

        segments = []
        for i in range(len(cut_points) - 1):
            start = cut_points[i]
            end = cut_points[i+1]
            def fmt(s):
                return f"{int(s//3600):02d}:{int((s%3600)//60):02d}:{s%60:06.3f}"
            segments.append({
                "index": i + 1,
                "start": start,
                "end": end,
                "duration": round(end - start, 2),
                "start_fmt": fmt(start),
                "end_fmt": fmt(end)
            })

        return {
            "status": "ok",
            "total_duration": round(total_duration, 2),
            "total_segments": len(segments),
            "silence_points": len(silence_ends),
            "segments": segments,
            "filename": file.filename
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        os.unlink(tmp_path)

@app.post("/cut-segment")
async def cut_segment(
    file: UploadFile = File(...),
    start: float = Form(...),
    end: float = Form(...),
    index: int = Form(...)
):
    suffix = Path(file.filename).suffix or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, dir="/tmp") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    out_path = tmp_path + f"_part{index}{suffix}"
    try:
        subprocess.run([
            "ffmpeg", "-i", tmp_path,
            "-ss", str(start), "-to", str(end),
            "-c", "copy",
            out_path, "-y", "-loglevel", "error"
        ], check=True, capture_output=True, timeout=300)

        with open(out_path, "rb") as f:
            data = f.read()

        from fastapi.responses import Response
        return Response(
            content=data,
            media_type="video/mp4",
            headers={"Content-Disposition": f"attachment; filename=part{index}{suffix}"}
        )
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        os.unlink(tmp_path)
        if os.path.exists(out_path):
            os.unlink(out_path)

# ============ DOWNLOAD VIDEO ============
@app.post("/download-video")
async def download_video(
    url: str = Form(...),
    quality: str = Form("best")
):
    """Download video từ YouTube/TikTok via yt-dlp"""
    try:
        with tempfile.TemporaryDirectory(dir="/tmp") as tmpdir:
            cmd = [
                "yt-dlp",
                "--no-playlist",
                "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "--merge-output-format", "mp4",
                "-o", os.path.join(tmpdir, "%(title)s.%(ext)s"),
                url
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                return {"status": "error", "message": result.stderr[-500:]}

            files = list(Path(tmpdir).glob("*.mp4"))
            if not files:
                return {"status": "error", "message": "Không tìm thấy file sau download"}

            video_path = files[0]
            with open(video_path, "rb") as f:
                data = f.read()

            from fastapi.responses import Response
            return Response(
                content=data,
                media_type="video/mp4",
                headers={"Content-Disposition": f"attachment; filename={video_path.name}"}
            )
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ============ CONVERT VIDEO ============
@app.post("/convert-video")
async def convert_video(
    file: UploadFile = File(...),
    format: str = Form("mp4"),
    codec: str = Form("h264"),
    ratio: str = Form("original"),
    preset: str = Form("medium")
):
    suffix = Path(file.filename).suffix or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, dir="/tmp") as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    out_path = tmp_path + f"_converted.{format}"
    try:
        # Build ffmpeg command
        vf_filters = []
        if ratio == "16:9":
            vf_filters.append("scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2")
        elif ratio == "9:16":
            vf_filters.append("scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2")
        elif ratio == "1:1":
            vf_filters.append("scale=1080:1080:force_original_aspect_ratio=decrease,pad=1080:1080:(ow-iw)/2:(oh-ih)/2")

        codec_map = {"h264": "libx264", "h265": "libx265", "vp9": "libvp9"}
        vcodec = codec_map.get(codec, "libx264")

        cmd = ["ffmpeg", "-i", tmp_path]
        if vf_filters:
            cmd += ["-vf", ",".join(vf_filters)]
        cmd += ["-c:v", vcodec, "-preset", preset, "-c:a", "aac", "-y", out_path, "-loglevel", "error"]

        result = subprocess.run(cmd, capture_output=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode()[:500])

        with open(out_path, "rb") as f:
            data = f.read()

        from fastapi.responses import Response
        return Response(
            content=data,
            media_type="video/mp4",
            headers={"Content-Disposition": f"attachment; filename=converted_{Path(file.filename).stem}.{format}"}
        )
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        os.unlink(tmp_path)
        if os.path.exists(out_path): os.unlink(out_path)


# ============ MERGE VIDEOS ============
@app.post("/merge-videos")
async def merge_videos(files: list[UploadFile] = File(...)):
    tmp_paths = []
    try:
        for f in files:
            with tempfile.NamedTemporaryFile(suffix=Path(f.filename).suffix or ".mp4", delete=False, dir="/tmp") as tmp:
                tmp.write(await f.read())
                tmp_paths.append(tmp.name)

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, dir="/tmp", mode='w') as list_file:
            for p in tmp_paths:
                list_file.write(f"file '{p}'\n")
            list_path = list_file.name

        out_path = tmp_paths[0] + "_merged.mp4"
        result = subprocess.run([
            "ffmpeg", "-f", "concat", "-safe", "0",
            "-i", list_path, "-c", "copy", out_path, "-y", "-loglevel", "error"
        ], capture_output=True, timeout=600)

        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode()[:500])

        with open(out_path, "rb") as f:
            data = f.read()

        from fastapi.responses import Response
        return Response(content=data, media_type="video/mp4",
            headers={"Content-Disposition": "attachment; filename=merged.mp4"})
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        for p in tmp_paths:
            if os.path.exists(p): os.unlink(p)
        if os.path.exists(list_path): os.unlink(list_path)
        if os.path.exists(out_path): os.unlink(out_path)


# ============ ADD MUSIC ============
@app.post("/add-music")
async def add_music(
    video: UploadFile = File(...),
    music: UploadFile = File(...),
    video_volume: float = Form(0.3),
    music_volume: float = Form(0.7)
):
    with tempfile.NamedTemporaryFile(suffix=Path(video.filename).suffix or ".mp4", delete=False, dir="/tmp") as v:
        v.write(await video.read())
        video_path = v.name
    with tempfile.NamedTemporaryFile(suffix=Path(music.filename).suffix or ".mp3", delete=False, dir="/tmp") as m:
        m.write(await music.read())
        music_path = m.name

    out_path = video_path + "_with_music.mp4"
    try:
        result = subprocess.run([
            "ffmpeg", "-i", video_path, "-i", music_path,
            "-filter_complex",
            f"[0:a]volume={video_volume}[a1];[1:a]volume={music_volume}[a2];[a1][a2]amix=inputs=2:duration=first[aout]",
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-shortest",
            out_path, "-y", "-loglevel", "error"
        ], capture_output=True, timeout=600)

        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode()[:500])

        with open(out_path, "rb") as f:
            data = f.read()

        from fastapi.responses import Response
        return Response(content=data, media_type="video/mp4",
            headers={"Content-Disposition": "attachment; filename=video_with_music.mp4"})
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        for p in [video_path, music_path, out_path]:
            if os.path.exists(p): os.unlink(p)


# ============ COMPRESS VIDEO ============
@app.post("/compress-video")
async def compress_video(
    file: UploadFile = File(...),
    preset: str = Form("youtube")
):
    presets = {
        "youtube": {"crf": "23", "res": "1920:1080", "bitrate": "8000k"},
        "facebook": {"crf": "28", "res": "1280:720", "bitrate": "4000k"},
        "mobile":   {"crf": "32", "res": "854:480",  "bitrate": "1500k"},
    }
    p = presets.get(preset, presets["youtube"])

    suffix = Path(file.filename).suffix or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, dir="/tmp") as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    out_path = tmp_path + f"_{preset}.mp4"
    try:
        result = subprocess.run([
            "ffmpeg", "-i", tmp_path,
            "-c:v", "libx264", "-crf", p["crf"],
            "-maxrate", p["bitrate"], "-bufsize", p["bitrate"],
            "-vf", f"scale={p['res']}:force_original_aspect_ratio=decrease",
            "-c:a", "aac", "-b:a", "192k",
            out_path, "-y", "-loglevel", "error"
        ], capture_output=True, timeout=600)

        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode()[:500])

        with open(out_path, "rb") as f:
            data = f.read()

        from fastapi.responses import Response
        return Response(content=data, media_type="video/mp4",
            headers={"Content-Disposition": f"attachment; filename={Path(file.filename).stem}_{preset}.mp4"})
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        for p in [tmp_path, out_path]:
            if os.path.exists(p): os.unlink(p)
