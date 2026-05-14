import os, base64, tempfile, subprocess, json, uuid, time, asyncio
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, Response
import httpx

app = FastAPI(title="Video-Claude Tool v2")

# CORS - chỉ cho phép nội bộ
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://192.168.100.2:8766,http://localhost:8766,http://127.0.0.1:8766").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET","POST","DELETE"],
    allow_headers=["*"],
    allow_credentials=True,
)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-20250514"
DOWNLOAD_DIR = Path("/data/downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
_download_jobs: dict = {}

# ── Session store: lưu frames + transcript để chat follow-up nhớ video ──
_sessions: dict[str, dict] = {}
SESSION_TTL = 3600  # 1 giờ

def clean_sessions():
    now = time.time()
    expired = [k for k,v in _sessions.items() if now - v.get("ts",0) > SESSION_TTL]
    for k in expired:
        del _sessions[k]

# ── Helpers ──────────────────────────────────────────────────────────────

def extract_frames(video_path: str, fps: float = 0.5, max_frames: int = 20) -> list[str]:
    with tempfile.TemporaryDirectory() as tmpdir:
        frame_pattern = os.path.join(tmpdir, "frame_%04d.jpg")
        subprocess.run([
            "ffmpeg", "-i", video_path,
            "-vf", f"fps={fps},scale=768:-1",
            "-q:v", "3", "-frames:v", str(max_frames),
            frame_pattern, "-y", "-loglevel", "error"
        ], capture_output=True, check=True)
        frames = []
        for f in sorted(Path(tmpdir).glob("frame_*.jpg"))[:max_frames]:
            frames.append(base64.b64encode(f.read_bytes()).decode())
        return frames

def transcribe_audio(video_path: str, language: str = "auto") -> str:
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, "audio.wav")
            subprocess.run([
                "ffmpeg", "-i", video_path,
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                audio_path, "-y", "-loglevel", "error"
            ], check=True, capture_output=True)
            lang_args = [] if language == "auto" else ["--language", language]
            result = subprocess.run([
                "whisper", audio_path, "--model", "base",
                "--output_dir", tmpdir, "--output_format", "txt"
            ] + lang_args, capture_output=True, text=True, timeout=300)
            if result.returncode == 0:
                txts = list(Path(tmpdir).glob("*.txt"))
                if txts:
                    return txts[0].read_text().strip()
        return ""
    except Exception as e:
        return f"[Transcription unavailable: {e}]"

def get_video_info(video_path: str) -> dict:
    try:
        r = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration,r_frame_rate",
            "-of", "json", video_path
        ], capture_output=True, text=True)
        data = json.loads(r.stdout)
        s = data.get("streams", [{}])[0]
        fps_str = s.get("r_frame_rate", "25/1")
        num, den = fps_str.split("/") if "/" in fps_str else (fps_str, "1")
        return {
            "width": s.get("width", 0), "height": s.get("height", 0),
            "duration": round(float(s.get("duration", 0)), 2),
            "fps": round(int(num)/int(den), 2)
        }
    except:
        return {}

def build_claude_content(frames: list, transcript: str, question: str) -> list:
    content = []
    if transcript and not transcript.startswith("[Transcription unavailable"):
        content.append({"type": "text", "text": f"**Transcript âm thanh:**\n{transcript}\n\n---"})
    content.append({"type": "text", "text": f"Dưới đây là {len(frames)} frame từ video:"})
    for b64 in frames:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})
    content.append({"type": "text", "text": f"\n**Câu hỏi:** {question}"})
    return content

