#!/usr/bin/env python3
import argparse
import itertools
import time

import pygame
import sys

from src.config_io import load_config, save_config, validate_config, migrate_config, ConfigWatcher
from src.platform_io import is_raspberry_pi
from src.renderer import CanvasRenderer
from src.calibration import CalibrationUI
from src.playback import Playback, full_canvas_corners
from src.renderer import Layer
from src.screen_detect import DetectionError, capture_and_detect, outline_pattern
from src.sources.base import StaticSource

FPS_LOG_SECONDS = 10


def main():
    parser = argparse.ArgumentParser(description="Projection Mapper")
    parser.add_argument("--calibrate", action="store_true", help="Run in calibration mode")
    parser.add_argument("--play", action="store_true", help="Run in playback mode (default)")
    parser.add_argument("--detect-screen", action="store_true",
                        help="Find the projector screen with the camera and save its corners")
    parser.add_argument("--windowed", action="store_true", help="Run windowed (dev mode)")
    parser.add_argument("--config", type=str, default="config/frames.json", help="Path to config file")
    parser.add_argument("--display", type=int, default=None,
                         help="Index of the display to use for fullscreen (see printed list). "
                              "Defaults to the display matching the canvas resolution, or 0.")

    args = parser.parse_args()

    try:
        config = migrate_config(load_config(args.config))
        validate_config(config)
    except Exception as e:
        print(f"Error loading config: {e}")
        sys.exit(1)

    print(f"Config: {args.config}")

    canvas_cfg = config.get('canvas', {})
    canvas_width = canvas_cfg.get('width', 1920)
    canvas_height = canvas_cfg.get('height', 1080)

    fullscreen = not args.windowed

    print(f"Canvas: {canvas_width}x{canvas_height}")
    print(f"Fullscreen: {fullscreen}")
    print(f"Platform: {'Raspberry Pi' if is_raspberry_pi() else 'macOS/Linux'}")

    display_index = 0
    if fullscreen:
        pygame.init()
        sizes = pygame.display.get_desktop_sizes()
        print("Available displays:")
        for i, size in enumerate(sizes):
            print(f"  {i}: {size[0]}x{size[1]}")

        if args.display is not None:
            if not 0 <= args.display < len(sizes):
                print(f"Error: --display {args.display} out of range (0-{len(sizes) - 1})")
                sys.exit(1)
            display_index = args.display
        else:
            matches = [i for i, size in enumerate(sizes) if size == (canvas_width, canvas_height)]
            if matches:
                display_index = matches[0]
            else:
                print(f"Warning: no display matches canvas {canvas_width}x{canvas_height}; "
                      f"using display 0. Pass --display N to pick a specific one.")
        print(f"Using display {display_index}")

    if args.detect_screen:
        ok = run_detect_screen(config, canvas_width, canvas_height, fullscreen, display_index, args.config)
        sys.exit(0 if ok else 1)
    elif args.calibrate:
        run_calibration(config, canvas_width, canvas_height, fullscreen, display_index, args.config)
    else:
        run_playback(config, canvas_width, canvas_height, fullscreen, display_index, args.config)


def run_calibration(config, canvas_width, canvas_height, fullscreen, display_index=0, config_path=None):
    """Run calibration mode."""
    pygame.init()
    if fullscreen:
        # See renderer.py's init_gl for why fullscreen uses the display's
        # real native resolution instead of canvas_width/height or (0, 0),
        # and why it's created windowed first then switched to fullscreen.
        window_size = pygame.display.get_desktop_sizes()[display_index]
        pygame.display.set_mode(window_size, display=display_index)
        pygame.event.pump()
        pygame.time.wait(100)
        screen = pygame.display.set_mode(window_size, pygame.FULLSCREEN, display=display_index)
    else:
        window_size = (canvas_width, canvas_height)
        screen = pygame.display.set_mode(window_size, display=display_index)
    pygame.display.set_caption("Projection Mapper - Calibration")
    clock = pygame.time.Clock()

    ui = CalibrationUI(canvas_width, canvas_height, config, config_path=config_path)

    print("Calibration Mode")
    print("Controls:")
    print("  Tab / Shift+Tab: cycle frames")
    print("  1-4: select corner (TL, TR, BR, BL)")
    print("  Arrow keys: nudge corner (Shift for 10px)")
    print("  n: add frame")
    print("  d: delete frame")
    print("  s: save config")
    print("  l: reload config")
    print("  Esc / q: quit")

    while ui.running:
        ui.handle_events()
        ui.render_grid(screen)
        pygame.display.flip()
        clock.tick(30)

    pygame.quit()


