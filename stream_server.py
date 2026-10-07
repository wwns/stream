#!/usr/bin/env python3
"""
HDMI-USB Streaming Server for Raspberry Pi 3B
Oparty o FFmpeg + Flask + HLS/MJPEG
"""

import subprocess
import os
import signal
import json
import glob
import threading
import time
from flask import Flask, Response, render_template_string, jsonify, request, send_from_directory

app = Flask(__name__)

# === KONFIGURACJA ===
CONFIG = {
    "hls_dir": "/tmp/hls",
    "device": "/dev/video0",      # USB2 Video (Innomaker HDMItoU30 na RPi 3B USB2)
    "input_format": "mjpeg",      # MJPEG wymagany! YUV422 1080p = ~190MB/s -> przepełni USB2
    "width": 1280,
    "height": 720,
    "fps": 30,
    "video_bitrate": "2000k",
    "audio_bitrate": "128k",
    "audio_device": "hw:2,0",     # USB2 Audio = card 2 (MACROSILICON 345f:2130)
    "hls_time": 2,
    "hls_list_size": 5,
    "port": 8080,
}

ffmpeg_process = None
stream_status = {"running": False, "error": None, "start_time": None}
lock = threading.Lock()

def get_video_devices():
    """Wykryj dostępne urządzenia video."""
    devices = []
    for dev in sorted(glob.glob("/dev/video*")):
        try:
            result = subprocess.run(
                ["v4l2-ctl", "--device", dev, "--info"],
                capture_output=True, text=True, timeout=2
            )
            name = "Nieznane"
            for line in result.stdout.splitlines():
                if "Card type" in line:
                    name = line.split(":", 1)[-1].strip()
                    break
            devices.append({"path": dev, "name": name})
        except Exception:
            devices.append({"path": dev, "name": "Urządzenie video"})
    return devices

def ensure_hls_dir():
    os.makedirs(CONFIG["hls_dir"], exist_ok=True)

def cleanup_hls():
    for f in glob.glob(f"{CONFIG['hls_dir']}/*"):
        try:
            os.remove(f)
        except:
            pass

def build_ffmpeg_cmd():
    device = CONFIG["device"]
    fmt = CONFIG.get("input_format", "yuyv422")
    w, h = CONFIG["width"], CONFIG["height"]
    fps = CONFIG["fps"]
    vb = CONFIG["video_bitrate"]
    ab = CONFIG["audio_bitrate"]
    audio_dev = CONFIG.get("audio_device", "hw:2,0")
    hls_dir = CONFIG["hls_dir"]
    hls_time = CONFIG["hls_time"]
    hls_list = CONFIG["hls_list_size"]

    # Innomaker HDMItoU30 obsługuje yuyv422 i mjpeg
    # yuyv422 = brak dodatkowej kompresji (wolniejszy na RPi3, ale stabilniejszy)
    # mjpeg = skompresowany JPEG (mniej obciążenia USB, zalecany dla 1080p@30)
    cmd = [
        "ffmpeg", "-y",
        # --- wejście video ---
        "-f", "v4l2",
        "-input_format", fmt,
        "-video_size", f"{w}x{h}",
        "-framerate", str(fps),
        "-i", device,
        # --- wejście audio (USB3 Digital Audio UAC1.0 L-PCM 48kHz) ---
        "-f", "alsa",
        "-ar", "48000",
        "-ac", "2",
        "-i", audio_dev,
        # --- enkoder video (libx264 ultrafast dla RPi3B) ---
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-b:v", vb,
        "-maxrate", vb,
        "-bufsize", str(int(vb[:-1]) * 2) + "k",
        "-g", str(fps * 2),
        "-sc_threshold", "0",
        "-vf", "format=yuv420p",   # konwersja kolorów do YUV420p (wymagane przez x264)
        # --- enkoder audio ---
        "-c:a", "aac",
        "-b:a", ab,
        "-ar", "48000",
        # --- wyjście HLS ---
        "-f", "hls",
        "-hls_time", str(hls_time),
        "-hls_list_size", str(hls_list),
        "-hls_flags", "delete_segments+append_list",
        "-hls_segment_filename", f"{hls_dir}/segment%05d.ts",
        f"{hls_dir}/stream.m3u8"
    ]
    return cmd