async def stream_anthropic(payload: dict, key: str):
    async with httpx.AsyncClient(timeout=180) as client:
        async with client.stream(
            "POST", "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={**payload, "stream": True}
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                yield {"type": "error", "message": body.decode()}
                return
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    raw = line[6:]
                    if raw == "[DONE]": break
                    try:
                        ev = json.loads(raw)
                        if ev.get("type") == "content_block_delta":
                            d = ev.get("delta", {})
                            if d.get("type") == "text_delta":
                                yield {"type": "text", "text": d.get("text", "")}
                    except: pass
    yield {"type": "done"}

# ── Endpoints ─────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    deps = {}
    for tool in ["ffmpeg", "ffprobe", "whisper", "yt-dlp"]:
        deps[tool] = subprocess.run(["which", tool], capture_output=True).returncode == 0
    return {"status": "ok", "dependencies": deps, "api_key_set": bool(ANTHROPIC_API_KEY)}

@app.post("/analyze")
async def analyze_video(
    file: UploadFile = File(...),
    question: str = Form("Hãy mô tả nội dung video này"),
    fps: float = Form(0.5),
    max_frames: int = Form(16),
    use_transcript: bool = Form(True),
    api_key: str = Form(""),
    session_id: str = Form(""),
):
    key = api_key or ANTHROPIC_API_KEY
    if not key:
        raise HTTPException(400, "Cần API key")

    suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        info = get_video_info(tmp_path)
        frames = extract_frames(tmp_path, fps=fps, max_frames=max_frames)
        transcript = transcribe_audio(tmp_path) if use_transcript else ""

        # Lưu session để chat follow-up nhớ video
        sid = session_id or str(uuid.uuid4())
        clean_sessions()
        _sessions[sid] = {
            "frames": frames,
            "transcript": transcript,
            "ts": time.time(),
            "filename": file.filename,
        }

        content = build_claude_content(frames, transcript, question)
        payload = {
            "model": CLAUDE_MODEL, "max_tokens": 2048,
            "system": "Bạn là trợ lý AI phân tích video qua frames và transcript. Trả lời tiếng Việt, chi tiết.",
            "messages": [{"role": "user", "content": content}]
        }

        async def generate():
            yield json.dumps({"type": "info", "frames": len(frames), "transcript_available": bool(transcript and not transcript.startswith("[Transcription")), "video_info": info, "session_id": sid}) + "\n"
            full = ""
            async for ev in stream_anthropic(payload, key):
                if ev["type"] == "text":
                    full += ev["text"]
                yield json.dumps(ev) + "\n"
            # Lưu analysis vào session
            if sid in _sessions:
                _sessions[sid]["last_analysis"] = full

        return StreamingResponse(generate(), media_type="application/x-ndjson")
    finally:
        os.unlink(tmp_path)


@app.post("/chat")
async def chat_followup(
    question: str = Form(...),
    session_id: str = Form(""),
    context: str = Form(""),
    api_key: str = Form(""),
):
    key = api_key or ANTHROPIC_API_KEY
    if not key:
        raise HTTPException(400, "Cần API key")

    messages = []
    # Nếu có session → gửi lại frames để Claude nhớ video
    if session_id and session_id in _sessions:
        sess = _sessions[session_id]
        frames = sess.get("frames", [])
        transcript = sess.get("transcript", "")
        last_analysis = sess.get("last_analysis", "")
        if frames:
            # Gửi lại toàn bộ context video
            init_content = build_claude_content(frames, transcript, "Đây là video bạn đã phân tích trước đó.")
            messages.append({"role": "user", "content": init_content})
            messages.append({"role": "assistant", "content": last_analysis or "Tôi đã phân tích video này."})
        _sessions[session_id]["ts"] = time.time()
    elif context:
        # Fallback: dùng text context
        messages.append({"role": "assistant", "content": context[:3000]})

    messages.append({"role": "user", "content": question})

    payload = {
        "model": CLAUDE_MODEL, "max_tokens": 1024,
        "system": "Bạn là trợ lý AI đã phân tích video. Trả lời tiếng Việt.",
        "messages": messages,
    }

    async def generate():
        async for ev in stream_anthropic(payload, key):
            yield json.dumps(ev) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@app.post("/sync-srt")
async def sync_srt(
    file: UploadFile = File(...),
    srt_content: str = Form(...),
    language: str = Form("auto"),
):
    suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, "audio.wav")
            subprocess.run(["ffmpeg", "-i", tmp_path, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", audio_path, "-y", "-loglevel", "error"], check=True, capture_output=True)
            lang_args = [] if language == "auto" else ["--language", language]
            result = subprocess.run(["whisper", audio_path, "--model", "tiny", "--word_timestamps", "True", "--output_format", "srt", "--output_dir", tmpdir] + lang_args, capture_output=True, text=True, timeout=600)
            srt_files = list(Path(tmpdir).glob("*.srt"))
            if srt_files:
                return {"status": "ok", "srt": srt_files[0].read_text().strip()}
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
    silence_duration: float = Form(0.3),
):
    with tempfile.NamedTemporaryFile(suffix=Path(file.filename or "v.mp4").suffix or ".mp4", delete=False, dir="/tmp") as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", tmp_path], capture_output=True, text=True)
        total = float(probe.stdout.strip())
        silence = subprocess.run(["ffmpeg", "-i", tmp_path, "-af", f"silencedetect=noise={silence_threshold}:d={silence_duration}", "-f", "null", "-"], capture_output=True, text=True, timeout=120)
        silence_ends = []
        for line in silence.stderr.split('\n'):
            if 'silence_end' in line:
                try:
                    silence_ends.append(float(line.split('silence_end:')[1].split()[0]))
                except: pass
        seg_secs = segment_minutes * 60
        cuts = [0.0]
        cur = seg_secs
        while cur < total:
            best = min(silence_ends, key=lambda s: abs(s-cur), default=None)
            cuts.append(round(best if best and abs(best-cur)<30 else cur, 2))
            cur += seg_secs
        cuts.append(round(total, 2))
        def fmt(s): return f"{int(s//3600):02d}:{int((s%3600)//60):02d}:{s%60:06.3f}"
        segments = [{"index":i+1,"start":cuts[i],"end":cuts[i+1],"duration":round(cuts[i+1]-cuts[i],2),"start_fmt":fmt(cuts[i]),"end_fmt":fmt(cuts[i+1])} for i in range(len(cuts)-1)]
        return {"status":"ok","total_duration":round(total,2),"total_segments":len(segments),"silence_points":len(silence_ends),"segments":segments,"filename":file.filename}
    except Exception as e:
        return {"status":"error","message":str(e)}
    finally:
        os.unlink(tmp_path)


@app.post("/cut-segment")
async def cut_segment(file: UploadFile = File(...), start: float = Form(...), end: float = Form(...), index: int = Form(...)):
    suffix = Path(file.filename or "v.mp4").suffix or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, dir="/tmp") as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    out_path = tmp_path + f"_part{index}{suffix}"
    try:
        subprocess.run(["ffmpeg", "-i", tmp_path, "-ss", str(start), "-to", str(end), "-c", "copy", out_path, "-y", "-loglevel", "error"], check=True, capture_output=True, timeout=300)
        data = Path(out_path).read_bytes()
        return Response(content=data, media_type="video/mp4", headers={"Content-Disposition": f"attachment; filename=part{index}{suffix}"})
    except Exception as e:
        return {"status":"error","message":str(e)}
    finally:
        os.unlink(tmp_path)
        if os.path.exists(out_path): os.unlink(out_path)


