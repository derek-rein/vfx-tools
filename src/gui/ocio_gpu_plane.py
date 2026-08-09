"""GPU OCIO image plane for realtime full-resolution slate/shot preview.

Architecture (Nuke viewer–aligned):

* Working-space RGB is uploaded once per frame as a float texture.
* Gain (as exposure stops) + Display/view run in OCIO's GPU processor
  (``ExposureContrastTransform`` LINEAR → ``DisplayViewTransform``).
* Viewer **gamma is not** OCIO EC gamma. Nuke applies
  ``pow(display, 1/γ)`` *after* the Viewer Process; we do the same in the
  fragment shader after ``OCIOMain``.

macOS note: Core Profile has no ``sampler1D``. OCIO still emits 1D LUT
samplers for some ACES ops; we promote those LUTs to 1×N ``sampler2D`` and
rewrite the generated GLSL so the program links on Apple GL 4.1.
"""

from __future__ import annotations

import ctypes
import logging
import re
from typing import Any

import numpy as np
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QMouseEvent, QSurfaceFormat, QWheelEvent
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)

ZOOM_MIN = 0.02
ZOOM_MAX = 64.0


def nuke_viewer_gamma_power(gamma: float) -> float:
    """Nuke viewer γ → power exponent: ``output = input ** (1/γ)``.

    γ = 1 identity; γ > 1 lightens display midtones; γ < 1 darkens them.
    Same inversion as the Gamma node / ViewerProcess gamma control.
    """
    return 1.0 / max(float(gamma), 1e-3)


# Back-compat alias (older call sites / docs referred to OCIO EC mapping).
nuke_gamma_to_ocio = nuke_viewer_gamma_power

# GLSL: Nuke post-display gamma. Negatives left unchanged (no NaN/complex).
_GLSL_NUKE_GAMMA = """
uniform float viewerGamma;

vec3 applyNukeViewerGamma(vec3 rgb) {
    float e = 1.0 / max(viewerGamma, 1e-3);
    // pow only on non-negative channels; match Nuke (negatives untouched).
    vec3 pos = pow(max(rgb, 0.0), vec3(e));
    return mix(rgb, pos, step(vec3(0.0), rgb));
}
"""

_GLSL_VERT = """#version 410 core
uniform mat4 mvpMat;
layout(location = 0) in vec2 in_position;
layout(location = 1) in vec2 in_texCoord;
out vec2 vert_texCoord;
void main() {
    vert_texCoord = in_texCoord;
    gl_Position = mvpMat * vec4(in_position, 0.0, 1.0);
}
"""

_GLSL_FRAG_PASSTHROUGH = (
    """#version 410 core
uniform sampler2D imageTex;
uniform sampler2D overlayTex;
uniform int hasOverlay;
"""
    + _GLSL_NUKE_GAMMA
    + """
in vec2 vert_texCoord;
out vec4 frag_color;

vec4 samplePlate() {
    vec4 plate = texture(imageTex, vert_texCoord);
    if (hasOverlay != 0) {
        vec4 ov = texture(overlayTex, vert_texCoord);
        plate.rgb = ov.rgb * ov.a + plate.rgb * (1.0 - ov.a);
    }
    plate.a = 1.0;
    return plate;
}

void main() {
    vec4 c = samplePlate();
    c.rgb = applyNukeViewerGamma(c.rgb);
    frag_color = c;
}
"""
)

# Placeholder ``@@OCIO_SRC@@`` (not str.format) so OCIO GLSL braces stay intact.
_GLSL_FRAG_OCIO_FMT = (
    """#version 410 core
uniform sampler2D imageTex;
uniform sampler2D overlayTex;
uniform int hasOverlay;
"""
    + _GLSL_NUKE_GAMMA
    + """
in vec2 vert_texCoord;
out vec4 frag_color;
@@OCIO_SRC@@

vec4 samplePlate() {
    vec4 plate = texture(imageTex, vert_texCoord);
    if (hasOverlay != 0) {
        vec4 ov = texture(overlayTex, vert_texCoord);
        // Overlay is pre-linearised working-space RGBA (authoring→working on CPU once).
        plate.rgb = ov.rgb * ov.a + plate.rgb * (1.0 - ov.a);
    }
    plate.a = 1.0;
    return plate;
}

void main() {
    // Nuke order: gain (in OCIO EC) → Viewer Process (DVT) → gamma power.
    vec4 c = OCIOMain(samplePlate());
    c.rgb = applyNukeViewerGamma(c.rgb);
    frag_color = c;
}
"""
)