def start_stream():
    global ffmpeg_process, stream_status
    with lock:
        if stream_status["running"]:
            return {"ok": False, "msg": "Stream już działa"}
        ensure_hls_dir()
        cleanup_hls()
        cmd = build_ffmpeg_cmd()
        try:
            ffmpeg_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stream_status["running"] = True
            stream_status["error"] = None
            stream_status["start_time"] = time.time()
            # wątek monitorujący
            threading.Thread(target=monitor_ffmpeg, daemon=True).start()
            return {"ok": True, "msg": "Stream uruchomiony"}
        except Exception as e:
            stream_status["running"] = False
            stream_status["error"] = str(e)
            return {"ok": False, "msg": str(e)}

def stop_stream():
    global ffmpeg_process, stream_status
    with lock:
        if ffmpeg_process and ffmpeg_process.poll() is None:
            ffmpeg_process.terminate()
            try:
                ffmpeg_process.wait(timeout=5)
            except:
                ffmpeg_process.kill()
        ffmpeg_process = None
        stream_status["running"] = False
        stream_status["start_time"] = None
        cleanup_hls()
    return {"ok": True, "msg": "Stream zatrzymany"}

def monitor_ffmpeg():
    global stream_status
    while True:
        time.sleep(1)
        with lock:
            if ffmpeg_process is None:
                break
            if ffmpeg_process.poll() is not None:
                # proces zakończył się
                stderr = ffmpeg_process.stderr.read().decode(errors="replace")
                stream_status["running"] = False
                stream_status["error"] = stderr[-500:] if stderr else "FFmpeg zakończył się nieoczekiwanie"
                break

# ===== ROUTES =====

@app.route("/")
def index():
    return render_template_string(HTML_PAGE)

@app.route("/hls/<path:filename>")
def hls_files(filename):
    return send_from_directory(CONFIG["hls_dir"], filename)

@app.route("/api/start", methods=["POST"])
def api_start():
    data = request.json or {}
    # aktualizuj konfigurację jeśli podano
    for key in ["device", "width", "height", "fps", "video_bitrate", "audio_bitrate",
                "input_format", "audio_device"]:
        if key in data:
            if key in ["width", "height", "fps"]:
                CONFIG[key] = int(data[key])
            else:
                CONFIG[key] = data[key]
    result = start_stream()
    return jsonify(result)

@app.route("/api/stop", methods=["POST"])
def api_stop():
    result = stop_stream()
    return jsonify(result)

@app.route("/api/status")
def api_status():
    with lock:
        status = dict(stream_status)
        if status["start_time"]:
            status["uptime"] = int(time.time() - status["start_time"])
        else:
            status["uptime"] = 0
        status["hls_ready"] = os.path.exists(f"{CONFIG['hls_dir']}/stream.m3u8")
        status["config"] = dict(CONFIG)
    return jsonify(status)

@app.route("/api/devices")
def api_devices():
    return jsonify(get_video_devices())

@app.route("/api/config", methods=["POST"])
def api_config():
    data = request.json or {}
    for key in ["device", "width", "height", "fps", "video_bitrate", "audio_bitrate",
                "hls_time", "hls_list_size"]:
        if key in data:
            if key in ["width", "height", "fps", "hls_time", "hls_list_size"]:
                CONFIG[key] = int(data[key])
            else:
                CONFIG[key] = data[key]
    return jsonify({"ok": True, "config": CONFIG})

