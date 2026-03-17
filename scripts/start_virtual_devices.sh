#!/usr/bin/env bash
set -euo pipefail

echo "Starting virtual display and audio devices..."

# Start Xvfb (virtual framebuffer)
export DISPLAY=:99
Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp &
XVFB_PID=$!
echo "Xvfb started (PID: $XVFB_PID) on $DISPLAY"

# Wait for Xvfb to be ready
sleep 1
if ! kill -0 "$XVFB_PID" 2>/dev/null; then
    echo "ERROR: Xvfb failed to start"
    exit 1
fi

# Start PulseAudio with virtual null-sink
pulseaudio --start --exit-idle-time=-1 2>/dev/null || true
sleep 1

# Create a virtual audio sink for capturing meeting audio
pactl load-module module-null-sink sink_name=MeetScribe sink_properties=device.description=MeetScribe
pactl set-default-sink MeetScribe
pactl set-default-source MeetScribe.monitor

echo "PulseAudio configured with virtual sink 'MeetScribe'"

# Verify
echo "--- Verification ---"
echo "Display: $DISPLAY"
pactl info | grep -E "Default (Sink|Source):" || echo "PulseAudio info unavailable"
echo "Virtual devices ready."
