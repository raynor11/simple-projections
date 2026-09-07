import numpy as np
import moderngl as mgl
import pygame
import cv2
from .homography import compute_homography
from .media_loader import MediaLoader


class CanvasRenderer:
    """Manages OpenGL rendering of warped frames onto a canvas."""

    def __init__(self, canvas_width, canvas_height, fullscreen=False):
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height
        self.fullscreen = fullscreen
        self.ctx = None
        self.program = None
        self.vao = None
        self.fbo = None
        self.frames = {}
        self.media_loaders = {}

    def init_gl(self):
        """Initialize pygame and OpenGL context."""
        flags = pygame.FULLSCREEN if self.fullscreen else 0
        pygame.init()
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_CORE)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_FORWARD_COMPATIBLE_FLAG, True)
        pygame.display.set_mode((self.canvas_width, self.canvas_height), flags | pygame.DOUBLEBUF | pygame.OPENGL)
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
            img_flipped = cv2.flip(img_rgb, 0)
            img_flipped = np.ascontiguousarray(img_flipped)

            texture = self.ctx.texture(
                (src_w, src_h),
                3,
                img_flipped.tobytes()
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
        quad_normalized[:, 1] = (quad[:, 1] / self.canvas_height) * 2.0 - 1.0

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