# ===== HTML GUI =====

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RPi Stream Control</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Orbitron:wght@400;700;900&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
<style>
  :root {
    --bg: #080c10;
    --panel: #0d1117;
    --border: #1a2535;
    --accent: #00e5ff;
    --accent2: #ff3d71;
    --green: #39ff14;
    --text: #c0cdd8;
    --muted: #3a4a5a;
    --font-mono: 'Share Tech Mono', monospace;
    --font-head: 'Orbitron', sans-serif;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--font-mono);
    min-height: 100vh;
    overflow-x: hidden;
  }
  /* scanlines overlay */
  body::before {
    content: '';
    position: fixed;
    inset: 0;
    background: repeating-linear-gradient(
      0deg,
      transparent,
      transparent 2px,
      rgba(0,229,255,0.015) 2px,
      rgba(0,229,255,0.015) 4px
    );
    pointer-events: none;
    z-index: 9999;
  }

  header {
    border-bottom: 1px solid var(--border);
    padding: 16px 32px;
    display: flex;
    align-items: center;
    gap: 16px;
    background: rgba(0,229,255,0.03);
  }
  .logo {
    font-family: var(--font-head);
    font-size: 1.4rem;
    font-weight: 900;
    color: var(--accent);
    letter-spacing: 3px;
    text-shadow: 0 0 20px rgba(0,229,255,0.5);
  }
  .logo span { color: var(--accent2); }
  .status-dot {
    width: 10px; height: 10px;
    border-radius: 50%;
    background: var(--muted);
    transition: all 0.3s;
    margin-left: auto;
  }
  .status-dot.live {
    background: var(--green);
    box-shadow: 0 0 12px var(--green);
    animation: pulse 1.5s infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }
  .status-label {
    font-size: 0.75rem;
    letter-spacing: 2px;
    color: var(--muted);
    text-transform: uppercase;
  }
  .status-label.live { color: var(--green); }

  main {
    display: grid;
    grid-template-columns: 380px 1fr;
    gap: 0;
    height: calc(100vh - 65px);
  }

  .sidebar {
    border-right: 1px solid var(--border);
    overflow-y: auto;
    padding: 24px;
    display: flex;
    flex-direction: column;
    gap: 20px;
  }

  .panel {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 4px;
    overflow: hidden;
  }
  .panel-header {
    padding: 10px 16px;
    background: rgba(0,229,255,0.05);
    border-bottom: 1px solid var(--border);
    font-family: var(--font-head);
    font-size: 0.65rem;
    letter-spacing: 3px;
    color: var(--accent);
    text-transform: uppercase;
  }
  .panel-body { padding: 16px; }

  .field { margin-bottom: 14px; }
  .field label {
    display: block;
    font-size: 0.7rem;
    letter-spacing: 1.5px;
    color: var(--muted);
    text-transform: uppercase;
    margin-bottom: 6px;
  }
  .field select, .field input {
    width: 100%;
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    font-family: var(--font-mono);
    font-size: 0.85rem;
    padding: 8px 12px;
    border-radius: 3px;
    outline: none;
    transition: border-color 0.2s;
  }
  .field select:focus, .field input:focus {
    border-color: var(--accent);
    box-shadow: 0 0 0 1px rgba(0,229,255,0.2);
  }
  .field-row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }

  .btn {
    width: 100%;
    padding: 12px;
    border: none;
    border-radius: 3px;
    font-family: var(--font-head);
    font-size: 0.75rem;
    letter-spacing: 3px;
    text-transform: uppercase;
    cursor: pointer;
    transition: all 0.2s;
  }
  .btn-start {
    background: var(--green);
    color: #000;
    box-shadow: 0 0 20px rgba(57,255,20,0.3);
  }
  .btn-start:hover { box-shadow: 0 0 30px rgba(57,255,20,0.6); transform: translateY(-1px); }
  .btn-stop {
    background: var(--accent2);
    color: #fff;
    box-shadow: 0 0 20px rgba(255,61,113,0.3);
  }
  .btn-stop:hover { box-shadow: 0 0 30px rgba(255,61,113,0.6); transform: translateY(-1px); }
  .btn:disabled { opacity: 0.3; cursor: not-allowed; transform: none !important; }

  .stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .stat {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 10px 12px;
  }
  .stat-label {
    font-size: 0.6rem;
    letter-spacing: 2px;
    color: var(--muted);
    text-transform: uppercase;
    margin-bottom: 4px;
  }
  .stat-value {
    font-size: 1rem;
    color: var(--accent);
    font-weight: bold;
  }
  .stat-value.ok { color: var(--green); }
  .stat-value.err { color: var(--accent2); }

  .log-box {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 10px;
    height: 80px;
    overflow-y: auto;
    font-size: 0.7rem;
    color: var(--muted);
    line-height: 1.5;
  }
  .log-box .log-ok { color: var(--green); }
  .log-box .log-err { color: var(--accent2); }
  .log-box .log-info { color: var(--accent); }

  /* Video area */
  .video-area {
    display: flex;
    flex-direction: column;
    background: #000;
    position: relative;
  }
  .video-container {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
    overflow: hidden;
  }
  video {
    max-width: 100%;
    max-height: 100%;
    width: 100%;
    object-fit: contain;
  }
  .no-stream {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 16px;
    color: var(--muted);
  }
  .no-stream .icon {
    font-size: 4rem;
    opacity: 0.3;
  }
  .no-stream p { font-size: 0.8rem; letter-spacing: 2px; text-transform: uppercase; }

  .video-overlay {
    position: absolute;
    top: 16px; left: 16px;
    display: flex;
    gap: 8px;
    z-index: 10;
  }
  .badge {
    padding: 4px 10px;
    border-radius: 2px;
    font-family: var(--font-head);
    font-size: 0.6rem;
    letter-spacing: 2px;
    text-transform: uppercase;
    font-weight: 700;
  }
  .badge-live {
    background: var(--accent2);
    color: #fff;
    animation: pulse 1.5s infinite;
  }
  .badge-hls {
    background: rgba(0,229,255,0.15);
    color: var(--accent);
    border: 1px solid rgba(0,229,255,0.3);
  }

  .video-bar {
    border-top: 1px solid var(--border);
    padding: 12px 20px;
    display: flex;
    align-items: center;
    gap: 16px;
    background: var(--panel);
  }
  .stream-url {
    flex: 1;
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--accent);
    font-family: var(--font-mono);
    font-size: 0.75rem;
    padding: 7px 12px;
    border-radius: 3px;
    user-select: all;
  }
  .btn-copy {
    padding: 7px 16px;
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text);
    font-family: var(--font-mono);
    font-size: 0.75rem;
    border-radius: 3px;
    cursor: pointer;
    white-space: nowrap;
    transition: all 0.2s;
  }
  .btn-copy:hover { border-color: var(--accent); color: var(--accent); }

  ::-webkit-scrollbar { width: 4px; }
  ::-webkit-scrollbar-track { background: var(--bg); }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

  @media (max-width: 900px) {
    main { grid-template-columns: 1fr; grid-template-rows: auto 1fr; height: auto; }
    .video-area { min-height: 280px; }
    .sidebar { border-right: none; border-bottom: 1px solid var(--border); }
  }