def detection_camera(config):
    """The camera for screen detection: detection.camera, else the first camera frame's device."""
    detection = config.get('detection') or {}
    if 'camera' in detection:
        return detection['camera'], tuple(detection.get('camera_size', (1920, 1080)))
    for frame in config.get('frames', []):
        source = frame.get('source') or {}
        if source.get('type') == 'camera':
            return source['device'], tuple(detection.get('camera_size', (1920, 1080)))
    return None, None


def run_detect_screen(config, canvas_width, canvas_height, fullscreen, display_index=0, config_path=None):
    """Project calibration patterns, find the screen with the camera, and save its corners on confirmation."""
    device, camera_size = detection_camera(config)
    if device is None:
        print('No camera configured: set "detection": {"camera": "/dev/v4l/by-id/..."} in the config')
        return False

    renderer = CanvasRenderer(canvas_width, canvas_height, fullscreen=fullscreen, display_index=display_index)
    full = full_canvas_corners(canvas_width, canvas_height)
    counter = itertools.count()

    def show(rgba):
        # A fresh layer key per pattern, so the renderer uploads the new image.
        renderer.render([Layer(f"pattern{next(counter)}", full, StaticSource(rgba))])

    try:
        renderer.init_gl()
        print(f"Detecting the screen with camera {device} ...")
        try:
            result = capture_and_detect(show, pygame.event.pump, device, (canvas_width, canvas_height),
                                        camera_size)
        except DetectionError as e:
            print(f"Screen detection failed: {e}")
            print("The camera photos are in captures/ for troubleshooting.")
            return False

        for warning in result.warnings:
            print(f"Warning: {warning}")
        print("Detected screen corners:", result.corners)
        print("Check the green outline sits on the inner edge of the black border.")
        print("Press Enter or s to save it, Esc or q to discard.")

        show(outline_pattern(canvas_width, canvas_height, result.corners))
        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return False
                if event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_s):
                        corners = {k: [round(v, 1) for v in xy] for k, xy in result.corners.items()}
                        config.setdefault('screen', {})['corners'] = corners
                        save_config(config, config_path)
                        print(f"Saved screen corners to {config_path}")
                        return True
                    if event.key in (pygame.K_ESCAPE, pygame.K_q):
                        print("Discarded")
                        return False
            pygame.time.wait(30)
    finally:
        renderer.close()


def run_playback(config, canvas_width, canvas_height, fullscreen, display_index=0, config_path=None):
    """Run playback mode. Edits to the config file are picked up live."""
    renderer = CanvasRenderer(canvas_width, canvas_height, fullscreen=fullscreen, display_index=display_index)
    playback = None

    try:
        renderer.init_gl()
        playback = Playback(config, renderer)
        playback.start()

        print("Playback Mode - Press Esc to quit")

        watcher = ConfigWatcher(config_path) if config_path else None
        clock = pygame.time.Clock()
        running = True
        frames_drawn = 0
        fps_window_start = time.monotonic()

        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False

            if watcher:
                new_config = watcher.check(time.monotonic())
                if new_config is not None:
                    if new_config['canvas'] != config['canvas']:
                        print("Canvas size changed; restart to apply it")
                    playback.apply_config(new_config)

            playback.tick()
            clock.tick(30)

            frames_drawn += 1
            elapsed = time.monotonic() - fps_window_start
            if elapsed >= FPS_LOG_SECONDS:
                print(f"FPS: {frames_drawn / elapsed:.1f}")
                frames_drawn = 0
                fps_window_start = time.monotonic()

    finally:
        if playback:
            playback.close()
        renderer.close()


if __name__ == "__main__":
    main()
