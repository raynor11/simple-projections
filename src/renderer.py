import numpy as np
import moderngl as mgl
import pygame
import cv2
from .homography import compute_homography
from .media_loader import MediaLoader


class CanvasRenderer:
    """Manages OpenGL rendering of warped frames onto a canvas."""

    def __init__(self, canvas_width, canvas_height, fullscreen=False, display_index=0):
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height
        self.fullscreen = fullscreen
        self.display_index = display_index
        self.ctx = None
        self.program = None
        self.vao = None
        self.fbo = None
        self.frames = {}
        self.media_loaders = {}

    def init_gl(self):
        """Initialize pygame and OpenGL context."""
        pygame.init()
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_CORE)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_FORWARD_COMPATIBLE_FLAG, True)
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
            pygame.display.set_mode(window_size, pygame.DOUBLEBUF | pygame.OPENGL, display=self.display_index)
            pygame.event.pump()
            pygame.time.wait(100)
            pygame.display.set_mode(window_size, pygame.FULLSCREEN | pygame.DOUBLEBUF | pygame.OPENGL, display=self.display_index)
        else:
            window_size = (self.canvas_width, self.canvas_height)
            pygame.display.set_mode(window_size, pygame.DOUBLEBUF | pygame.OPENGL, display=self.display_index)
        pygame.display.set_caption("Projection Mapper")

        self.ctx = mgl.create_context()
        self._create_shader_program()
        self._create_canvas_framebuffer()

    def _create_shader_program(self):
        """Compile vertex and fragment shaders."""
        vertex_src = """
        #version 330 core
        in vec2 position;
        in vec2 uv;
        out vec2 frag_uv;
        uniform mat4 transform;
        void main() {
            frag_uv = uv;
            gl_Position = transform * vec4(position, 0.0, 1.0);
        }
        """

        fragment_src = """
        #version 330 core
        in vec2 frag_uv;
        out vec4 color;
        uniform sampler2D texture0;
        void main() {
            if (frag_uv.x < 0.0 || frag_uv.x > 1.0 || frag_uv.y < 0.0 || frag_uv.y > 1.0) {
                color = vec4(0.0);
            } else {
                color = texture(texture0, frag_uv);
            }
        }
        """

        self.program = self.ctx.program(vertex_shader=vertex_src, fragment_shader=fragment_src)

    def _create_canvas_framebuffer(self):
        """Create framebuffer for off-screen canvas rendering."""
        self.ctx.enable(mgl.BLEND)
        self.ctx.blend_func = (mgl.SRC_ALPHA, mgl.ONE_MINUS_SRC_ALPHA)

    def register_frame(self, frame_id, frame_config, media_path):
        """Register a frame with media and corners."""
        self.frames[frame_id] = frame_config
        try:
            self.media_loaders[frame_id] = MediaLoader(media_path)
        except Exception as e:
            print(f"Error loading media for frame {frame_id}: {e}")

    def render_frame(self):
        """Render all frames to the canvas and present."""
        self.ctx.clear(0.0, 0.0, 0.0, 1.0)

        for frame_id, frame_config in self.frames.items():
            if frame_id not in self.media_loaders:
                continue

            loader = self.media_loaders[frame_id]
            frame_data = loader.get_next_frame()

            if frame_data is None:
                continue

            img, src_w, src_h = frame_data

            img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            img_rgb = np.ascontiguousarray(img_rgb)

            texture = self.ctx.texture(
                (src_w, src_h),
                3,
                img_rgb.tobytes()
            )

            self._render_quad(frame_config, src_w, src_h, texture)
            texture.release()

        pygame.display.flip()

    def _render_quad(self, frame_config, src_w, src_h, texture):
        """Render a single warped quad."""
        corners = frame_config['corners']
        quad = np.array([
            corners['tl'],
            corners['tr'],
            corners['br'],
            corners['bl']
        ], dtype=np.float32)

        quad_normalized = quad.copy()
        quad_normalized[:, 0] = (quad[:, 0] / self.canvas_width) * 2.0 - 1.0
        # Corners are in top-left-origin, y-down pixel space (matching what
        # calibration mode shows and saves), but OpenGL clip space is
        # y-up (-1 = bottom, +1 = top). Without flipping, a corner
        # calibrated near the top of the screen renders near the bottom.
        quad_normalized[:, 1] = 1.0 - (quad[:, 1] / self.canvas_height) * 2.0

        vertices = np.array([
            quad_normalized[0, 0], quad_normalized[0, 1], 0, 0,
            quad_normalized[1, 0], quad_normalized[1, 1], 1, 0,
            quad_normalized[2, 0], quad_normalized[2, 1], 1, 1,
            quad_normalized[3, 0], quad_normalized[3, 1], 0, 1,
        ], dtype='f4')

        indices = np.array([0, 1, 2, 0, 2, 3], dtype='i4')

        vbo = self.ctx.buffer(vertices)
        ebo = self.ctx.buffer(indices)
        vao = self.ctx.vertex_array(self.program, [(vbo, '2f 2f', 'position', 'uv')], ebo)

        texture.use(0)
        self.program['texture0'].value = 0

        transform = np.eye(4, dtype=np.float32)
        self.program['transform'].write(transform)

        vao.render(mgl.TRIANGLES)
        vao.release()
        vbo.release()
        ebo.release()

    def close(self):
        """Cleanup OpenGL resources."""
        for loader in self.media_loaders.values():
            loader.close()

        if self.ctx:
            self.ctx.release()

        pygame.quit()

    def __del__(self):
        self.close()
