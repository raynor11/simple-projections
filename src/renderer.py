import os
import time
from dataclasses import dataclass

import cv2
import numpy as np
import moderngl as mgl
import pygame

from .homography import corners_to_array, quad_footprint, uv_homography
from .sources.base import FULL_CROP


VERTEX_SRC = """
in vec2 position;
void main() {
    gl_Position = vec4(position, 0.0, 1.0);
}
"""

# Texture UVs are computed per pixel from the inverse homography rather than
# interpolated from per-vertex UVs. Interpolating UVs across the quad's two
# triangles is affine, which bends the image along the diagonal whenever the
# quad is keystoned; the per-pixel projective divide is exact.
FRAGMENT_SRC = """
uniform sampler2D texture0;
uniform mat3 uv_from_px;
uniform vec4 viewport;
uniform vec2 canvas_size;
uniform vec4 uv_crop;
uniform float alpha;
uniform float dim;
uniform int swap_rb;
out vec4 color;
void main() {
    vec2 win = (gl_FragCoord.xy - viewport.xy) / viewport.zw;
    // Window space is y-up; canvas pixels are y-down (top-left origin).
    vec2 px = vec2(win.x, 1.0 - win.y) * canvas_size;
    vec3 h = uv_from_px * vec3(px, 1.0);
    vec2 uv = h.xy / h.z;
    if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) {
        discard;
    }
    vec4 c = texture(texture0, mix(uv_crop.xy, uv_crop.zw, uv));
    if (swap_rb == 1) {
        c = c.bgra;   // BGR(A) frames straight from OpenCV
    }
    color = vec4(c.rgb * dim, c.a * alpha);
}
"""

# Desktop GL 3.3 first (macOS, or the Pi with the Mesa version override the
# service sets); GLSL 1.40 is the fallback for the Pi 4's native GL 3.1.
GLSL_VERSIONS = ("#version 330 core\n", "#version 140\n")

# Some GPU drivers convert 3-channel uploads on the CPU internally. Set
# PM_UPLOAD_RGBA=1 to pad frames to 4 channels instead (compare both with
# scripts/benchmark.py on the Pi).
UPLOAD_RGBA = os.environ.get('PM_UPLOAD_RGBA') == '1'
# Textures shown at less than 1/MIPMAP_RATIO of their size get mipmaps, so
# heavy downscaling doesn't shimmer.
MIPMAP_RATIO = 2.0


@dataclass
class Layer:
    """One frame to draw this tick."""
    key: str
    corners: dict          # {'tl': [x, y], ...} in canvas pixels
    source: object         # sources.base.Source
    alpha: float = 1.0
    crop: tuple = FULL_CROP


class _LayerGL:
    """GPU resources for one layer, kept across ticks."""

    def __init__(self, ctx, program, ebo):
        self.vbo = ctx.buffer(reserve=4 * 2 * 4)
        self.vao = ctx.vertex_array(program, [(self.vbo, '2f', 'position')], ebo)
        self.texture = None
        self.version = None
        self.corners_key = None
        self.uv_from_px = None
        self.footprint = None      # on-canvas size (w, h) of the quad
        self.mipmapped = False

    def release(self):
        self.vao.release()
        self.vbo.release()
        if self.texture:
            self.texture.release()


