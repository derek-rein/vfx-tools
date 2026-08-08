from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import PyOpenColorIO as OCIO_mod
from PySide6.QtCore import QSettings, Qt, QThread, QUrl
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QFontDatabase,
    QGuiApplication,
    QIcon,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..core.constants import APP_NAME, APP_ORG, APP_VERSION, GITHUB_REPO
from ..core.ocio_utils import color_space_families, config_source_info
from ..services.presets import delete_preset, list_presets, load_preset, save_preset
from ..services.worker import ConvertWorker
from .preferences import PreferencesDialog, open_video_with_player, reveal_in_file_manager
from .size_grip import SizeGrip
from .widgets import ConvertTab, OcioConfigPanel


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About EXR Converter")
        self.setFixedSize(480, 440)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Header only outside the scroll area.
        title = QLabel(f"<h2>EXR Converter</h2><p>Version {APP_VERSION}</p>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        try:
            import OpenImageIO as oiio

            oiio_ver = getattr(oiio, "VERSION_STRING", None) or str(oiio.openimageio_version())
        except Exception:
            oiio_ver = "?"

        deps = (
            f"Python {sys.version.split()[0]} · "
            f"PySide6 {__import__('PySide6').__version__}<br>"
            f"OpenColorIO {OCIO_mod.GetVersion()} · "
            f"OpenImageIO {oiio_ver}"
        )

        body = QTextBrowser()
        body.setOpenExternalLinks(True)
        body.setHtml(
            f"<p style='text-align:center;'>{deps}</p>"
            "<hr>"
            "<p>by <b>Derek Rein</b></p>"
            '<p><a href="https://derekvfx.ca">derekvfx.ca</a> &nbsp;|&nbsp; '
            '<a href="https://ocio.cc">ocio.cc</a></p>'
            "<hr>"
            "<p><b>Bundled OCIO config:</b> ACES Studio Config v4 (ACES 2.0)<br>"
            "Sourced from <a href='https://github.com/AcademySoftwareFoundation/OpenColorIO-Config-ACES/releases'>"
            "AcademySoftwareFoundation/OpenColorIO-Config-ACES</a> (BSD-3-Clause).<br>"
            "Contains official camera IDTs for <b>Apple Log</b> (iPhone cinematic/ProRes Log), "
            "ARRI, RED, Sony, Canon, DJI and many more.</p>"
            f"<p style='font-size:10px;'>"
            f"MIT License &copy; {datetime.now().year} Derek Rein<br><br>"
            "Permission is hereby granted, free of charge, to any person obtaining "
            "a copy of this software and associated documentation files, to deal in "
            "the Software without restriction, including without limitation the "
            "rights to use, copy, modify, merge, publish, distribute, sublicense, "
            "and/or sell copies of the Software, subject to the above copyright "
            "notice and this permission notice being included in all copies.</p>"
        )
        body.setReadOnly(True)
        layout.addWidget(body, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("EXR Converter")
        # macOS Dock icon comes from the bundle .icns; a runtime PNG override
        # replaces the system tile and shows sharp square corners while running.
        if sys.platform != "darwin":
            self.setWindowIcon(QIcon(":/icon.png"))
        self.setMinimumSize(700, 640)
        self.setAcceptDrops(True)
        self._settings = QSettings(APP_ORG, APP_NAME)
        self._thread: QThread | None = None
        self._worker: ConvertWorker | None = None
        self._ocio_cfg = None
        # "convert" | None — keeps progress UI from fighting itself.
        self._busy: str | None = None

        self._build_menu_bar()

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Vertical)
        root.addWidget(splitter)

        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(8, 8, 8, 4)
        top_layout.setSpacing(6)

        self._ocio_panel = OcioConfigPanel(self._settings)
        top_layout.addWidget(self._ocio_panel)

        self._tabs = QTabWidget()
        self._v2e_tab = ConvertTab("video2exr", self._settings)
        self._e2v_tab = ConvertTab("exr2video", self._settings)
        self._tabs.addTab(self._v2e_tab, "Video \u2192 EXR")
        self._tabs.addTab(self._e2v_tab, "EXR \u2192 Video")
        saved_tab = int(self._settings.value("ui/tab", 0))
        if 0 <= saved_tab < self._tabs.count():
            self._tabs.setCurrentIndex(saved_tab)
        top_layout.addWidget(self._tabs, 1)

        prog_row = QHBoxLayout()
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        self._progress.setFormat("%p%")
        self._progress.setObjectName("convertProgress")
        prog_row.addWidget(self._progress, 1)
        self._go = QPushButton("  Convert  ")
        self._go.setObjectName("convertBtn")
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setEnabled(False)
        prog_row.addWidget(self._go)
        prog_row.addWidget(self._cancel_btn)
        top_layout.addLayout(prog_row)

        # Post-convert actions (persisted).
        after_row = QHBoxLayout()
        after_row.setContentsMargins(0, 0, 0, 0)
        after_row.setSpacing(14)
        after_lbl = QLabel("When done:")
        after_lbl.setStyleSheet("color: #888;")
        after_row.addWidget(after_lbl)
        self._copy_path_cb = QCheckBox("Copy path")
        self._copy_path_cb.setToolTip(
            "Copy the output path to the clipboard (Nuke-style #### pattern for EXR sequences)."
        )
        # Default ON for new installs; persisted after first toggle.
        self._copy_path_cb.setChecked(self._settings.value("ui/copy_path_after", True, type=bool))
        self._copy_path_cb.toggled.connect(
            lambda v: self._settings.setValue("ui/copy_path_after", v)
        )
        after_row.addWidget(self._copy_path_cb)
        self._open_after_cb = QCheckBox("Open result")
        self._open_after_cb.setToolTip(
            "Open the finished video in your preferred player "
            "(File → Preferences). For EXR output this has no effect — use Show in folder."
        )
        self._open_after_cb.setChecked(self._settings.value("ui/open_after", False, type=bool))
        self._open_after_cb.toggled.connect(lambda v: self._settings.setValue("ui/open_after", v))
        after_row.addWidget(self._open_after_cb)
        self._show_folder_cb = QCheckBox("Show in folder")
        self._show_folder_cb.setToolTip(
            "Reveal the output file (or EXR sequence folder) in the system file manager."
        )
        self._show_folder_cb.setChecked(
            self._settings.value("ui/show_folder_after", False, type=bool)
        )
        self._show_folder_cb.toggled.connect(
            lambda v: self._settings.setValue("ui/show_folder_after", v)
        )
        after_row.addWidget(self._show_folder_cb)
        after_row.addStretch()
        top_layout.addLayout(after_row)

        splitter.addWidget(top)

        log_container = QWidget()
        log_layout = QVBoxLayout(log_container)
        log_layout.setContentsMargins(8, 4, 8, 4)
        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("Log"))
        self._clear_log = QPushButton("Clear")
        self._clear_log.setObjectName("clearBtn")
        log_header.addStretch()
        log_header.addWidget(self._clear_log)
        log_layout.addLayout(log_header)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(5000)
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        mono.setPointSize(11)
        self._log.setFont(mono)
        self._log.setObjectName("logPane")
        log_layout.addWidget(self._log, 1)
        splitter.addWidget(log_container)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        self._statusbar = QStatusBar()
        # Native grip is invisible under the QSS stylesheet — use a painted one.
        self._statusbar.setSizeGripEnabled(False)
        self._statusbar.addPermanentWidget(SizeGrip(self._statusbar))
        self.setStatusBar(self._statusbar)

        self._ocio_panel.config_changed.connect(self._reload_ocio)
        self._go.clicked.connect(self._start)
        self._cancel_btn.clicked.connect(self._cancel_run)
        self._clear_log.clicked.connect(self._log.clear)
        self._tabs.currentChanged.connect(lambda i: self._settings.setValue("ui/tab", i))
        self._tabs.currentChanged.connect(lambda _: self._update_go_state())
        self._v2e_tab.log_message.connect(self._append_log)
        self._e2v_tab.log_message.connect(self._append_log)
        self._v2e_tab.readiness_changed.connect(lambda _: self._update_go_state())
        self._e2v_tab.readiness_changed.connect(lambda _: self._update_go_state())
        self._go.setEnabled(self._active_tab().is_ready())

        self._reload_ocio()

        geom = self._settings.value("ui/geometry")
        if geom:
            self.restoreGeometry(geom)

    def apply_startup(
        self,
        open_path: str | None = None,
        ocio_path: str | None = None,
        mode: str | None = None,
    ) -> None:
        """Apply GUI launch options (CLI ``--open`` / ``--gui-ocio`` / ``--mode``).

        Used by the bare ``main.py`` entry and by the Nuke menu integration.
        """
        if ocio_path:
            ok = self._ocio_panel.set_custom_config_file(ocio_path)
            if ok:
                self._append_log(f"OCIO config (launch): {ocio_path}")
                self._reload_ocio()
            else:
                self._append_log(f"OCIO config not found (launch ignored): {ocio_path}")

        open_path = (open_path or "").strip()
        mode_norm = (mode or "auto").strip().lower()
        if mode_norm in ("video2exr", "v2e", "video"):
            self._tabs.setCurrentIndex(0)
        elif mode_norm in ("exr2video", "e2v", "exr"):
            self._tabs.setCurrentIndex(1)
        elif open_path:
            # Prefer extension over filesystem existence (Nuke may pass paths
            # that are not yet fully visible, or sequence tokens).
            p = Path(open_path.split()[0] if open_path else "")
            name = p.name.lower()
            if p.suffix.lower() in self._VIDEO_EXTS or any(
                name.endswith(ext) for ext in self._VIDEO_EXTS
            ):
                self._tabs.setCurrentIndex(0)
            else:
                # Directory, EXR frame, or sequence pattern → EXR tab
                self._tabs.setCurrentIndex(1)

        if open_path:
            # Prevent deferred QSettings restore from overwriting --open / Nuke.
            self._v2e_tab.suppress_saved_input_restore()
            self._e2v_tab.suppress_saved_input_restore()
            tab = self._active_tab()
            # Async probe so large MXFs / sequences don't freeze launch.
            tab.set_input_async(open_path)
            self._append_log(f"Opened (launch): {open_path}")

    # -- Menu bar --

    def _build_menu_bar(self) -> None:
        mb = self.menuBar()
        mb.setNativeMenuBar(False)

        file_menu = mb.addMenu("&File")
        prefs_action = QAction("&Preferences\u2026", self)
        prefs_action.setShortcut(QKeySequence.StandardKey.Preferences)
        prefs_action.triggered.connect(self._open_preferences)
        file_menu.addAction(prefs_action)
        file_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        self._presets_menu = mb.addMenu("&Presets")
        self._presets_menu.aboutToShow.connect(self._populate_presets_menu)

        slate_menu = mb.addMenu("&Slate")
        edit_slate_action = QAction("Edit Slate && Overlays\u2026", self)
        edit_slate_action.triggered.connect(self._open_slate_dialog)
        slate_menu.addAction(edit_slate_action)

        help_menu = mb.addMenu("&Help")

        update_action = QAction("Check for &Updates\u2026", self)
        update_action.triggered.connect(self._check_for_updates)
        help_menu.addAction(update_action)

        about_action = QAction("&About EXR Converter", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

        help_menu.addSeparator()

        version_action = QAction(f"Version {APP_VERSION}", self)
        version_action.setEnabled(False)
        help_menu.addAction(version_action)

    def _show_about(self) -> None:
        dlg = AboutDialog(self)
        dlg.exec()

    def _open_preferences(self) -> None:
        dlg = PreferencesDialog(self._settings, self)
        dlg.exec()

    # -- Updates --

    def _is_convert_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def _check_for_updates(self) -> None:
        """Open the latest GitHub release page in the system browser."""
        url = QUrl(f"https://github.com/{GITHUB_REPO}/releases/latest")
        QDesktopServices.openUrl(url)

    # -- Slate menu --

    def _open_slate_dialog(self) -> None:
        self._active_tab()._open_slate_dialog()

    # -- Presets --

    def _populate_presets_menu(self) -> None:
        m = self._presets_menu
        m.clear()

        reset_action = QAction("Reset to Defaults", self)
        reset_action.triggered.connect(self._reset_to_defaults)
        m.addAction(reset_action)
        m.addSeparator()

        save_action = QAction("Save Preset As\u2026", self)
        save_action.triggered.connect(self._save_preset)
        m.addAction(save_action)
        m.addSeparator()

        names = list_presets()
        if names:
            for name in names:
                action = QAction(name, self)
                action.triggered.connect(lambda _checked, n=name: self._load_preset(n))
                m.addAction(action)
            m.addSeparator()
            delete_sub = m.addMenu("Delete Preset")
            for name in names:
                action = QAction(name, self)
                action.triggered.connect(lambda _checked, n=name: self._delete_preset(n))
                delete_sub.addAction(action)
        else:
            no_presets = QAction("(no presets saved)", self)
            no_presets.setEnabled(False)
            m.addAction(no_presets)

    def _save_preset(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "Save Preset", "Preset name:")
        if ok and name.strip():
            try:
                state = self.snapshot_state()
                save_preset(name.strip(), state)
                self._append_log(f"Preset saved: {name.strip()}")
            except ValueError as e:
                QMessageBox.warning(self, "Save Preset", str(e))

    def _load_preset(self, name: str) -> None:
        try:
            data = load_preset(name)
        except Exception as e:
            QMessageBox.warning(self, "Load Preset", f"Failed to load preset: {e}")
            return
        self.restore_state(data)
        self._append_log(f"Preset loaded: {name}")

    def _delete_preset(self, name: str) -> None:
        delete_preset(name)
        self._append_log(f"Preset deleted: {name}")

    def _reset_to_defaults(self) -> None:
        from ..core.constants import (
            DEFAULT_EXR_COMPRESSION,
            DEFAULT_FRAME_PADDING,
            DEFAULT_SCALE,
            DEFAULT_SRC_E2V,
            DEFAULT_SRC_V2E,
            DEFAULT_START_FRAME,
            DEFAULT_VIDEO_CODEC,
            OCIO_SOURCE_ENV,
        )

        defaults = {
            "tab": 0,
            "ocio_source": OCIO_SOURCE_ENV,
            "ocio_file": "",
            "v2e_src_space": DEFAULT_SRC_V2E,
            "v2e_dst_space": "ACEScg",
            "v2e_compression": DEFAULT_EXR_COMPRESSION,
            "v2e_scale": DEFAULT_SCALE,
            "v2e_padding": DEFAULT_FRAME_PADDING,
            "v2e_start_frame": DEFAULT_START_FRAME,
            "e2v_src_space": DEFAULT_SRC_E2V,
            "e2v_dst_space": "Output - Rec.709",
            "e2v_fps": 24.0,
            "e2v_scale": DEFAULT_SCALE,
            "e2v_codec": DEFAULT_VIDEO_CODEC,
        }
        self.restore_state(defaults)
        self._append_log("Reset all parameters to defaults")

    # -- OCIO --

    def _reload_ocio(self) -> None:
        if self._is_convert_running():
            self._append_log("OCIO reload deferred — conversion in progress")
            self._statusbar.showMessage("Cannot reload OCIO during conversion", 4000)
            return
        cfg = self._ocio_panel.load_config()
        if cfg is None:
            self._ocio_cfg = None
            self._statusbar.showMessage("OCIO config error", 5000)
            return
        self._ocio_cfg = cfg
        families = color_space_families(cfg)
        n_spaces = sum(len(v) for v in families.values())
        self._v2e_tab.populate_spaces(families, ocio_cfg=cfg)
        self._e2v_tab.populate_spaces(families, ocio_cfg=cfg)
        self._update_go_state()
        # Surface invalid color spaces after a config switch.
        tab = self._active_tab()
        bad: list[str] = []
        if not tab.src_btn.is_valid():
            bad.append("source")
        if not tab.dst_btn.is_valid():
            bad.append("destination")
        if bad:
            self._statusbar.showMessage(
                f"OCIO loaded — pick a new {' & '.join(bad)} color space",
                6000,
            )
        else:
            self._statusbar.showMessage(f"OCIO: {n_spaces} color spaces loaded", 3000)
        self._append_log(f"OCIO config loaded ({n_spaces} spaces)")

    # -- Log --

    def _append_log(self, msg: str) -> None:
        self._log.appendPlainText(msg)

    # -- Convert --

    def _active_tab(self) -> ConvertTab:
        return self._v2e_tab if self._tabs.currentIndex() == 0 else self._e2v_tab

    def _update_go_state(self) -> None:
        """Enable Convert only when idle and the active tab is ready."""
        busy = self._is_convert_running() or self._busy is not None
        self._go.setEnabled(not busy and self._active_tab().is_ready())

    def _start(self) -> None:
        if self._is_convert_running():
            self._append_log("Conversion already running — ignore start.")
            return
        if self._ocio_cfg is None:
            QMessageBox.warning(self, "OCIO", "No valid OCIO config loaded.")
            return

        tab = self._active_tab()
        mode = "video2exr" if self._tabs.currentIndex() == 0 else "exr2video"

        inp = tab.get_input_path()
        out = tab.get_output_path()
        if not inp or not out:
            QMessageBox.warning(self, "Missing paths", "Set both input and output paths.")
            return
        src = tab.src_btn.current_space()
        dst = tab.dst_btn.current_space()
        if not tab.src_btn.is_valid() or not tab.dst_btn.is_valid() or not src or not dst:
            QMessageBox.warning(
                self,
                "Color spaces",
                "Source and/or destination color space is missing or not in the "
                "current OCIO config. Pick valid spaces before converting.",
            )
            return

        cs, cp = config_source_info(
            self._ocio_panel.current_source_key(),
            self._ocio_panel._file_path,
        )

        frame_range_str = tab.get_frame_range()
        frame_set: set[int] | None = None
        if frame_range_str:
            from ..core.framerange import parse_frame_range

            try:
                frame_set = set(parse_frame_range(frame_range_str))
            except ValueError as e:
                QMessageBox.warning(self, "Frame range", f"Invalid frame range: {e}")
                return
            if not frame_set:
                frame_set = None

        # -- Slate / burn-in / watermark: EXR → video only (never video → EXR) --
        slate_np = None
        overlay_np = None
        slate_overlay_np = None
        overlay_provider = None
        if mode == "exr2video" and tab.slate_enabled():
            slate_data = tab.get_slate_data()
            if slate_data is not None:
                from ..render.slate import render_slate_frame

                sw, sh = self._detect_slate_resolution(mode, inp)
                thumb_b64 = tab.get_slate_thumbnail_b64()
                # If the slate dialog was never opened, still extract a thumb
                # from the known EXR frame list (first / mid / last).
                if not thumb_b64 and inp:
                    try:
                        from .preferences import thumbnail_frame_choice
                        from .slate_widgets import extract_thumbnail_b64

                        thumb_b64 = extract_thumbnail_b64(
                            inp,
                            "exr2video",
                            which=thumbnail_frame_choice(self._settings),
                            ocio_cfg=self._ocio_cfg,
                            src_space=tab.src_btn.current_space() or "",
                        )
                        if thumb_b64 and tab._slate_model is not None:
                            tab._slate_model.set_thumbnail_b64(thumb_b64)
                    except Exception as e:
                        self._append_log(f"Slate thumbnail extract skipped: {e}")
                self._append_log(f"Rendering slate frame ({sw}\u00d7{sh})\u2026")
                try:
                    slate_np = render_slate_frame(slate_data, sw, sh, thumbnail_b64=thumb_b64)
                    if thumb_b64:
                        self._append_log("Slate frame rendered (with thumbnail)")
                    else:
                        self._append_log("Slate frame rendered (no thumbnail)")
                except Exception as e:
                    QMessageBox.warning(self, "Slate Error", f"Failed to render slate: {e}")
                    return

        self._busy = "convert"
        self._go.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFormat("%p%")

        # Burn-in and watermark are stamped on shot frames only; the slate is
        # left clean (matching the live preview). Overlay is sRGB uint8 RGBA —
        # convert.py linearises once and composites in working space.
        if mode == "exr2video":
            overlay_np, slate_overlay_np, overlay_provider = self._build_overlays(
                tab, inp, frame_range_str, frame_set
            )

        if mode == "video2exr":
            # ocio_cfg intentionally omitted — worker rebuilds from config_source/path.
            kwargs = dict(
                video_path=inp,
                output_dir=Path(out),
                src_space=src,
                dst_space=dst,
                compression=tab.get_compression(),
                config_source=cs,
                config_path=cp,
                scale=tab.get_scale(),
                padding=tab.get_padding(),
                start_frame=tab.get_start_frame(),
                frame_set=frame_set,
                exr_opts=tab.get_exr_opts() or None,
            )
        else:
            _codec_key, _codec, _pix = tab.get_video_codec_info()
            kwargs = dict(
                input_spec=inp,
                output_video=Path(out),
                src_space=src,
                dst_space=dst,
                fps=tab.get_fps(),
                config_source=cs,
                config_path=cp,
                scale=tab.get_scale(),
                video_codec=_codec,
                pix_fmt_out=_pix,
                codec_key=_codec_key,
                frame_set=frame_set,
                slate_frame=slate_np,
                burnin_overlay=overlay_np,
                slate_overlay=slate_overlay_np,
                overlay_provider=overlay_provider,
                codec_opts=tab.get_codec_opts() or None,
            )

        out_path = Path(out)
        self._output_folder = str(out_path if out_path.is_dir() else out_path.parent)
        # Paths used by post-convert actions (clipboard / open).
        self._output_mode = mode
        self._output_file = str(out_path)  # video file or EXR directory
        # Prefer the Nuke-style #### pattern shown in the field for sequences.
        display_out = tab.output_path.text().strip()
        self._output_clipboard = display_out or str(out_path)

        self._append_log(f"--- {mode} ---")
        self._thread = QThread(self)
        self._worker = ConvertWorker(mode, kwargs)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.log_message.connect(self._append_log)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.finished_ok.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._worker.cancelled.connect(self._thread.quit)
        worker_ref = self._worker
        thr_ref = self._thread
        self._thread.finished.connect(worker_ref.deleteLater)
        self._thread.finished.connect(thr_ref.deleteLater)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _on_progress(self, cur: int, total: int) -> None:
        if self._busy != "convert":
            return
        if total > 0:
            self._progress.setValue(int(100 * cur / total))
        self._statusbar.showMessage(f"Frame {cur} / {total}")

    def _on_failed(self, msg: str) -> None:
        self._busy = None
        self._progress.setValue(0)
        self._statusbar.showMessage("Conversion failed.")
        QMessageBox.critical(self, "Error", msg)
        self._update_go_state()
        self._cancel_btn.setEnabled(False)

    def _on_cancelled(self) -> None:
        """User hit Cancel — quiet UI, no error dialog."""
        self._busy = None
        self._progress.setValue(0)
        self._statusbar.showMessage("Cancelled.", 4000)
        self._update_go_state()
        self._cancel_btn.setEnabled(False)

    def _on_done(self) -> None:
        self._busy = None
        self._progress.setValue(100)
        self._update_go_state()
        self._cancel_btn.setEnabled(False)

        notes: list[str] = []
        clip = getattr(self, "_output_clipboard", "") or ""
        if self._copy_path_cb.isChecked() and clip:
            QGuiApplication.clipboard().setText(clip)
            notes.append("path copied")
            self._append_log(f"Copied to clipboard: {clip}")

        mode = getattr(self, "_output_mode", "")
        target = getattr(self, "_output_file", "") or ""
        folder = getattr(self, "_output_folder", None)

        if self._open_after_cb.isChecked():
            if mode == "exr2video" and target and Path(target).is_file():
                try:
                    msg = open_video_with_player(target, self._settings)
                    notes.append(msg)
                    self._append_log(f"{msg.capitalize()}: {target}")
                except OSError as e:
                    self._append_log(f"Could not open player: {e}")
            elif mode != "exr2video":
                # Open result is video-oriented; nudge EXR users toward folder.
                self._append_log(
                    "Open result applies to video output — use Show in folder for EXR sequences."
                )

        if self._show_folder_cb.isChecked():
            reveal_target = target if (target and Path(target).exists()) else folder
            if reveal_target:
                try:
                    msg = reveal_in_file_manager(reveal_target)
                    notes.append(msg.split(":")[0] if ":" in msg else msg)
                    self._append_log(msg)
                except OSError as e:
                    self._append_log(f"Could not show in folder: {e}")

        status = "Done."
        if notes:
            status = f"Done — {', '.join(notes)}."
        self._statusbar.showMessage(status, 6000)

    def _cleanup_thread(self) -> None:
        self._worker = None
        self._thread = None
        if self._busy == "convert":
            self._busy = None
        self._cancel_btn.setEnabled(False)
        self._update_go_state()

    def _cancel_run(self) -> None:
        if self._worker:
            self._worker.cancel()
            self._append_log("Cancellation requested\u2026")

    # -- Burn-in / watermark overlays --

    def _build_overlays(self, tab, inp, frame_range_str, frame_set):
        """Prepare the burn-in + watermark overlays for an EXR→video render.

        Returns ``(shot_overlay, slate_overlay, overlay_provider)``:

        - *shot_overlay* — combined burn-in + watermark for shot frames, or
          ``None`` when a per-frame token forces per-frame rendering instead.
        - *slate_overlay* — always ``None``: burn-in and watermark are stamped
          on shot frames only, never on the designed slate frame.
        - *overlay_provider* — ``fn(frame_num) -> uint8 RGBA`` used by the
          pipeline when any field contains a per-frame token (e.g. ``<frame>``);
          ``None`` for the fast, render-once path.

        Token text is expanded here so the renderers only ever see literal
        strings.  Everything captured by the provider closure is plain data so
        it is safe to call from the conversion worker thread.
        """
        import numpy as np

        from ..render import tokens as tok
        from ..render.burnin import render_burnin_overlay
        from ..render.watermark import render_watermark_overlay

        bw, bh = self._detect_slate_resolution("exr2video", inp)

        burnin_on = tab.burnin_enabled()
        raw_fields = (tab.get_effective_burnin_fields(inp) or {}) if burnin_on else {}

        wm_params = tab.get_watermark_params()
        slate_model = tab.slate_model()
        wm_on = bool(wm_params and slate_model is not None and slate_model.watermark_active())

        if not burnin_on and not wm_on:
            return None, None, None

        slate_render = slate_model.slate_data_for_render() if slate_model is not None else {}
        start_f = min(frame_set) if frame_set else None
        end_f = max(frame_set) if frame_set else None
        pad = max(4, len(str(end_f))) if end_f is not None else 4
        base_values = tok.build_values(
            slate_render,
            input_name=Path(inp).name,
            frame_pad=pad,
            start_frame=start_f,
            end_frame=end_f,
            resolution=f"{bw}x{bh}",
            frame_range=frame_range_str or None,
        )

        def _composite(burnin_ov, wm_ov):
            if burnin_ov is None or wm_ov is None:
                return burnin_ov if burnin_ov is not None else wm_ov
            a = wm_ov[..., 3:4].astype(np.float32) / 255.0
            rgb = wm_ov[..., :3].astype(np.float32) * a + burnin_ov[..., :3].astype(np.float32) * (
                1.0 - a
            )
            bg_a = burnin_ov[..., 3:4].astype(np.float32) / 255.0
            out = np.empty_like(burnin_ov)
            out[..., :3] = np.clip(rgb, 0.0, 255.0).astype(np.uint8)
            out[..., 3:4] = np.clip((a + bg_a * (1.0 - a)) * 255.0, 0.0, 255.0).astype(np.uint8)
            return out

        def _render(frame_num):
            values = dict(base_values)
            # On static renders (no <frame> in use) collapse <frame> to empty so
            # it never leaks into the slate watermark as literal text.
            values["frame"] = f"{int(frame_num):0{pad}d}" if frame_num is not None else ""
            burnin_ov = None
            if burnin_on:
                fields = {k: tok.substitute(v, values) for k, v in raw_fields.items()}
                burnin_ov = render_burnin_overlay(bw, bh, fields)
            wm_ov = None
            if wm_on:
                p = dict(wm_params)
                p["text"] = tok.substitute(p.get("text", ""), values)
                wm_ov = render_watermark_overlay(bw, bh, p)
            return _composite(burnin_ov, wm_ov), wm_ov

        per_frame = tok.any_per_frame_token(raw_fields) or (
            wm_on and tok.has_per_frame_token(wm_params.get("text", ""))
        )

        # Render the base overlay once (with <frame> collapsed). Only shot
        # frames get an overlay; the slate is left clean.
        shot_overlay, _ = _render(None)

        if per_frame:
            self._append_log("Per-frame tokens detected — overlays render per frame (1 thread)")
            return None, None, lambda f: _render(f)[0]

        if shot_overlay is not None:
            self._append_log("Burn-in / watermark overlay rendered")
        return shot_overlay, None, None

    # -- Slate colorspace + resolution --

    def _ocio_transform_slate(self, slate, slate_cs: str, dst_space: str):
        """OCIO-transform the slate frame from its native colorspace to *dst_space*.

        The slate is painted with QPainter and is always sRGB.  This converts it
        into whatever the pipeline destination is (e.g. ACEScg for EXR output,
        or Rec.709 for video output).
        """
        import numpy as np
        import PyOpenColorIO as OCIO_mod

        cfg = self._ocio_cfg
        if cfg is None:
            return slate

        from ..core.ocio_utils import resolve_alias

        src_name = resolve_alias(cfg, slate_cs)
        if not src_name:
            for candidate in ("sRGB", "sRGB - Texture", "Utility - sRGB - Texture", "srgb"):
                src_name = resolve_alias(cfg, candidate)
                if src_name:
                    break
        if not src_name:
            self._append_log(
                "Warning: could not find sRGB colorspace in OCIO config, "
                "slate will not be color-managed"
            )
            return slate

        dst_name = resolve_alias(cfg, dst_space) or dst_space
        if src_name == dst_name:
            return slate

        self._append_log(f"Slate OCIO: {src_name} \u2192 {dst_name}")
        rgb = np.ascontiguousarray(slate[:, :, :3], dtype=np.float32)
        h, w = rgb.shape[:2]
        cpu = cfg.getProcessor(src_name, dst_name).getDefaultCPUProcessor()
        desc = OCIO_mod.PackedImageDesc(rgb, w, h, 3)
        cpu.apply(desc)
        result = np.empty_like(slate)
        result[:, :, :3] = rgb
        result[:, :, 3] = slate[:, :, 3]
        return result

    @staticmethod
    def _detect_slate_resolution(mode: str, inp: str) -> tuple[int, int]:
        """Probe the input to determine the resolution for the slate frame."""
        try:
            if mode == "video2exr":
                from ..core.video import probe_video

                w, h, _fps, _total = probe_video(inp)
                return w, h
            else:
                from ..core.exr_io import read_exr
                from ..core.sequence import find_exr_sequence

                paths, _bn = find_exr_sequence(inp)
                if paths:
                    first = read_exr(paths[0])
                    return first.shape[1], first.shape[0]
        except Exception:
            pass
        return 1920, 1080

    # -- State snapshot/restore (for presets) --

    def snapshot_state(self) -> dict:
        """Capture parameters only — no input/output paths."""
        return {
            "tab": self._tabs.currentIndex(),
            "ocio_source": self._ocio_panel.current_source_key(),
            "ocio_file": self._ocio_panel._file_path,
            "v2e_src_space": self._v2e_tab.src_btn.current_space(),
            "v2e_dst_space": self._v2e_tab.dst_btn.current_space(),
            "v2e_compression": self._v2e_tab.get_compression(),
            "v2e_scale": self._v2e_tab.get_scale(),
            "v2e_padding": self._v2e_tab.get_padding(),
            "v2e_start_frame": self._v2e_tab.get_start_frame(),
            "e2v_src_space": self._e2v_tab.src_btn.current_space(),
            "e2v_dst_space": self._e2v_tab.dst_btn.current_space(),
            "e2v_fps": self._e2v_tab.get_fps(),
            "e2v_scale": self._e2v_tab.get_scale(),
            "e2v_codec": self._e2v_tab.get_video_codec_info()[0],
        }

    def restore_state(self, data: dict) -> None:
        """Restore parameters only — input/output paths are left untouched."""
        if "tab" in data:
            self._tabs.setCurrentIndex(data["tab"])
        if "ocio_source" in data:
            combo = self._ocio_panel._source_combo
            for i in range(combo.count()):
                if combo.itemData(i) == data["ocio_source"]:
                    combo.setCurrentIndex(i)
                    break
        if "ocio_file" in data:
            self._ocio_panel._file_path = data["ocio_file"]
            self._ocio_panel._settings.setValue("ocio/file_path", data["ocio_file"])
            self._ocio_panel._update_custom_label()
        if "v2e_src_space" in data:
            self._v2e_tab.src_btn.set_current_space(data["v2e_src_space"])
        if "v2e_dst_space" in data:
            self._v2e_tab.dst_btn.set_current_space(data["v2e_dst_space"])
        if "v2e_compression" in data and self._v2e_tab.compression_combo:
            from ..core.constants import EXR_COMPRESSIONS

            val = data["v2e_compression"]
            if val in EXR_COMPRESSIONS:
                self._v2e_tab.compression_combo.setCurrentIndex(EXR_COMPRESSIONS.index(val))
        if "v2e_padding" in data and self._v2e_tab.padding_spin:
            self._v2e_tab.padding_spin.setValue(int(data["v2e_padding"]))
        if "v2e_start_frame" in data and self._v2e_tab.start_frame_spin:
            self._v2e_tab.start_frame_spin.setValue(int(data["v2e_start_frame"]))
        if "e2v_src_space" in data:
            self._e2v_tab.src_btn.set_current_space(data["e2v_src_space"])
        if "e2v_dst_space" in data:
            self._e2v_tab.dst_btn.set_current_space(data["e2v_dst_space"])
        if "e2v_fps" in data and self._e2v_tab.fps_widget:
            self._e2v_tab.fps_widget._restore(data["e2v_fps"])
        for tab_prefix, tab_widget in [("v2e", self._v2e_tab), ("e2v", self._e2v_tab)]:
            scale_key = f"{tab_prefix}_scale"
            if scale_key in data:
                for i in range(tab_widget.scale_combo.count()):
                    if abs(tab_widget.scale_combo.itemData(i) - data[scale_key]) < 0.01:
                        tab_widget.scale_combo.setCurrentIndex(i)
                        break
        if "e2v_codec" in data and self._e2v_tab.codec_combo:
            for i in range(self._e2v_tab.codec_combo.count()):
                if self._e2v_tab.codec_combo.itemData(i) == data["e2v_codec"]:
                    self._e2v_tab.codec_combo.setCurrentIndex(i)
                    break

    # -- Drag and drop --

    _VIDEO_EXTS = {
        ".mp4",
        ".mov",
        ".mkv",
        ".avi",
        ".mxf",
        ".webm",
        ".m4v",
        ".ts",
    }

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    p = Path(url.toLocalFile())
                    if p.is_dir() or p.suffix.lower() in (self._VIDEO_EXTS | {".exr"}):
                        event.acceptProposedAction()
                        return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        mime = event.mimeData()
        if mime is None or not mime.hasUrls():
            return
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            p = Path(url.toLocalFile())
            if p.is_file() and p.suffix.lower() in self._VIDEO_EXTS:
                self._tabs.setCurrentIndex(0)
                self._v2e_tab.handle_dropped_path(str(p))
                self._append_log(f"Dropped video: {p.name}")
                event.acceptProposedAction()
                return
            if p.is_dir() or (p.is_file() and p.suffix.lower() == ".exr"):
                self._tabs.setCurrentIndex(1)
                self._e2v_tab.handle_dropped_path(str(p))
                self._append_log(f"Dropped: {p.name}")
                event.acceptProposedAction()
                return

    def closeEvent(self, event: QCloseEvent) -> None:
        self._settings.setValue("ui/geometry", self.saveGeometry())
        # Flush prefs (input paths, post-convert toggles, …) before exit so a
        # subsequent launch restores a validated source without re-browsing.
        self._settings.sync()
        # Stop background work so QApplication tear-down does not race threads.
        if self._worker is not None:
            self._worker.cancel()
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            if not self._thread.wait(8000):
                self._append_log("Convert thread did not stop in time; forcing exit.")
        super().closeEvent(event)