def gpu_ocio_available() -> bool:
    """Return True when PyOpenGL + QOpenGLWidget are importable."""
    try:
        from OpenGL import GL  # noqa: F401
        from PySide6.QtOpenGLWidgets import QOpenGLWidget as _W  # noqa: F401

        return True
    except Exception:
        return False


def configure_default_gl_format() -> None:
    """Core Profile 4.1 default format (OCIO GLSL 4.x). Call before QApplication."""
    fmt = QSurfaceFormat()
    fmt.setVersion(4, 1)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setDepthBufferSize(0)
    fmt.setStencilBufferSize(0)
    fmt.setSwapBehavior(QSurfaceFormat.SwapBehavior.DoubleBuffer)
    fmt.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(fmt)


def _rewrite_sampler1d_to_2d(shader_src: str) -> str:
    """Promote OCIO ``sampler1D`` usage to Core-Profile-legal ``sampler2D``.

    Replaces declarations and ``texture(name, t)`` → ``texture(name, vec2(t, 0.5))``
    for each 1D sampler name. Handles nested parentheses in *t*.
    """
    names = set(re.findall(r"\buniform\s+sampler1D\s+(\w+)\s*;", shader_src))
    if not names:
        return shader_src

    out = re.sub(r"\buniform\s+sampler1D\s+", "uniform sampler2D ", shader_src)

    def _rewrite_texture_calls(src: str, sampler: str) -> str:
        result: list[str] = []
        i = 0
        n = len(src)
        while i < n:
            m = re.search(
                rf"texture\s*\(\s*{re.escape(sampler)}\s*,",
                src[i:],
            )
            if not m:
                result.append(src[i:])
                break
            start = i + m.start()
            result.append(src[i:start])
            j = i + m.end()
            while j < n and src[j].isspace():
                j += 1
            expr_start = j
            depth = 0
            while j < n:
                ch = src[j]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    if depth == 0:
                        break
                    depth -= 1
                j += 1
            if j >= n:
                result.append(src[start:])
                break
            expr = src[expr_start:j].strip()
            result.append(f"texture({sampler}, vec2({expr}, 0.5))")
            i = j + 1
        return "".join(result)

    for name in names:
        out = _rewrite_texture_calls(out, name)
    return out