@app.post("/download-video")
async def download_video(url: str = Form(...), quality: str = Form("best")):
    job_id = str(uuid.uuid4())[:8]
    out_tpl = str(DOWNLOAD_DIR / f"{job_id}_%(title)s.%(ext)s")
    _download_jobs[job_id] = {"status": "downloading", "job_id": job_id}

    async def _run():
        try:
            proc = await asyncio.create_subprocess_exec(
                "yt-dlp", "--no-playlist", "--js-runtimes", "node",
                "-f", "best[height<=720][ext=mp4]/best[ext=mp4]/worst",
                "--merge-output-format", "mp4", "-o", out_tpl, url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _out, _err = await asyncio.wait_for(proc.communicate(), timeout=1800)
            if proc.returncode != 0:
                _download_jobs[job_id] = {"status":"error","job_id":job_id,"message":_err.decode()[-300:]}
                return
            files = list(DOWNLOAD_DIR.glob(f"{job_id}_*.mp4"))
            if files:
                f = files[0]
                _download_jobs[job_id] = {
                    "status":"ok","job_id":job_id,
                    "filename":f.name,
                    "size_mb":round(f.stat().st_size/1024/1024,1),
                    "download_url":f"/downloads/{f.name}"
                }
            else:
                _download_jobs[job_id] = {"status":"error","job_id":job_id,"message":"File not found"}
        except Exception as e:
            _download_jobs[job_id] = {"status":"error","job_id":job_id,"message":str(e)}

    asyncio.create_task(_run())
    return {"status":"started","job_id":job_id,"poll_url":f"/download-status/{job_id}"}

@app.get("/download-status/{job_id}")
async def download_status(job_id: str):
    return _download_jobs.get(job_id, {"status":"not_found","job_id":job_id})

@app.get("/downloads/{filename}")
async def serve_download(filename: str):
    p = DOWNLOAD_DIR / filename
    if not p.exists():
        raise HTTPException(404, "File không tồn tại")
    return FileResponse(str(p), filename=filename, media_type="video/mp4")


@app.post("/convert-video")
async def convert_video(file: UploadFile = File(...), format: str = Form("mp4"), codec: str = Form("h264"), ratio: str = Form("original"), preset: str = Form("medium")):
    suffix = Path(file.filename or "v.mp4").suffix or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, dir="/tmp") as tmp:
        tmp.write(await file.read()); tmp_path = tmp.name
    out_path = tmp_path + f"_converted.{format}"
    try:
        vf = []
        ratio_map = {"16:9":"scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2","9:16":"scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2","1:1":"scale=1080:1080:force_original_aspect_ratio=decrease,pad=1080:1080:(ow-iw)/2:(oh-ih)/2"}
        if ratio in ratio_map: vf.append(ratio_map[ratio])
        codec_map = {"h264":"libx264","h265":"libx265","vp9":"libvp9"}
        cmd = ["ffmpeg","-i",tmp_path]
        if vf: cmd += ["-vf",",".join(vf)]
        cmd += ["-c:v",codec_map.get(codec,"libx264"),"-preset",preset,"-c:a","aac","-y",out_path,"-loglevel","error"]
        r = subprocess.run(cmd, capture_output=True, timeout=600)
        if r.returncode != 0: raise RuntimeError(r.stderr.decode()[:500])
        data = Path(out_path).read_bytes()
        return Response(content=data, media_type="video/mp4", headers={"Content-Disposition": f"attachment; filename=converted_{Path(file.filename or 'video').stem}.{format}"})
    except Exception as e:
        return {"status":"error","message":str(e)}
    finally:
        os.unlink(tmp_path)
        if os.path.exists(out_path): os.unlink(out_path)


