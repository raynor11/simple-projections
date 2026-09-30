"""Frame content sources: files, cameras, capture cards, text, weather, casting."""


def source_config(frame_cfg):
    """
    The frame's source config, migrating the legacy "media": "path" form to
    {"type": "file", "path": ...}.
    """
    if 'source' in frame_cfg:
        return frame_cfg['source']
    if 'media' in frame_cfg:
        return {'type': 'file', 'path': frame_cfg['media']}
    return None


def create_source(src_cfg, context=None):
    """
    Build a source from its config. `context` carries app-wide settings that
    some sources need (e.g. "location" for weather).
    """
    context = context or {}
    kind = src_cfg.get('type')
    if kind == 'file':
        from .file import create_file_source
        return create_file_source(src_cfg)
    if kind == 'camera':
        from .v4l2 import V4L2Source
        return V4L2Source(src_cfg)
    if kind == 'text':
        from .text import TextSource
        return TextSource(src_cfg)
    if kind == 'weather':
        from .weather import WeatherSource
        return WeatherSource(src_cfg, location=context.get('location'))
    if kind == 'chromecast':
        from .chromecast import ChromecastSource
        return ChromecastSource(src_cfg)
    if kind == 'airplay':
        from .airplay import AirPlaySource
        return AirPlaySource(src_cfg)
    raise ValueError(f"Unknown source type: {kind!r}")
