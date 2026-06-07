#!/usr/bin/env python3
"""
Littlebat AI — portable voice agent
Raspberry Pi 5 + Whisplay HAT → Amazon Bedrock AgentCore
"""

import asyncio
import json
import os
import re
import socket
import struct
import subprocess
import sys
import tempfile
import time
import textwrap
import uuid
import wave
from pathlib import Path

import boto3
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent / "whisplay"))
from runtime.whisplay import WhisplayBoard

# ── Config ────────────────────────────────────────────────────────────────────
REGION             = os.environ.get("AWS_REGION", "us-east-1")
AGENT_RUNTIME_ARN  = os.environ.get("AGENTCORE_RUNTIME_ARN", "")
POLLY_VOICE        = os.environ.get("POLLY_VOICE", "Matthew")
RECORD_RATE        = int(os.environ.get("RECORD_RATE", "16000"))
RECORD_CHANNELS    = int(os.environ.get("RECORD_CHANNELS", "1"))
MAX_RECORD_SEC     = 60

W, H = 240, 280

# ── Colors ────────────────────────────────────────────────────────────────────
BG   = (8,  12,  32)
TEXT = (200, 220, 255)
DIM  = (80,  90, 140)
WARN = (255, 140,  40)
ERR  = (255,  60,  60)

# ── Display ───────────────────────────────────────────────────────────────────
def _to_rgb565(img: Image.Image) -> bytes:
    """Convert PIL RGB image to packed big-endian RGB565 bytes."""
    buf = bytearray(W * H * 2)
    idx = 0
    for r, g, b in img.getdata():
        v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        struct.pack_into(">H", buf, idx, v)
        idx += 2
    return bytes(buf)

def _fonts():
    try:
        bold = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
        body = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
    except OSError:
        bold = body = ImageFont.load_default()
    return bold, body

def draw_screen(board: WhisplayBoard, title: str, body: str = "",
                title_color: tuple = TEXT) -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    bold, body_font = _fonts()

    d.text((10, 8), title, fill=title_color, font=bold)
    d.line([(10, 30), (230, 30)], fill=DIM, width=1)

    y = 38
    for line in textwrap.wrap(body, width=28):
        d.text((10, y), line, fill=DIM, font=body_font)
        y += 17
        if y > H - 10:
            break

    board.draw_image(0, 0, W, H, _to_rgb565(img))

# ── Connectivity ──────────────────────────────────────────────────────────────
def online(host="8.8.8.8", port=53, timeout=3) -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except OSError:
        return False

# ── Audio ─────────────────────────────────────────────────────────────────────
def record_while_held(board: WhisplayBoard) -> str:
    path = tempfile.mktemp(suffix=".wav")
    proc = subprocess.Popen(
        ["arecord", "-D", "default", "-f", "S16_LE",
         "-c", str(RECORD_CHANNELS), "-r", str(RECORD_RATE), path],
        stderr=subprocess.DEVNULL,
    )
    start = time.time()
    while board.button_pressed() and (time.time() - start) < MAX_RECORD_SEC:
        time.sleep(0.05)
    proc.terminate()
    proc.wait()
    return path

def play_wav(path: str) -> None:
    subprocess.run(["aplay", "-D", "default", "-q", path], check=False)

# ── STT — Amazon Transcribe Streaming ────────────────────────────────────────
async def _transcribe(wav_path: str) -> str:
    from amazon_transcribe.client import TranscribeStreamingClient
    from amazon_transcribe.handlers import TranscriptResultStreamHandler
    from amazon_transcribe.model import TranscriptEvent

    class Collector(TranscriptResultStreamHandler):
        def __init__(self, stream):
            super().__init__(stream)
            self.parts = []

        async def handle_transcript_event(self, event: TranscriptEvent):
            for result in event.transcript.results:
                if not result.is_partial:
                    self.parts.append(result.alternatives[0].transcript)

    client = TranscribeStreamingClient(region=REGION)
    stream = await client.start_stream_transcription(
        language_code="en-US",
        media_sample_rate_hz=RECORD_RATE,
        media_encoding="pcm",
    )
    collector = Collector(stream.output_stream)

    async def send_audio():
        with wave.open(wav_path, "rb") as wf:
            while True:
                frames = wf.readframes(4096)
                if not frames:
                    break
                await stream.input_stream.send_audio_event(audio_chunk=frames)
        await stream.input_stream.end_stream()

    await asyncio.gather(send_audio(), collector.handle_events())
    return " ".join(collector.parts).strip()

def transcribe(wav_path: str) -> str:
    return asyncio.run(_transcribe(wav_path))