@app.post("/merge-videos")
async def merge_videos(files: list[UploadFile] = File(...)):
    tmp_paths = []
    list_path = out_path = None
    try:
        for f in files:
            with tempfile.NamedTemporaryFile(suffix=Path(f.filename or "v.mp4").suffix or ".mp4", delete=False, dir="/tmp") as tmp:
                tmp.write(await f.read()); tmp_paths.append(tmp.name)
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, dir="/tmp", mode='w') as lf:
            for p in tmp_paths: lf.write(f"file '{p}'\n")
            list_path = lf.name
        out_path = tmp_paths[0] + "_merged.mp4"
        r = subprocess.run(["ffmpeg","-f","concat","-safe","0","-i",list_path,"-c","copy",out_path,"-y","-loglevel","error"], capture_output=True, timeout=600)
        if r.returncode != 0: raise RuntimeError(r.stderr.decode()[:500])
        data = Path(out_path).read_bytes()
        return Response(content=data, media_type="video/mp4", headers={"Content-Disposition":"attachment; filename=merged.mp4"})
    except Exception as e:
        return {"status":"error","message":str(e)}
    finally:
        for p in tmp_paths:
            if os.path.exists(p): os.unlink(p)
        if list_path and os.path.exists(list_path): os.unlink(list_path)
        if out_path and os.path.exists(out_path): os.unlink(out_path)


