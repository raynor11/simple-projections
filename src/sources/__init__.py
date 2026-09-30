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
    raise ValueError(f"Unknown source type: {kind!r}")
