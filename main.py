#!/usr/bin/env python3
import argparse
import pygame
import sys
from pathlib import Path

from src.config_io import load_config, validate_config
from src.platform_io import get_display_size, setup_fullscreen_window, is_raspberry_pi
from src.renderer import CanvasRenderer
from src.calibration import CalibrationUI


def main():
    parser = argparse.ArgumentParser(description="Projection Mapper")
    parser.add_argument("--calibrate", action="store_true", help="Run in calibration mode")
    parser.add_argument("--play", action="store_true", help="Run in playback mode (default)")
    parser.add_argument("--windowed", action="store_true", help="Run windowed (dev mode)")
    parser.add_argument("--config", type=str, default="config/frames.json", help="Path to config file")

    args = parser.parse_args()

    try:
        config = load_config()
        validate_config(config)
    except Exception as e:
        print(f"Error loading config: {e}")
        sys.exit(1)

    canvas_cfg = config.get('canvas', {})
    canvas_width = canvas_cfg.get('width', 1920)
    canvas_height = canvas_cfg.get('height', 1080)

    fullscreen = not args.windowed

    print(f"Canvas: {canvas_width}x{canvas_height}")
    print(f"Fullscreen: {fullscreen}")
    print(f"Platform: {'Raspberry Pi' if is_raspberry_pi() else 'macOS/Linux'}")

    if args.calibrate:
        run_calibration(config, canvas_width, canvas_height)
    else:
        run_playback(config, canvas_width, canvas_height, fullscreen)


def run_calibration(config, canvas_width, canvas_height):
    """Run calibration mode."""
    pygame.init()
    screen = pygame.display.set_mode((canvas_width, canvas_height))
    pygame.display.set_caption("Projection Mapper - Calibration")
    clock = pygame.time.Clock()

    ui = CalibrationUI(canvas_width, canvas_height, config)

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


def run_playback(config, canvas_width, canvas_height, fullscreen):
    """Run playback mode."""
    renderer = CanvasRenderer(canvas_width, canvas_height, fullscreen=fullscreen)

    try:
        renderer.init_gl()

        for frame in config.get('frames', []):
            frame_id = frame.get('id')
            media_path = frame.get('media')
            if frame_id and media_path:
                renderer.register_frame(frame_id, frame, media_path)

        print("Playback Mode - Press Esc to quit")

        clock = pygame.time.Clock()
        running = True

        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False

            renderer.render_frame()
            clock.tick(30)

    finally:
        renderer.close()


if __name__ == "__main__":
    main()
