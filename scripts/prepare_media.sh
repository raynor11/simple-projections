#!/bin/bash
# Convert videos to what the Raspberry Pi 4 decodes in hardware: H.264
# (High profile, 4:2:0, up to 1080p30) in MP4, with no audio track (frames
# play silently). Run it once on the Mac; HEVC/AV1/VP9 or 4K files would
# force slow software decoding on the Pi.
#
#   scripts/prepare_media.sh input.mov                 -> media/input.mp4
#   scripts/prepare_media.sh input.mov out.mp4
#   WIDTH=1280 scripts/prepare_media.sh input.mov      (smaller, for a smaller frame)
#
# Sizing videos close to their frame's on-screen size (e.g. 1280 wide for a
# frame covering two thirds of the screen) cuts decoding and upload work.

set -e

if [ $# -lt 1 ]; then
    sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
fi

IN="$1"
OUT="${2:-media/$(basename "${IN%.*}").mp4}"
WIDTH="${WIDTH:-1920}"
FPS="${FPS:-30}"

if [ "$(cd "$(dirname "$IN")" && pwd)/$(basename "$IN")" = "$(cd "$(dirname "$OUT")" 2>/dev/null && pwd)/$(basename "$OUT")" ]; then
    echo "Output would overwrite the input; give a different output path" >&2
    exit 1
fi

command -v ffmpeg > /dev/null || { echo "Needs ffmpeg (brew install ffmpeg)" >&2; exit 1; }
mkdir -p "$(dirname "$OUT")"

ffmpeg -hide_banner -y -i "$IN" \
    -an \
    -vf "scale='min(${WIDTH},iw)':-2:flags=lanczos,fps=${FPS},format=yuv420p" \
    -c:v libx264 -profile:v high -level:v 4.1 -preset slow -crf 20 \
    -movflags +faststart \
    "$OUT"

echo "Wrote $OUT"
