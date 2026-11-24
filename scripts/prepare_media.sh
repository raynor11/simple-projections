#!/bin/bash
# Convert videos to what the Raspberry Pi 4 decodes in hardware: H.264
# (High profile, 4:2:0, up to 1080p30) in MP4, with no audio track (frames
# play silently). Run it once on the Mac; HEVC/AV1/VP9 or 4K files would
# force slow software decoding on the Pi.
#
#   scripts/prepare_media.sh input.mov                 -> media/input.mp4
#   scripts/prepare_media.sh input.mov out.mp4
#   WIDTH=1280 scripts/prepare_media.sh input.mov      (smaller, for a smaller frame)
#   ROTATE=cw FPS=24 scripts/prepare_media.sh in.mp4 "out (landscape).mp4"
#                                                      (portrait turned sideways: use "rotate": 270)
#   REVERSE=1 scripts/prepare_media.sh in.mp4 "out (reversed).mp4"
#                                                      (backwards, for a sequence clip's "reverse")
#   SMOOTH=8 scripts/prepare_media.sh in.mp4           (blend each frame with the 7 before it)
#
# Sizing videos close to their frame's on-screen size (e.g. 1280 wide for a
# frame covering two thirds of the screen) cuts decoding and upload work.

set -e

if [ $# -lt 1 ]; then
    sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
fi

IN="$1"
OUT="${2:-media/$(basename "${IN%.*}").mp4}"
WIDTH="${WIDTH:-1920}"
FPS="${FPS:-30}"
# The Pi's hardware scaler can't take tall portrait frames, so ROTATE=cw
# encodes a portrait video turned 90 degrees clockwise (landscape); the
# frame's "rotate": 270 draws it upright again.
case "${ROTATE:-}" in
    "") TURN="" ;;
    cw) TURN="transpose=1," ;;
    *) echo "ROTATE must be cw" >&2; exit 1 ;;
esac
# REVERSE=1 plays it backwards without its first and last frames, so a
# sequence bouncing forward-backward-forward doesn't show either one twice.
BACKWARDS=""
if [ -n "${REVERSE:-}" ]; then
    BACKWARDS=",trim=start_frame=1,setpts=PTS-STARTPTS,reverse,trim=start_frame=1,setpts=PTS-STARTPTS"
fi

# SMOOTH=n blends each frame with the n-1 before it, weighted 1, 2, ...,
# 2, 1. Some AI-generated clips move their fine texture in steps (every 4th
# frame, with a bigger jump every 32), which shimmers on a big screen;
# SMOOTH=8 evens that out while keeping the detail, but ghosts fast
# movement, so keep it for mostly still clips.
SMOOTHING=""
if [ -n "${SMOOTH:-}" ]; then
    WEIGHTS=""
    for ((i = 1; i <= SMOOTH; i++)); do
        WEIGHTS="$WEIGHTS $(( i < SMOOTH + 1 - i ? i : SMOOTH + 1 - i ))"
    done
    SMOOTHING=",tmix=frames=${SMOOTH}:weights='${WEIGHTS# }'"
fi

if [ "$(cd "$(dirname "$IN")" && pwd)/$(basename "$IN")" = "$(cd "$(dirname "$OUT")" 2>/dev/null && pwd)/$(basename "$OUT")" ]; then
    echo "Output would overwrite the input; give a different output path" >&2
    exit 1
fi

command -v ffmpeg > /dev/null || { echo "Needs ffmpeg (brew install ffmpeg)" >&2; exit 1; }
mkdir -p "$(dirname "$OUT")"

ffmpeg -hide_banner -y -i "$IN" \
    -an \
    -vf "${TURN}scale='min(${WIDTH},iw)':-2:flags=lanczos,fps=${FPS}${SMOOTHING},format=yuv420p${BACKWARDS}" \
    -c:v libx264 -profile:v high -level:v 4.1 -preset slow -crf 20 \
    -movflags +faststart \
    "$OUT"

echo "Wrote $OUT"