</style>
</head>
<body>
<header>
  <div class="logo">RPI<span>STREAM</span></div>
  <div style="font-size:0.65rem; color:var(--muted); letter-spacing:2px;">HDMI → USB → HLS</div>
  <div style="margin-left:auto; display:flex; align-items:center; gap:8px;">
    <span class="status-label" id="statusLabel">OFFLINE</span>
    <div class="status-dot" id="statusDot"></div>
  </div>
</header>

<main>
  <aside class="sidebar">

    <!-- Urządzenie -->
    <div class="panel">
      <div class="panel-header">Źródło sygnału</div>
      <div class="panel-body">
        <div class="field">
          <label>Urządzenie video</label>
          <select id="device">
            <option value="/dev/video0">/dev/video0</option>
          </select>
        </div>
        <button class="btn" style="background:transparent;border:1px solid var(--border);color:var(--muted);font-size:0.65rem;letter-spacing:2px;margin-bottom:0;" onclick="loadDevices()">↻ Odśwież urządzenia</button>
      </div>
    </div>

    <!-- Parametry -->
    <div class="panel">
      <div class="panel-header">Parametry strumienia</div>
      <div class="panel-body">
        <div class="field-row">
          <div class="field">
            <label>Szerokość</label>
            <input type="number" id="width" value="1280" min="320" max="1920">
          </div>
          <div class="field">
            <label>Wysokość</label>
            <input type="number" id="height" value="720" min="240" max="1080">
          </div>
        </div>
        <div class="field-row">
          <div class="field">
            <label>Format wejścia</label>
            <select id="input_format">
              <option value="mjpeg" selected>MJPEG ✓ (wymagany na USB2)</option>
              <option value="yuyv422" disabled>YUV422 ✗ (przepełni USB2!)</option>
            </select>
          </div>
          <div class="field">
            <label>FPS</label>
            <select id="fps">
              <option value="15">15 fps</option>
              <option value="25">25 fps</option>
              <option value="30" selected>30 fps</option>
              <option value="60">60 fps</option>
            </select>
          </div>
        </div>
        <div class="field-row">
          <div class="field">
            <label>Rozdzielczość</label>
            <select id="resolution" onchange="updateRes(this.value)">
              <option value="1920x1080">1080p — uwaga: ciężkie dla RPi3B</option>
              <option value="1280x720" selected>720p ✓ (zalecane dla RPi3B)</option>
              <option value="1024x768">1024×768</option>
              <option value="800x600">800×600 (lekkie)</option>
            </select>
          </div>
          <div class="field">
            <label>Bitrate video</label>
            <select id="video_bitrate">
              <option value="1000k">1 Mb/s</option>
              <option value="2000k">2 Mb/s</option>
              <option value="2500k" selected>2.5 Mb/s</option>
              <option value="4000k">4 Mb/s</option>
            </select>
          </div>
        </div>
        <div class="field-row">
          <div class="field">
            <label>Bitrate audio</label>
            <select id="audio_bitrate">
              <option value="64k">64 kb/s</option>
              <option value="128k" selected>128 kb/s</option>
              <option value="192k">192 kb/s</option>
            </select>
          </div>
          <div class="field">
            <label>Audio (ALSA hw:X,0)</label>
            <input type="text" id="audio_device" value="hw:2,0" placeholder="np. hw:1,0">
          </div>
        </div>
      </div>
    </div>

    <!-- Kontrolki -->
    <div style="display:flex; flex-direction:column; gap:10px;">
      <button class="btn btn-start" id="btnStart" onclick="startStream()">▶ START STREAM</button>
      <button class="btn btn-stop" id="btnStop" onclick="stopStream()" disabled>■ STOP STREAM</button>
    </div>

    <!-- Statystyki -->
    <div class="panel">
      <div class="panel-header">Status</div>
      <div class="panel-body">
        <div class="stat-grid">
          <div class="stat">
            <div class="stat-label">Stan</div>
            <div class="stat-value" id="statState">OFFLINE</div>
          </div>
          <div class="stat">
            <div class="stat-label">Uptime</div>
            <div class="stat-value" id="statUptime">--</div>
          </div>
          <div class="stat">
            <div class="stat-label">Rozdzielczość</div>
            <div class="stat-value" id="statRes">--</div>
          </div>
          <div class="stat">
            <div class="stat-label">HLS</div>
            <div class="stat-value" id="statHls">--</div>
          </div>
        </div>
        <div style="margin-top:12px;">
          <div class="stat-label" style="margin-bottom:6px;">Log</div>
          <div class="log-box" id="logBox"></div>
        </div>
      </div>
    </div>

  </aside>

  <!-- Video -->
  <div class="video-area">
    <div class="video-container">
      <div class="video-overlay" id="videoOverlay" style="display:none;">
        <span class="badge badge-live">● LIVE</span>
        <span class="badge badge-hls">HLS</span>
      </div>
      <div class="no-stream" id="noStream">
        <div class="icon">▷</div>
        <p>Brak aktywnego strumienia</p>
        <p style="font-size:0.65rem; opacity:0.5;">Uruchom stream z panelu bocznego</p>
      </div>
      <video id="videoPlayer" style="display:none;" controls autoplay muted></video>
    </div>
    <div class="video-bar">
      <input class="stream-url" id="streamUrl" readonly value="http://[adres-rpi]:8080/hls/stream.m3u8">
      <button class="btn-copy" onclick="copyUrl()">Kopiuj URL</button>
      <button class="btn-copy" onclick="openVlc()">Otwórz w VLC</button>
    </div>
  </div>