class OcioGpuImagePlane(QOpenGLWidget):
    """Full-res working-space image plane with OCIO GPU display transform.

    Hot path (Nuke-aligned):
      * plate texture upload only when the frame changes
      * overlay texture upload only when burn-in/watermark content changes
      * gain → OCIO EC exposure dynamic property + redraw
      * gamma → post-``OCIOMain`` ``pow(rgb, 1/γ)`` uniform (not OCIO EC gamma)
      * fragment: alpha-over overlay → OCIOMain (gain+display) → Nuke gamma
    """

    gpu_failed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(200, 150)
        self.setUpdateBehavior(QOpenGLWidget.UpdateBehavior.NoPartialUpdate)

        self._gl_ready = False
        self._gpu_dead = False
        self._image: np.ndarray | None = None
        self._image_w = 1
        self._image_h = 1
        self._tex_w = 0
        self._tex_h = 0
        self._pending_upload = False
        self._fitted_once = False

        self._overlay: np.ndarray | None = None
        self._overlay_tex_w = 0  # allocated GL texture size (not content)
        self._overlay_tex_h = 0
        self._overlay_pending = False
        self._has_overlay = False
        self._overlay_key: object = None

        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._panning = False
        self._zooming = False
        self._last_pos: QPointF | None = None
        self._zoom_anchor: QPointF | None = None

        self._ocio_cfg: Any = None
        self._working_space = ""
        self._display = ""
        self._view = ""
        self._exposure_stops = 0.0
        self._gamma = 1.0
        self._view_key: tuple[str, str, str] | None = None

        self._shader_desc = None
        self._proc_cache_id = ""
        self._shader_cache_id = ""
        self._exp_prop = None

        self._image_tex = 0
        self._overlay_tex = 0
        self._vao = 0
        self._vbo = 0
        self._program = 0
        self._vert_shader = 0
        self._ocio_tex_ids: list[tuple] = []
        self._ocio_uniform_ids: dict[str, int] = {}
        self._ocio_tex_start = 2  # 0=plate, 1=overlay
        self._mvp_loc = -1
        self._image_tex_loc = -1
        self._overlay_tex_loc = -1
        self._has_overlay_loc = -1
        self._viewer_gamma_loc = -1

    # -- Public API -----------------------------------------------------------

    def set_working_image(self, rgb: np.ndarray | None) -> None:
        """Upload working-space RGB (H,W,3) float16/float32. Prefer float16 from cache."""
        if self._gpu_dead:
            return
        if rgb is None:
            self._image = None
            self.update()
            return
        arr = rgb
        if arr.dtype not in (np.float16, np.float32):
            arr = np.ascontiguousarray(arr, dtype=np.float32)
        elif not arr.flags["C_CONTIGUOUS"]:
            arr = np.ascontiguousarray(arr)
        if arr.ndim != 3 or arr.shape[2] < 3:
            log.warning("OcioGpuImagePlane: expected HxWx3 image")
            return
        if arr.shape[2] > 3:
            arr = np.ascontiguousarray(arr[..., :3])
        size_changed = (
            self._image is None or self._image_w != arr.shape[1] or self._image_h != arr.shape[0]
        )
        self._image = arr
        self._image_h, self._image_w = int(arr.shape[0]), int(arr.shape[1])
        self._pending_upload = True
        if size_changed or not self._fitted_once:
            self._fitted_once = True
            self.fit_in_view()
        else:
            self.update()

    def set_overlay_rgba(self, rgba_lin: np.ndarray | None, *, key: object = None) -> None:
        """Upload pre-linearised working-space RGBA overlay, or clear.

        Pass a stable *key* (e.g. overlay signature) so unchanged overlays skip
        re-upload during playback.
        """
        if self._gpu_dead:
            return
        if rgba_lin is None:
            if self._has_overlay or self._overlay is not None:
                self._overlay = None
                self._has_overlay = False
                self._overlay_key = None
                self._overlay_pending = True
                self.update()
            return
        if key is not None and key == self._overlay_key and self._has_overlay:
            return
        arr = rgba_lin
        if arr.dtype != np.float32 or not arr.flags["C_CONTIGUOUS"]:
            arr = np.ascontiguousarray(arr, dtype=np.float32)
        if arr.ndim != 3 or arr.shape[2] < 4:
            return
        self._overlay = arr
        self._has_overlay = True
        self._overlay_key = key
        self._overlay_pending = True
        self.update()

    def set_ocio_view(
        self,
        config: object | None,
        working_space: str,
        display: str,
        view: str,
    ) -> None:
        if self._gpu_dead:
            return
        key = (working_space or "", display or "", view or "")
        same = config is self._ocio_cfg and key == self._view_key and self._shader_desc is not None
        self._ocio_cfg = config
        self._working_space, self._display, self._view = key
        self._view_key = key
        if same:
            return
        self._rebuild_ocio_processor()
        self.update()

    def set_exposure_stops(self, stops: float) -> None:
        stops = float(stops)
        if abs(stops - self._exposure_stops) < 1e-6:
            return
        self._exposure_stops = stops
        self._push_dynamic_props()
        self.update()

    def set_gamma(self, gamma: float) -> None:
        """Set Nuke-style viewer γ (``pow(display, 1/γ)`` after OCIO display)."""
        gamma = max(float(gamma), 1e-3)
        if abs(gamma - self._gamma) < 1e-6:
            return
        self._gamma = gamma
        # Gamma is a post-display shader uniform — no OCIO processor rebuild.
        self.update()

    def fit_in_view(self) -> None:
        if self._image_w <= 0 or self._image_h <= 0:
            return
        vw = max(1, self.width())
        vh = max(1, self.height())
        self._zoom = min(vw / self._image_w, vh / self._image_h)
        self._pan_x = 0.0
        self._pan_y = 0.0
        self.update()

    def is_alive(self) -> bool:
        return not self._gpu_dead

    def release_gl(self) -> None:
        """Tear down GL resources before the widget is destroyed.

        Call this from the owning player on shutdown. Relying only on
        ``closeEvent`` is unreliable when the widget is reparented /
        ``deleteLater``'d from a dialog stack (browser Preview path).
        """
        if self._gpu_dead and not self._gl_ready:
            return
        if self._gl_ready:
            try:
                self.makeCurrent()
                self._teardown_gl()
            except Exception:
                log.debug("GL release teardown failed", exc_info=True)
            finally:
                try:
                    self.doneCurrent()
                except Exception:
                    pass
        self._gl_ready = False
        self._gpu_dead = True
        self._image = None
        self._overlay = None
        self._shader_desc = None
        self._exp_prop = None

    # -- GL lifecycle ---------------------------------------------------------

    def initializeGL(self) -> None:
        try:
            # PyOpenGL raises on glGetError by default; a sticky error from Qt's
            # context setup must not abort the whole GPU path (that forces the
            # slow CPU fallback and looks like "no improvement").
            import OpenGL

            OpenGL.ERROR_CHECKING = False
            OpenGL.ERROR_LOGGING = False
            from OpenGL import GL

            self._gl_ready = True
            # Drain any pre-existing error from context creation.
            while GL.glGetError() != GL.GL_NO_ERROR:
                pass
            try:
                GL.glDisable(GL.GL_DEPTH_TEST)
                GL.glDisable(GL.GL_CULL_FACE)
                GL.glDisable(GL.GL_BLEND)
            except Exception:
                pass
            GL.glClearColor(0.196, 0.196, 0.196, 1.0)

            self._image_tex = int(GL.glGenTextures(1))
            GL.glBindTexture(GL.GL_TEXTURE_2D, self._image_tex)
            # Nearest = raw pixel grid when zooming (Nuke-style; no bilinear blur).
            for p, v in (
                (GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST),
                (GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST),
                (GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE),
                (GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE),
            ):
                GL.glTexParameteri(GL.GL_TEXTURE_2D, p, v)
            z = np.zeros((1, 1, 3), dtype=np.float32)
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGB32F, 1, 1, 0, GL.GL_RGB, GL.GL_FLOAT, z)
            self._tex_w = self._tex_h = 1

            self._overlay_tex = int(GL.glGenTextures(1))
            GL.glBindTexture(GL.GL_TEXTURE_2D, self._overlay_tex)
            for p, v in (
                (GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST),
                (GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST),
                (GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE),
                (GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE),
            ):
                GL.glTexParameteri(GL.GL_TEXTURE_2D, p, v)
            z4 = np.zeros((1, 1, 4), dtype=np.float32)
            GL.glTexImage2D(
                GL.GL_TEXTURE_2D, 0, GL.GL_RGBA32F, 1, 1, 0, GL.GL_RGBA, GL.GL_FLOAT, z4
            )

            verts = np.array(
                [
                    0.0,
                    0.0,
                    0.0,
                    1.0,
                    1.0,
                    0.0,
                    1.0,
                    1.0,
                    1.0,
                    1.0,
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    1.0,
                    1.0,
                    1.0,
                    1.0,
                    0.0,
                    0.0,
                    1.0,
                    0.0,
                    0.0,
                ],
                dtype=np.float32,
            )
            self._vao = int(GL.glGenVertexArrays(1))
            self._vbo = int(GL.glGenBuffers(1))
            GL.glBindVertexArray(self._vao)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbo)
            GL.glBufferData(GL.GL_ARRAY_BUFFER, verts.nbytes, verts, GL.GL_STATIC_DRAW)
            stride = 16
            GL.glEnableVertexAttribArray(0)
            GL.glVertexAttribPointer(0, 2, GL.GL_FLOAT, GL.GL_FALSE, stride, ctypes.c_void_p(0))
            GL.glEnableVertexAttribArray(1)
            GL.glVertexAttribPointer(1, 2, GL.GL_FLOAT, GL.GL_FALSE, stride, ctypes.c_void_p(8))
            GL.glBindVertexArray(0)

            if not self._build_program(passthrough=True):
                self._fail("Failed to build passthrough GL program")
                return
            if self._image is not None:
                self._pending_upload = True
            if self._overlay is not None:
                self._overlay_pending = True
            if self._view_key is not None:
                self._rebuild_ocio_processor()
        except Exception as e:
            log.exception("initializeGL failed")
            self._fail(str(e))

    def resizeGL(self, w: int, h: int) -> None:
        if self._gpu_dead:
            return
        try:
            from OpenGL import GL

            GL.glViewport(0, 0, max(1, w), max(1, h))
        except Exception:
            pass

    def paintGL(self) -> None:
        if self._gpu_dead or not self._gl_ready:
            return
        try:
            from OpenGL import GL

            while GL.glGetError() != GL.GL_NO_ERROR:
                pass
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)

            if self._pending_upload and self._image is not None:
                self._upload_image_tex()
                self._pending_upload = False
            if self._overlay_pending:
                self._upload_overlay_tex()
                self._overlay_pending = False

            if self._program == 0 or self._image is None:
                return

            GL.glUseProgram(self._program)
            self._bind_ocio_textures()
            self._update_ocio_uniforms()

            if self._mvp_loc >= 0:
                GL.glUniformMatrix4fv(self._mvp_loc, 1, GL.GL_TRUE, self._mvp_matrix())

            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self._image_tex)
            if self._image_tex_loc >= 0:
                GL.glUniform1i(self._image_tex_loc, 0)

            GL.glActiveTexture(GL.GL_TEXTURE1)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self._overlay_tex)
            if self._overlay_tex_loc >= 0:
                GL.glUniform1i(self._overlay_tex_loc, 1)
            if self._has_overlay_loc >= 0:
                GL.glUniform1i(self._has_overlay_loc, 1 if self._has_overlay else 0)
            if self._viewer_gamma_loc >= 0:
                GL.glUniform1f(self._viewer_gamma_loc, float(self._gamma))

            GL.glBindVertexArray(self._vao)
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, 6)
            GL.glBindVertexArray(0)
            GL.glUseProgram(0)
        except Exception:
            log.exception("paintGL failed")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        # Explicit cleanup with makeCurrent (Qt docs: do not rely on deleteLater
        # for OpenGL resource teardown).
        self.release_gl()
        super().closeEvent(event)

    def _fail(self, reason: str) -> None:
        if self._gpu_dead:
            return
        self._gpu_dead = True
        log.error("GPU OCIO disabled: %s", reason)
        try:
            self.gpu_failed.emit(reason)
        except Exception:
            pass

    def _teardown_gl(self) -> None:
        from OpenGL import GL

        self._delete_ocio_textures()
        texs = [t for t in (self._image_tex, self._overlay_tex) if t]
        if texs:
            GL.glDeleteTextures(texs)
        self._image_tex = self._overlay_tex = 0
        if self._program:
            GL.glDeleteProgram(self._program)
            self._program = 0
        if self._vert_shader:
            GL.glDeleteShader(self._vert_shader)
            self._vert_shader = 0
        if self._vbo:
            GL.glDeleteBuffers(1, [self._vbo])
            self._vbo = 0
        if self._vao:
            GL.glDeleteVertexArrays(1, [self._vao])
            self._vao = 0

    # -- Interaction ----------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        btn = event.button()
        if btn in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self._panning = True
            self._last_pos = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if btn == Qt.MouseButton.RightButton:
            self._zooming = True
            self._last_pos = event.position()
            self._zoom_anchor = event.position()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        if self._panning and self._last_pos is not None:
            d = pos - self._last_pos
            self._last_pos = pos
            self._pan_x += d.x() / max(self._zoom, 1e-6)
            self._pan_y -= d.y() / max(self._zoom, 1e-6)
            self.update()
            event.accept()
            return
        if self._zooming and self._last_pos is not None:
            dx = pos.x() - self._last_pos.x()
            self._last_pos = pos
            self._zoom_at(1.02 ** (dx * 0.5), self._zoom_anchor or pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        btn = event.button()
        if btn in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton) and self._panning:
            self._panning = False
            self._last_pos = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        if btn == Qt.MouseButton.RightButton and self._zooming:
            self._zooming = False
            self._last_pos = None
            self._zoom_anchor = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            return
        self._zoom_at(1.02 ** (delta / 4.0), event.position())
        event.accept()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_F and not event.modifiers():
            self.fit_in_view()
            event.accept()
            return
        super().keyPressEvent(event)

    def _zoom_at(self, factor: float, view_pos: QPointF) -> None:
        old = self._zoom
        new = max(ZOOM_MIN, min(ZOOM_MAX, old * factor))
        if abs(new - old) < 1e-9:
            return
        vw = max(1.0, float(self.width()))
        vh = max(1.0, float(self.height()))
        ix = (view_pos.x() - vw * 0.5) / old - self._pan_x
        iy = (vh * 0.5 - view_pos.y()) / old - self._pan_y
        self._zoom = new
        self._pan_x = (view_pos.x() - vw * 0.5) / new - ix
        self._pan_y = (vh * 0.5 - view_pos.y()) / new - iy
        self.update()

    # -- Texture / program ----------------------------------------------------

    def _upload_image_tex(self) -> None:
        from OpenGL import GL

        if self._image is None:
            return
        h, w = self._image.shape[:2]
        if self._image.dtype == np.float16:
            internal, gl_type = GL.GL_RGB16F, GL.GL_HALF_FLOAT
        else:
            internal, gl_type = GL.GL_RGB32F, GL.GL_FLOAT
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._image_tex)
        if w != self._tex_w or h != self._tex_h:
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, internal, w, h, 0, GL.GL_RGB, gl_type, self._image)
            self._tex_w, self._tex_h = w, h
        else:
            GL.glTexSubImage2D(GL.GL_TEXTURE_2D, 0, 0, 0, w, h, GL.GL_RGB, gl_type, self._image)

    def _upload_overlay_tex(self) -> None:
        from OpenGL import GL

        GL.glBindTexture(GL.GL_TEXTURE_2D, self._overlay_tex)
        if self._overlay is None or not self._has_overlay:
            z4 = np.zeros((1, 1, 4), dtype=np.float32)
            GL.glTexImage2D(
                GL.GL_TEXTURE_2D, 0, GL.GL_RGBA32F, 1, 1, 0, GL.GL_RGBA, GL.GL_FLOAT, z4
            )
            self._overlay_tex_w = self._overlay_tex_h = 1
            return
        h, w = int(self._overlay.shape[0]), int(self._overlay.shape[1])
        # Always reallocate when size differs from the *GPU* texture storage
        # (not the last content dims we already assigned in set_overlay_rgba).
        if w != self._overlay_tex_w or h != self._overlay_tex_h:
            GL.glTexImage2D(
                GL.GL_TEXTURE_2D,
                0,
                GL.GL_RGBA32F,
                w,
                h,
                0,
                GL.GL_RGBA,
                GL.GL_FLOAT,
                self._overlay,
            )
            self._overlay_tex_w, self._overlay_tex_h = w, h
        else:
            GL.glTexSubImage2D(
                GL.GL_TEXTURE_2D,
                0,
                0,
                0,
                w,
                h,
                GL.GL_RGBA,
                GL.GL_FLOAT,
                self._overlay,
            )

    def _mvp_matrix(self) -> np.ndarray:
        vw = max(1.0, float(self.width()))
        vh = max(1.0, float(self.height()))
        z = max(self._zoom, 1e-6)
        half_w = vw / (2.0 * z)
        half_h = vh / (2.0 * z)
        cx = self._image_w * 0.5 - self._pan_x
        cy = self._image_h * 0.5 - self._pan_y
        left, right = cx - half_w, cx + half_w
        bottom, top = cy - half_h, cy + half_h
        sx = float(self._image_w)
        sy = float(self._image_h)
        a = 2.0 * sx / max(right - left, 1e-12)
        c = 2.0 * sy / max(top - bottom, 1e-12)
        tx = -1.0 - 2.0 * left / max(right - left, 1e-12)
        ty = -1.0 - 2.0 * bottom / max(top - bottom, 1e-12)
        return np.array(
            [
                [a, 0.0, 0.0, tx],
                [0.0, c, 0.0, ty],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            dtype=np.float32,
        )

    def _compile_shader(self, src: str, shader_type: int) -> int:
        from OpenGL import GL

        sh = int(GL.glCreateShader(shader_type))
        GL.glShaderSource(sh, src)
        GL.glCompileShader(sh)
        if not GL.glGetShaderiv(sh, GL.GL_COMPILE_STATUS):
            info = GL.glGetShaderInfoLog(sh)
            if isinstance(info, bytes):
                info = info.decode("utf-8", "replace")
            log.error("Shader compile failed: %s", info)
            GL.glDeleteShader(sh)
            return 0
        return sh

    def _build_program(self, *, passthrough: bool = False) -> bool:
        from OpenGL import GL

        if not self._gl_ready or self._gpu_dead:
            return False

        shader_cache_id = "passthrough" if passthrough else ""
        if self._shader_desc is not None and not passthrough:
            try:
                shader_cache_id = str(self._shader_desc.getCacheID())
            except Exception:
                shader_cache_id = "ocio"
            if shader_cache_id and shader_cache_id == self._shader_cache_id and self._program:
                return True

        if not self._vert_shader:
            self._vert_shader = self._compile_shader(_GLSL_VERT, GL.GL_VERTEX_SHADER)
        if not self._vert_shader:
            return False

        if self._shader_desc is not None and not passthrough:
            try:
                ocio_src = _rewrite_sampler1d_to_2d(self._shader_desc.getShaderText())
                frag_src = _GLSL_FRAG_OCIO_FMT.replace("@@OCIO_SRC@@", ocio_src)
            except Exception:
                log.exception("Failed to build OCIO fragment source")
                frag_src = _GLSL_FRAG_PASSTHROUGH
                shader_cache_id = "passthrough"
        else:
            frag_src = _GLSL_FRAG_PASSTHROUGH

        frag = self._compile_shader(frag_src, GL.GL_FRAGMENT_SHADER)
        if not frag:
            return False

        prog = int(GL.glCreateProgram())
        GL.glAttachShader(prog, self._vert_shader)
        GL.glAttachShader(prog, frag)
        GL.glBindAttribLocation(prog, 0, "in_position")
        GL.glBindAttribLocation(prog, 1, "in_texCoord")
        GL.glLinkProgram(prog)
        GL.glDeleteShader(frag)
        if not GL.glGetProgramiv(prog, GL.GL_LINK_STATUS):
            info = GL.glGetProgramInfoLog(prog)
            if isinstance(info, bytes):
                info = info.decode("utf-8", "replace")
            log.error("Shader link failed: %s", info)
            GL.glDeleteProgram(prog)
            return False

        if self._program:
            GL.glDeleteProgram(self._program)
        self._program = prog
        self._shader_cache_id = shader_cache_id
        self._mvp_loc = int(GL.glGetUniformLocation(prog, "mvpMat"))
        self._image_tex_loc = int(GL.glGetUniformLocation(prog, "imageTex"))
        self._overlay_tex_loc = int(GL.glGetUniformLocation(prog, "overlayTex"))
        self._has_overlay_loc = int(GL.glGetUniformLocation(prog, "hasOverlay"))
        self._viewer_gamma_loc = int(GL.glGetUniformLocation(prog, "viewerGamma"))
        self._ocio_uniform_ids.clear()
        return True

    # -- OCIO GPU -------------------------------------------------------------

    def _rebuild_ocio_processor(self) -> None:
        import PyOpenColorIO as OCIO

        self._shader_desc = None
        self._proc_cache_id = ""
        self._exp_prop = None
        if self._gpu_dead or not self._gl_ready:
            return

        cfg = self._ocio_cfg
        if cfg is None or not self._working_space or not self._display or not self._view:
            self.makeCurrent()
            self._delete_ocio_textures()
            self._build_program(passthrough=True)
            return

        try:
            group = OCIO.GroupTransform()
            # Gain only (as exposure stops). Nuke gamma is post-display in GLSL.
            ec = OCIO.ExposureContrastTransform()
            ec.setStyle(OCIO.EXPOSURE_CONTRAST_LINEAR)
            ec.setExposure(self._exposure_stops)
            ec.setGamma(1.0)
            ec.setPivot(0.18)
            ec.makeExposureDynamic()
            group.appendTransform(ec)

            dvt = OCIO.DisplayViewTransform()
            dvt.setSrc(self._working_space)
            dvt.setDisplay(self._display)
            dvt.setView(self._view)
            group.appendTransform(dvt)

            proc = cfg.getProcessor(group)
            cache_id = str(proc.getCacheID())
            if cache_id == self._proc_cache_id and self._shader_desc is not None:
                self._push_dynamic_props()
                return

            gpu = proc.getDefaultGPUProcessor()
            desc = OCIO.GpuShaderDesc.CreateShaderDesc(language=OCIO.GPU_LANGUAGE_GLSL_4_0)
            gpu.extractGpuShaderInfo(desc)
            self._shader_desc = desc
            self._proc_cache_id = cache_id
            if desc.hasDynamicProperty(OCIO.DYNAMIC_PROPERTY_EXPOSURE):
                self._exp_prop = desc.getDynamicProperty(OCIO.DYNAMIC_PROPERTY_EXPOSURE)

            self.makeCurrent()
            self._allocate_ocio_textures()
            if not self._build_program(passthrough=False):
                log.error("OCIO GPU shader failed — raw working-space plate")
                self._shader_desc = None
                self._delete_ocio_textures()
                self._build_program(passthrough=True)
            self._push_dynamic_props()
        except Exception as e:
            log.exception("OCIO GPU processor failed")
            try:
                self.makeCurrent()
                self._shader_desc = None
                self._delete_ocio_textures()
                self._build_program(passthrough=True)
            except Exception:
                self._fail(str(e))

    def _push_dynamic_props(self) -> None:
        try:
            if self._exp_prop is not None:
                self._exp_prop.setDouble(self._exposure_stops)
        except Exception:
            log.debug("Failed to push EC dynamic properties", exc_info=True)

    def _set_tex_params(self, tex_type: int, nearest: bool) -> None:
        from OpenGL import GL

        filt = GL.GL_NEAREST if nearest else GL.GL_LINEAR
        GL.glTexParameteri(tex_type, GL.GL_TEXTURE_MIN_FILTER, filt)
        GL.glTexParameteri(tex_type, GL.GL_TEXTURE_MAG_FILTER, filt)
        GL.glTexParameteri(tex_type, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
        GL.glTexParameteri(tex_type, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
        if tex_type == GL.GL_TEXTURE_3D:
            GL.glTexParameteri(tex_type, GL.GL_TEXTURE_WRAP_R, GL.GL_CLAMP_TO_EDGE)

    def _delete_ocio_textures(self) -> None:
        from OpenGL import GL

        if self._ocio_tex_ids:
            try:
                GL.glDeleteTextures([t[0] for t in self._ocio_tex_ids])
            except Exception:
                pass
        self._ocio_tex_ids.clear()

    def _allocate_ocio_textures(self) -> None:
        import PyOpenColorIO as OCIO
        from OpenGL import GL

        if self._shader_desc is None:
            return
        self._delete_ocio_textures()
        tex_index = self._ocio_tex_start

        for tex_info in self._shader_desc.get3DTextures():
            tex_data = np.ascontiguousarray(tex_info.getValues(), dtype=np.float32)
            tex = int(GL.glGenTextures(1))
            GL.glActiveTexture(GL.GL_TEXTURE0 + tex_index)
            GL.glBindTexture(GL.GL_TEXTURE_3D, tex)
            self._set_tex_params(GL.GL_TEXTURE_3D, tex_info.interpolation == OCIO.INTERP_NEAREST)
            edge = int(tex_info.edgeLen)
            GL.glTexImage3D(
                GL.GL_TEXTURE_3D,
                0,
                GL.GL_RGB32F,
                edge,
                edge,
                edge,
                0,
                GL.GL_RGB,
                GL.GL_FLOAT,
                tex_data,
            )
            self._ocio_tex_ids.append((tex, tex_info.samplerName, GL.GL_TEXTURE_3D, tex_index))
            tex_index += 1

        for tex_info in self._shader_desc.getTextures():
            tex_data = np.ascontiguousarray(tex_info.getValues(), dtype=np.float32)
            channels = 1 if tex_info.channel == self._shader_desc.TEXTURE_RED_CHANNEL else 3
            internal = GL.GL_R32F if channels == 1 else GL.GL_RGB32F
            fmt = GL.GL_RED if channels == 1 else GL.GL_RGB
            nearest = tex_info.interpolation == OCIO.INTERP_NEAREST
            tex = int(GL.glGenTextures(1))
            GL.glActiveTexture(GL.GL_TEXTURE0 + tex_index)
            width = int(tex_info.width)
            height = (
                int(tex_info.height) if tex_info.dimensions == self._shader_desc.TEXTURE_2D else 1
            )
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
            self._set_tex_params(GL.GL_TEXTURE_2D, nearest)
            GL.glTexImage2D(
                GL.GL_TEXTURE_2D, 0, internal, width, height, 0, fmt, GL.GL_FLOAT, tex_data
            )
            self._ocio_tex_ids.append((tex, tex_info.samplerName, GL.GL_TEXTURE_2D, tex_index))
            tex_index += 1

    def _bind_ocio_textures(self) -> None:
        from OpenGL import GL

        for tex, sampler, tex_type, tex_index in self._ocio_tex_ids:
            GL.glActiveTexture(GL.GL_TEXTURE0 + tex_index)
            GL.glBindTexture(tex_type, tex)
            if self._program:
                loc = GL.glGetUniformLocation(self._program, sampler)
                if loc >= 0:
                    GL.glUniform1i(loc, tex_index)

    def _update_ocio_uniforms(self) -> None:
        import PyOpenColorIO as OCIO
        from OpenGL import GL

        if self._shader_desc is None or not self._program:
            return
        for name, uniform_data in self._shader_desc.getUniforms():
            if name not in self._ocio_uniform_ids:
                self._ocio_uniform_ids[name] = int(GL.glGetUniformLocation(self._program, name))
            uid = self._ocio_uniform_ids[name]
            if uid < 0:
                continue
            if uniform_data.type == OCIO.UNIFORM_DOUBLE:
                GL.glUniform1f(uid, float(uniform_data.getDouble()))
