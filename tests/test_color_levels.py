import numpy as np
import pytest

from src.renderer import FRAGMENT_SRC, GLSL_VERSIONS, NO_LEVELS, VERTEX_SRC
from src.sources.file import HW_CPU, HW_ISP, ISP_LEVELS, SOFTWARE, decoder_levels


def apply(levels, rgb):
    scale, offset = levels
    return np.clip(np.asarray(rgb) / 255 * scale + offset, 0, 1) * 255


def test_only_the_isp_needs_correcting():
    assert decoder_levels(HW_ISP) == ISP_LEVELS
    assert decoder_levels(HW_CPU) is None
    assert decoder_levels(SOFTWARE) is None


def test_isp_levels_expand_tv_range_rgb_to_full():
    black, white = apply(ISP_LEVELS, [16, 16, 16]), apply(ISP_LEVELS, [235, 235, 235])
    assert np.all(black < 3)
    assert np.all(white > 250)
    # Mid-dark, where the washed-out ISP frames differed most (measured on the Pi).
    np.testing.assert_allclose(apply(ISP_LEVELS, [76.5, 50.2, 40.2]), [67.9, 39.7, 27.1], atol=1.5)


def render_pixel(rgb, levels):
    """Draw one RGB pixel through the real fragment shader; skips without OpenGL."""
    mgl = pytest.importorskip('moderngl')
    try:
        ctx = mgl.create_standalone_context(require=330)
    except Exception:
        try:
            ctx = mgl.create_standalone_context()
        except Exception as e:
            pytest.skip(f"No OpenGL context: {e}")
    try:
        prog = None
        for header in GLSL_VERSIONS:
            try:
                prog = ctx.program(vertex_shader=header + VERTEX_SRC, fragment_shader=header + FRAGMENT_SRC)
                break
            except Exception:
                continue
        assert prog is not None, "fragment shader didn't compile"
        fbo = ctx.simple_framebuffer((1, 1))
        fbo.use()
        tex = ctx.texture((1, 1), 3, bytes(rgb))
        tex.use(0)
        prog['texture0'].value = 0
        prog['uv_from_px'].write(np.eye(3, dtype='f4').tobytes())   # 1 canvas px = whole texture
        prog['viewport'].value = (0.0, 0.0, 1.0, 1.0)
        prog['canvas_size'].value = (0.5, 0.5)
        prog['uv_crop'].value = (0.0, 0.0, 1.0, 1.0)
        prog['alpha'].value = 1.0
        prog['dim'].value = 1.0
        prog['swap_rb'].value = 0
        prog['levels_scale'].value = levels[0]
        prog['levels_offset'].value = levels[1]
        vbo = ctx.buffer(np.array([-1, -1, 3, -1, -1, 3], dtype='f4').tobytes())
        ctx.vertex_array(prog, [(vbo, '2f', 'position')]).render()
        return np.frombuffer(fbo.read(components=3), np.uint8)
    finally:
        ctx.release()


def test_shader_leaves_uncorrected_sources_alone():
    assert list(render_pixel([12, 128, 250], NO_LEVELS)) == [12, 128, 250]


def test_shader_applies_isp_levels():
    out = render_pixel([76, 50, 40], ISP_LEVELS)
    np.testing.assert_allclose(out, apply(ISP_LEVELS, [76, 50, 40]), atol=1)