</main>

<script>
let hls = null;
let pollInterval = null;

function log(msg, type='info') {
  const box = document.getElementById('logBox');
  const line = document.createElement('div');
  line.className = `log-${type}`;
  const t = new Date().toLocaleTimeString('pl');
  line.textContent = `[${t}] ${msg}`;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
  // trim
  while (box.children.length > 50) box.removeChild(box.firstChild);
}

function formatUptime(s) {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return [h, m, sec].map(v => String(v).padStart(2,'0')).join(':');
}

async function loadDevices() {
  try {
    const r = await fetch('/api/devices');
    const devices = await r.json();
    const sel = document.getElementById('device');
    sel.innerHTML = '';
    if (devices.length === 0) {
      sel.innerHTML = '<option value="/dev/video0">/dev/video0 (brak wykrytych)</option>';
    } else {
      devices.forEach(d => {
        const opt = document.createElement('option');
        opt.value = d.path;
        opt.textContent = `${d.path} — ${d.name}`;
        sel.appendChild(opt);
      });
    }
    log(`Znaleziono ${devices.length} urządzenie(a)`, 'ok');
  } catch(e) {
    log('Błąd ładowania urządzeń: ' + e, 'err');
  }
}

async function startStream() {
  const res = document.getElementById('resolution').value.split('x');
  const payload = {
    device: document.getElementById('device').value,
    input_format: document.getElementById('input_format').value,
    width: parseInt(res[0]),
    height: parseInt(res[1]),
    fps: parseInt(document.getElementById('fps').value),
    video_bitrate: document.getElementById('video_bitrate').value,
    audio_bitrate: document.getElementById('audio_bitrate').value,
    audio_device: document.getElementById('audio_device').value,
  };
  log('Uruchamianie strumienia...', 'info');
  try {
    const r = await fetch('/api/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await r.json();
    if (data.ok) {
      log('Stream uruchomiony ✓', 'ok');
      document.getElementById('btnStart').disabled = true;
      document.getElementById('btnStop').disabled = false;
      // poczekaj chwilę i załaduj player
      setTimeout(loadPlayer, 4000);
    } else {
      log('Błąd: ' + data.msg, 'err');
    }
  } catch(e) {
    log('Błąd połączenia: ' + e, 'err');
  }
}

async function stopStream() {
  log('Zatrzymywanie strumienia...', 'info');
  try {
    const r = await fetch('/api/stop', { method: 'POST' });
    const data = await r.json();
    log('Stream zatrzymany', 'ok');
    document.getElementById('btnStart').disabled = false;
    document.getElementById('btnStop').disabled = true;
    unloadPlayer();
  } catch(e) {
    log('Błąd: ' + e, 'err');
  }
}

function loadPlayer() {
  const video = document.getElementById('videoPlayer');
  const hlsUrl = '/hls/stream.m3u8';
  if (hls) { hls.destroy(); hls = null; }
  if (Hls.isSupported()) {
    hls = new Hls({ lowLatencyMode: true, liveSyncDurationCount: 2 });
    hls.loadSource(hlsUrl);
    hls.attachMedia(video);
    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      video.play().catch(() => {});
      log('Odtwarzacz HLS gotowy ✓', 'ok');
    });
    hls.on(Hls.Events.ERROR, (e, data) => {
      if (data.fatal) log('Błąd HLS: ' + data.type, 'err');
    });
  } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
    video.src = hlsUrl;
    video.play().catch(() => {});
  }
  video.style.display = 'block';
  document.getElementById('noStream').style.display = 'none';
  document.getElementById('videoOverlay').style.display = 'flex';
}

