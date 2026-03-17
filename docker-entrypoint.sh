#!/usr/bin/env bash
set -euo pipefail

echo "=== MeetScribe Container Starting ==="

# Start Xvfb
export DISPLAY=:99
Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp &
sleep 1

# Start PulseAudio
pulseaudio --start --exit-idle-time=-1 --daemonize=yes 2>/dev/null || true
sleep 1

# Configure virtual audio sink
pactl load-module module-null-sink sink_name=MeetScribe sink_properties=device.description=MeetScribe 2>/dev/null || true
pactl set-default-sink MeetScribe 2>/dev/null || true
pactl set-default-source MeetScribe.monitor 2>/dev/null || true

echo "Virtual devices ready (DISPLAY=$DISPLAY)"

# Launch the application
exec python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