@app.post("/add-music")
async def add_music(video: UploadFile = File(...), music: UploadFile = File(...), video_volume: float = Form(0.3), music_volume: float = Form(0.7)):
    with tempfile.NamedTemporaryFile(suffix=Path(video.filename or "v.mp4").suffix or ".mp4", delete=False, dir="/tmp") as v:
        v.write(await video.read()); vp = v.name
    with tempfile.NamedTemporaryFile(suffix=Path(music.filename or "m.mp3").suffix or ".mp3", delete=False, dir="/tmp") as m:
        m.write(await music.read()); mp = m.name
    out = vp + "_music.mp4"
    try:
        r = subprocess.run([
            "ffmpeg","-i",vp,"-i",mp,
            "-filter_complex",f"[0:a]volume={video_volume}[a1];[1:a]volume={music_volume}[a2];[a1][a2]amix=inputs=2:duration=first[aout]",
            "-map","0:v","-map","[aout]","-c:v","copy","-shortest",out,"-y","-loglevel","error"
        ], capture_output=True, timeout=600)
        if r.returncode != 0: raise RuntimeError(r.stderr.decode()[:500])
        data = Path(out).read_bytes()
        return Response(content=data, media_type="video/mp4", headers={"Content-Disposition":"attachment; filename=video_with_music.mp4"})
    except Exception as e:
        return {"status":"error","message":str(e)}
    finally:
        for p in [vp, mp, out]:
            if os.path.exists(p): os.unlink(p)


@app.post("/compress-video")
async def compress_video(file: UploadFile = File(...), preset: str = Form("youtube")):
    presets = {
        "youtube": {"crf":"23","res":"1920:1080","bitrate":"8000k"},
        "facebook": {"crf":"28","res":"1280:720","bitrate":"4000k"},
        "mobile":   {"crf":"32","res":"854:480","bitrate":"1500k"},
    }
    p = presets.get(preset, presets["youtube"])
    suffix = Path(file.filename or "v.mp4").suffix or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, dir="/tmp") as tmp:
        tmp.write(await file.read()); tmp_path = tmp.name
    out = tmp_path + f"_{preset}.mp4"
    try:
        r = subprocess.run([
            "ffmpeg","-i",tmp_path,"-c:v","libx264","-crf",p["crf"],
            "-maxrate",p["bitrate"],"-bufsize",p["bitrate"],
            "-vf",f"scale={p['res']}:force_original_aspect_ratio=decrease",
            "-c:a","aac","-b:a","192k",out,"-y","-loglevel","error"
        ], capture_output=True, timeout=600)
        if r.returncode != 0: raise RuntimeError(r.stderr.decode()[:500])
        data = Path(out).read_bytes()
        stem = Path(file.filename or "video").stem
        return Response(content=data, media_type="video/mp4", headers={"Content-Disposition":f"attachment; filename={stem}_{preset}.mp4"})
    except Exception as e:
        return {"status":"error","message":str(e)}
    finally:
        for pp in [tmp_path, out]:
            if os.path.exists(pp): os.unlink(pp)

@app.delete("/session/{session_id}")
def delete_session(session_id: str):
    _sessions.pop(session_id, None)
    return {"deleted": session_id}
