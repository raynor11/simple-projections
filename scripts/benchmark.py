#!/usr/bin/env python3
"""
Run the real playback pipeline with a fixed scenario and report performance.

  python scripts/benchmark.py typical --seconds 60
  python scripts/benchmark.py all --seconds 30 --json results.json
  python scripts/benchmark.py video --fullscreen          (on the Pi)

Scenarios (each adds to the previous one):
  static   text and weather only
  video    + a looping 1080p video in a large frame
  typical  + a "cast": a second looping video in the cast-sized frame
  stress   + a "camera" video frame and a second text frame

The cast and camera are stood in for by video files, so results are
repeatable and need no hardware. Set --media to use your own clips.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pygame

from src.playback import Playback
from src.renderer import CanvasRenderer
from src.stats import Stats, format_summary


SCENARIOS = ('static', 'video', 'typical', 'stress')


def scenario_config(name, media, width, height):
    frames = [
        {'id': 'weather', 'rect': [0.02, 0.04, 0.3, 0.45],
         'source': {'type': 'weather', 'lat': 45.6, 'lon': -123.2}},
        {'id': 'msg', 'rect': [0.02, 0.55, 0.3, 0.4], 'source': {'type': 'text', 'text': 'Benchmark'}},
    ]
    level = SCENARIOS.index(name)
    if level >= 1:
        frames.append({'id': 'video', 'rect': [0.36, 0.04, 0.6, 0.5],
                       'source': {'type': 'file', 'path': str(media[0])}})
    if level >= 2:
        frames.append({'id': 'cast', 'rect': [0.34, 0.38, 0.64, 0.58],
                       'source': {'type': 'file', 'path': str(media[1 % len(media)])}})
    if level >= 3:
        frames.append({'id': 'camera', 'rect': [0.02, 0.04, 0.25, 0.25],
                       'source': {'type': 'file', 'path': str(media[0])}})
        frames.append({'id': 'msg2', 'rect': [0.7, 0.7, 0.28, 0.25],
                       'source': {'type': 'text', 'text': 'Second text frame with more words in it'}})
    return {'canvas': {'width': width, 'height': height}, 'frames': frames}


def run(name, seconds, media, width, height, fullscreen):
    renderer = CanvasRenderer(width, height, fullscreen=fullscreen)
    renderer.init_gl()
    playback = Playback(scenario_config(name, media, width, height), renderer)
    playback.start()
    clock = pygame.time.Clock()
    warmup = 3.0
    stats = Stats(interval=seconds)
    start = time.monotonic()
    try:
        while True:
            pygame.event.pump()
            now = time.monotonic()
            if now - start < warmup:
                playback.tick()
                stats = Stats(interval=seconds)       # discard warm-up (decoder start, first fetches)
                clock.tick(30)
                continue
            t = time.perf_counter()
            rendered = playback.tick()
            stats.record_tick((time.perf_counter() - t) * 1000, rendered)
            if rendered:
                stats.record_render(*renderer.last_render_stats)
            summary = stats.maybe_report()
            if summary:
                return summary
            clock.tick(30)
    finally:
        playback.close()
        renderer.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('scenario', choices=SCENARIOS + ('all',))
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--media', nargs='+', default=['media/sample.mp4', 'media/sample2.mp4'],
                        help='Video files to use (one or two)')
    parser.add_argument('--size', default='1920x1080')
    parser.add_argument('--fullscreen', action='store_true')
    parser.add_argument('--json', help='Also write the results to this file')
    args = parser.parse_args()

    media = [Path(m) for m in args.media]
    missing = [m for m in media if not m.exists()]
    if missing:
        sys.exit(f"Missing media: {', '.join(map(str, missing))}")
    width, height = (int(v) for v in args.size.split('x'))

    results = {}
    if args.scenario == 'all':
        # Each scenario in its own process, so memory numbers don't accumulate.
        import subprocess
        import tempfile
        for name in SCENARIOS:
            with tempfile.NamedTemporaryFile(suffix='.json') as tmp:
                cmd = [sys.executable, __file__, name, '--seconds', str(args.seconds), '--size', args.size,
                       '--json', tmp.name, '--media', *args.media] + (['--fullscreen'] if args.fullscreen else [])
                subprocess.run(cmd, check=True)
                results.update(json.loads(Path(tmp.name).read_text()))
    else:
        print(f"Running {args.scenario} for {args.seconds:.0f}s...")
        results[args.scenario] = run(args.scenario, args.seconds, media, width, height, args.fullscreen)
        print(f"  {args.scenario}: {format_summary(results[args.scenario])}")

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2))
        print(f"Wrote {args.json}")


if __name__ == '__main__':
    main()