function unloadPlayer() {
  const video = document.getElementById('videoPlayer');
  if (hls) { hls.destroy(); hls = null; }
  video.src = '';
  video.style.display = 'none';
  document.getElementById('noStream').style.display = 'flex';
  document.getElementById('videoOverlay').style.display = 'none';
}

async function pollStatus() {
  try {
    const r = await fetch('/api/status');
    const s = await r.json();
    const dot = document.getElementById('statusDot');
    const label = document.getElementById('statusLabel');
    const statState = document.getElementById('statState');
    const statUptime = document.getElementById('statUptime');
    const statRes = document.getElementById('statRes');
    const statHls = document.getElementById('statHls');

    if (s.running) {
      dot.className = 'status-dot live';
      label.className = 'status-label live';
      label.textContent = 'LIVE';
      statState.textContent = 'ONLINE';
      statState.className = 'stat-value ok';
      statUptime.textContent = formatUptime(s.uptime);
      statRes.textContent = `${s.config.width}×${s.config.height}`;
      statHls.textContent = s.hls_ready ? 'GOTOWY' : 'CZEKA...';
      statHls.className = s.hls_ready ? 'stat-value ok' : 'stat-value';
      document.getElementById('btnStart').disabled = true;
      document.getElementById('btnStop').disabled = false;
    } else {
      dot.className = 'status-dot';
      label.className = 'status-label';
      label.textContent = 'OFFLINE';
      statState.textContent = 'OFFLINE';
      statState.className = 'stat-value';
      statUptime.textContent = '--';
      statHls.textContent = '--';
      statHls.className = 'stat-value';
      document.getElementById('btnStart').disabled = false;
      document.getElementById('btnStop').disabled = true;
      if (s.error && s.error !== '') {
        // pokaż błąd tylko raz
        const logBox = document.getElementById('logBox');
        const last = logBox.lastElementChild;
        if (!last || !last.textContent.includes(s.error.slice(0,20))) {
          log('FFmpeg: ' + s.error.slice(0,120), 'err');
        }
        unloadPlayer();
      }
    }
    // aktualizuj URL
    const host = window.location.hostname;
    document.getElementById('streamUrl').value = `http://${host}:8080/hls/stream.m3u8`;
  } catch(e) {}
}

function copyUrl() {
  const url = document.getElementById('streamUrl').value;
  navigator.clipboard.writeText(url).then(() => log('URL skopiowany ✓', 'ok'));
}

function openVlc() {
  const url = document.getElementById('streamUrl').value;
  window.open(`vlc://${url}`);
}

// Init
loadDevices();
pollStatus();
setInterval(pollStatus, 2000);
log('Chip: MACROSILICON 345f:2130 @ /dev/video0', 'ok');
log('Audio: USB2 Video card 2 → hw:2,0 ✓', 'ok');
log('Zalecane: MJPEG 720p@30fps', 'info');

function updateRes(val) { /* rozdzielczość obsługuje select */ }
</script>
</body>
</html>"""

if __name__ == "__main__":
    print(f"[RpiStream] Serwer startuje na porcie {CONFIG['port']}")
    print(f"[RpiStream] GUI: http://localhost:{CONFIG['port']}")
    app.run(host="0.0.0.0", port=CONFIG["port"], debug=False, threaded=True)
