# MeetScribe

Headless meeting recorder and transcription bot. Open-source alternative to Otter.ai and Sana.ai.

Joins **Webex**, **Zoom**, and **Google Meet** meetings as a guest, records audio, and generates timestamped transcripts with speaker labels — all running headless with zero cost.

## Features

- **Multi-platform**: Webex (primary), Zoom, Google Meet
- **Headless**: No GUI required — runs in Docker or CI
- **FOSS transcription**: faster-whisper (Whisper.cpp backend, CPU-optimized)
- **Speaker diarization**: Silence-gap heuristic (lightweight, no GPU needed)
- **Dashboard**: Web UI to deploy bots, search/view/download transcripts
- **GitHub Actions**: Trigger recordings via workflow dispatch
- **Zero cost**: No paid APIs, SDKs, or subscriptions

## Quick Start

### Docker (recommended)

```bash
cp .env.example .env
docker compose up --build
```

Open http://localhost:8000 to access the dashboard.

### Local (Linux)

```bash
bash scripts/setup.sh
source scripts/start_virtual_devices.sh
python -m api.main
```

### GitHub Actions

1. Push this repo to GitHub
2. Go to **Actions** → **Deploy MeetScribe Bot**
3. Click **Run workflow**
4. Enter: meeting URL, display name, platform
5. Download transcript from workflow artifacts

## Usage

### Dashboard

1. Open http://localhost:8000
2. Click **Join Meeting**
3. Select platform, paste meeting URL, set bot name
4. Click **Deploy Bot**
5. View transcript when complete

### API

```bash
# Deploy bot
curl -X POST http://localhost:8000/api/bot/join \
  -H "Content-Type: application/json" \
  -d '{"platform": "webex", "meeting_url": "https://example.webex.com/meet/user", "display_name": "MeetScribe"}'

# Check status
curl http://localhost:8000/api/bot/{bot_id}

# List transcripts
curl http://localhost:8000/api/transcripts

# Search transcripts
curl "http://localhost:8000/api/transcripts?q=budget"

# Download transcript
curl http://localhost:8000/api/transcripts/{id}/download?format=md
```

### CLI

```bash
python -m bot.engine --platform webex --meeting-url "https://example.webex.com/meet/user" --display-name "MeetScribe"
```

## Architecture

```
User/GitHub Actions
        │
        ▼
   ┌─────────┐     ┌──────────────┐     ┌──────────────┐
   │ FastAPI  │────▶│  Bot Engine   │────▶│  Transcriber  │
   │ + HTMX  │     │  (Playwright) │     │  (Whisper)    │
   └─────────┘     └──────────────┘     └──────────────┘
        │               │                      │
        ▼               ▼                      ▼
   ┌─────────┐     ┌──────────────┐     ┌──────────────┐
   │ SQLite  │     │ Xvfb + Pulse │     │  WAV files   │
   │   DB    │     │ + FFmpeg     │     │              │
   └─────────┘     └──────────────┘     └──────────────┘
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DISPLAY_NAME` | MeetScribe | Bot name in meetings |
| `WHISPER_MODEL` | small | Whisper model size (tiny/base/small/medium/large-v3) |
| `WHISPER_DEVICE` | cpu | Device for inference (cpu/cuda) |
| `WHISPER_COMPUTE_TYPE` | int8 | Quantization (int8/float16/float32) |
| `LOG_LEVEL` | INFO | Logging level |
| `PORT` | 8000 | Dashboard port |
| `DATABASE_PATH` | meetscribe.db | SQLite database path |

## Platform Notes

### Webex
- Joins as guest via public web client — no Webex account needed
- Supports meeting links (`*.webex.com/meet/*`) and numeric meeting IDs
- Host may need to admit from waiting room

### Zoom
- Joins via browser web client — no Zoom desktop app
- Supports invite links and meeting IDs with passcodes
- Passcode extracted automatically from URL

### Google Meet
- Joins via Chromium browser
- Supports meeting codes (`xxx-xxxx-xxx`) and full URLs
- Host must admit from waiting room

## Limitations

- **Audio quality**: Depends on PulseAudio virtual sink capture (mono 16kHz)
- **Speaker diarization**: Uses silence-gap heuristic, not true speaker recognition. For better results, enable pyannote.audio with a GPU
- **Anti-bot detection**: Platforms may update their detection. Selectors may need updating
- **GitHub Actions**: 2 vCPU / 7GB RAM limits transcription to post-recording; 6h max job duration
- **Meeting admission**: Bot may be held in waiting room until host admits

## License

MIT