# ── Agent — Bedrock AgentCore ─────────────────────────────────────────────────
def _parse_runtime_arn(arn: str) -> tuple:
    """Return (runtime_id, account_id) from a full AgentCore runtime ARN.
    Using just the runtime ID (not the full ARN) avoids URL-encoding issues
    where ':' and '/' in the ARN break path routing on the service side.
    """
    parts = arn.split(":")
    account_id = parts[4]
    runtime_id = parts[5].split("/", 1)[1]
    return runtime_id, account_id

def invoke_agent(text: str, session_id: str) -> str:
    client = boto3.client("bedrock-agentcore", region_name=REGION)
    runtime_id, account_id = _parse_runtime_arn(AGENT_RUNTIME_ARN)
    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_id,
        accountId=account_id,
        runtimeSessionId=session_id,
        contentType="application/json",
        accept="application/json",
        payload=json.dumps({"prompt": text, "session_id": session_id}).encode("utf-8"),
    )
    raw = b"".join(response["response"].iter_chunks())
    data = json.loads(raw)
    return (
        data.get("output")
        or data.get("response")
        or data.get("message")
        or str(data)
    )

# ── Reply cleanup ─────────────────────────────────────────────────────────────
def _clean_reply(text: str) -> str:
    """Strip model thinking/reasoning blocks before display and TTS."""
    text = re.sub(r"<thinking>.*?</thinking>", "", text,
                  flags=re.DOTALL | re.IGNORECASE)
    return text.strip()

# ── TTS — Amazon Polly ────────────────────────────────────────────────────────
def speak(text: str) -> None:
    client = boto3.client("polly", region_name=REGION)
    resp = client.synthesize_speech(
        Text=text[:3000],
        OutputFormat="pcm",
        VoiceId=POLLY_VOICE,
        SampleRate="16000",
    )
    tmp = tempfile.mktemp(suffix=".wav")
    with wave.open(tmp, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(resp["AudioStream"].read())
    play_wav(tmp)
    Path(tmp).unlink(missing_ok=True)

# ── Main loop ─────────────────────────────────────────────────────────────────
def _reset(board: WhisplayBoard) -> None:
    board.set_rgb(0, 255, 0)
    draw_screen(board, "Ready", "Hold button to speak")

def main():
    board = WhisplayBoard()
    board.set_backlight(100)
    session_id = str(uuid.uuid4())

    board.set_rgb(255, 0, 0)
    draw_screen(board, "Littlebat AI", "Waiting for WiFi...")

    while not online():
        time.sleep(2)

    _reset(board)

    while True:
        # Wait for button press
        while not board.button_pressed():
            time.sleep(0.05)

        if not online():
            board.set_rgb(255, 140, 0)
            draw_screen(board, "No WiFi", "Connect and try again", WARN)
            time.sleep(3)
            _reset(board)
            continue

        board.set_rgb(255, 0, 0)
        draw_screen(board, "Listening...", "Release to send")

        wav_path = record_while_held(board)

        board.set_rgb(0, 0, 255)
        draw_screen(board, "Transcribing...", "")

        try:
            text = transcribe(wav_path)
        except Exception as e:
            print(f"[transcribe error] {e}", flush=True)
            import traceback; traceback.print_exc()
            draw_screen(board, "Transcription failed", str(e)[:80], ERR)
            time.sleep(3)
            _reset(board)
            continue
        finally:
            Path(wav_path).unlink(missing_ok=True)

        if not text:
            draw_screen(board, "Didn't catch that", "Try again", WARN)
            time.sleep(2)
            _reset(board)
            continue

        draw_screen(board, "You said", text[:120])
        board.set_rgb(0, 80, 255)
        draw_screen(board, "Asking agent...", text[:60])

        reply = None
        for attempt in range(3):
            try:
                reply = invoke_agent(text, session_id)
                break
            except Exception as e:
                err = str(e)
                if "initialization time exceeded" in err and attempt < 2:
                    wait = 20
                    print(f"[agent cold start] warming up, retry in {wait}s (attempt {attempt+1}/2)…", flush=True)
                    board.set_rgb(255, 140, 0)
                    draw_screen(board, "Warming up...",
                                f"Agent cold-starting, retry in {wait}s", WARN)
                    time.sleep(wait)
                    board.set_rgb(0, 80, 255)
                    draw_screen(board, "Asking agent...", text[:60])
                else:
                    print(f"[agent error] {e}", flush=True)
                    import traceback; traceback.print_exc()
                    draw_screen(board, "Agent error", str(e)[:80], ERR)
                    time.sleep(3)
                    _reset(board)
                    break

        if reply is None:
            continue

        reply = _clean_reply(reply)
        draw_screen(board, "Agent", reply[:240])

        try:
            speak(reply)
        except Exception as e:
            print(f"[polly error] {e}", flush=True)
            import traceback; traceback.print_exc()

        _reset(board)

if __name__ == "__main__":
    main()