class CanvasRenderer:
    """Draws warped frames onto the canvas with OpenGL."""

    def __init__(self, canvas_width, canvas_height, fullscreen=False, display_index=0):
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height
        self.fullscreen = fullscreen
        self.display_index = display_index
        self.ctx = None
        self.program = None
        self.ebo = None
        self.layers = {}
        # (upload ms, draw ms, bytes uploaded, textures uploaded, frames dropped) for the last render
        self.last_render_stats = (0.0, 0.0, 0, 0, 0)

    def init_gl(self):
        """Initialize pygame and OpenGL context."""
        pygame.init()
        try:
            self._open_window(gl_version=(3, 3))
        except pygame.error as e:
            print(f"GL 3.3 context unavailable ({e}); falling back to GL 3.1")
            self._open_window(gl_version=(3, 1))
        pygame.display.set_caption("Projection Mapper")
        pygame.mouse.set_visible(not self.fullscreen)

        self.ctx = mgl.create_context(require=310)
        print(f"OpenGL: {self.ctx.info['GL_RENDERER']} / {self.ctx.info['GL_VERSION']}")
        self._create_shader_program()
        self.ebo = self.ctx.buffer(np.array([0, 1, 2, 0, 2, 3], dtype='i4'))
        self.ctx.enable(mgl.BLEND)
        self.ctx.blend_func = (mgl.SRC_ALPHA, mgl.ONE_MINUS_SRC_ALPHA)

    def _open_window(self, gl_version):
        major, minor = gl_version
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, major)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, minor)
        if gl_version >= (3, 2):
            pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_CORE)
            pygame.display.gl_set_attribute(pygame.GL_CONTEXT_FORWARD_COMPATIBLE_FLAG, True)
        else:
            pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK, 0)
            pygame.display.gl_set_attribute(pygame.GL_CONTEXT_FORWARD_COMPATIBLE_FLAG, False)

        flags = pygame.DOUBLEBUF | pygame.OPENGL
        if self.fullscreen:
            # Use the display's actual native resolution rather than
            # canvas_width/height -- forcing a resolution the display
            # doesn't natively support (e.g. on a Retina Mac) makes
            # fullscreen fail to cover the screen. Passing (0, 0) instead
            # would sidestep that, but on macOS it makes SDL fall back to a
            # borderless "fullscreen desktop" window (looks maximized,
            # menu bar/dock still reachable) rather than a true fullscreen
            # Space transition, so we look up and pass the real size.
            window_size = pygame.display.get_desktop_sizes()[self.display_index]
            # Requesting FULLSCREEN at window creation can race with macOS
            # granting the freshly-launched process focus, silently
            # dropping the fullscreen Space transition. Create windowed
            # first, let the event queue settle, then switch to fullscreen.
            pygame.display.set_mode(window_size, flags, display=self.display_index)
            pygame.event.pump()
            pygame.time.wait(100)
            pygame.display.set_mode(window_size, pygame.FULLSCREEN | flags, display=self.display_index)
        else:
            window_size = (self.canvas_width, self.canvas_height)
            pygame.display.set_mode(window_size, flags, display=self.display_index)

    def _create_shader_program(self):
        last_error = None
        for header in GLSL_VERSIONS:
            try:
                self.program = self.ctx.program(vertex_shader=header + VERTEX_SRC,
                                                fragment_shader=header + FRAGMENT_SRC)
                return
            except mgl.Error as e:
                last_error = e
        raise RuntimeError(f"Could not compile shaders: {last_error}")

    def render(self, layers, dim=1.0):
        """
        Draw the given layers (in order, later on top) and present. dim
        scales the output brightness (e.g. 0.75 at night).
        """
        self.ctx.clear(0.0, 0.0, 0.0, 1.0)
        self.program['dim'].value = float(max(0.0, min(1.0, dim)))
        viewport = self.ctx.viewport
        self.program['viewport'].value = tuple(float(v) for v in viewport)
        self.program['canvas_size'].value = (float(self.canvas_width), float(self.canvas_height))
        self.program['texture0'].value = 0

        start = time.perf_counter()
        upload_s = 0.0
        self._uploaded_bytes = self._uploads = self._dropped = 0
        drawn = set()
        for layer in layers:
            drawn.add(layer.key)
            gl = self.layers.get(layer.key)
            if gl is None:
                gl = self.layers[layer.key] = _LayerGL(self.ctx, self.program, self.ebo)
            self._update_geometry(gl, layer.corners)
            t = time.perf_counter()
            has_texture = self._update_texture(gl, layer.source)
            upload_s += time.perf_counter() - t
            if has_texture and layer.alpha > 0.0:
                self._draw(gl, layer)

        for key in list(self.layers):
            if key not in drawn:
                self.layers.pop(key).release()

        total_s = time.perf_counter() - start
        self.last_render_stats = (upload_s * 1000, (total_s - upload_s) * 1000,
                                  self._uploaded_bytes, self._uploads, self._dropped)
        pygame.display.flip()

    def _update_texture(self, gl, source):
        """Upload the source's newest image if it changed. Returns whether there's anything to draw."""
        data = source.latest()
        if data is None:
            return gl.texture is not None
        version, img = data
        if version == gl.version:
            return True
        if gl.version is not None and version > gl.version + 1:
            self._dropped += version - gl.version - 1   # frames the source made that were never shown
        if img.ndim == 2:
            img = img[:, :, None]
        if UPLOAD_RGBA and img.shape[2] == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        h, w, components = img.shape
        img = np.ascontiguousarray(img)
        self._uploaded_bytes += img.nbytes
        self._uploads += 1
        mipmaps = (gl.footprint is not None and
                   (w > MIPMAP_RATIO * gl.footprint[0] or h > MIPMAP_RATIO * gl.footprint[1]))
        if (gl.texture is None or gl.texture.size != (w, h) or gl.texture.components != components
                or gl.mipmapped != mipmaps):
            if gl.texture:
                gl.texture.release()
            # alignment=1: 3-channel rows aren't always a multiple of 4 bytes.
            gl.texture = self.ctx.texture((w, h), components, img, alignment=1)
            gl.texture.repeat_x = False
            gl.texture.repeat_y = False
            gl.mipmapped = mipmaps
            gl.texture.filter = ((mgl.LINEAR_MIPMAP_LINEAR, mgl.LINEAR) if mipmaps
                                 else (mgl.LINEAR, mgl.LINEAR))
        else:
            gl.texture.write(img, alignment=1)
        if mipmaps:
            gl.texture.build_mipmaps()
        gl.version = version
        gl.swap_rb = source.pixel_format.startswith('BGR')
        return True

    def _update_geometry(self, gl, corners):
        quad = corners_to_array(corners)
        key = quad.tobytes()
        if key == gl.corners_key:
            return
        ndc = np.empty_like(quad)
        ndc[:, 0] = quad[:, 0] / self.canvas_width * 2.0 - 1.0
        # Corners are y-down canvas pixels; clip space is y-up.
        ndc[:, 1] = 1.0 - quad[:, 1] / self.canvas_height * 2.0
        gl.vbo.write(ndc.astype('f4').tobytes())
        # GLSL mat3 is column-major; transposing the row-major numpy matrix
        # lays its bytes out that way.
        gl.uv_from_px = np.ascontiguousarray(uv_homography(corners).T, dtype='f4').tobytes()
        gl.footprint = quad_footprint(corners)
        gl.corners_key = key

    def _draw(self, gl, layer):
        gl.texture.use(0)
        self.program['uv_from_px'].write(gl.uv_from_px)
        self.program['uv_crop'].value = tuple(float(v) for v in layer.crop)
        self.program['alpha'].value = float(max(0.0, min(1.0, layer.alpha)))
        self.program['swap_rb'].value = 1 if getattr(gl, 'swap_rb', False) else 0
        gl.vao.render(mgl.TRIANGLES)

    def close(self):
        """Cleanup OpenGL resources."""
        for gl in self.layers.values():
            gl.release()
        self.layers.clear()
        if self.ctx:
            self.ctx.release()
            self.ctx = None
        pygame.quit()
