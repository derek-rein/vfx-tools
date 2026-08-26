"""Backward-compatible re-export façade.

This module used to contain every convert-tab / browser-dialog / color widget
in one ~5500-line file. It has been split into focused modules:

- :mod:`src.gui.color_widgets` — ``ColorSpaceButton``, ``FpsCombo``, ``OcioConfigPanel``
- :mod:`src.gui.browser_chrome` — shared file-browser chrome (places sidebar, search
  tree, thumbnails, copy-path helpers) used by the browser dialogs
- :mod:`src.gui.sequence_browser` — ``SequenceBrowserDialog``
- :mod:`src.gui.video_browser` — ``VideoBrowserDialog``
- :mod:`src.gui.convert_tab` — the codec/compression settings dialogs and ``ConvertTab``

Existing imports of ``src.gui.widgets`` keep working via this façade; prefer
importing from the specific module directly in new code.
"""

from __future__ import annotations

from .browser_chrome import (
    _MAX_SEARCH_DEPTH,
    _MAX_SEARCH_RESULTS,
    _OS_PLACES,
    _SEARCH_BATCH_SIZE,
    _SEARCH_DEBOUNCE_MS,
    _SEARCH_SKIP_ABSPATHS,
    _SEARCH_SKIP_DIRS,
    _SEARCH_SKIP_FILES,
    _SEARCH_SKIP_SUFFIXES,
    _SEQ_BROWSER_VIEW_GRID,
    _SEQ_BROWSER_VIEW_LIST,
    _SEQ_BROWSER_VIEW_PREVIEW,
    _SEQ_THUMB_EDGE,
    _SEQ_THUMB_ICON,
    _VID_BROWSER_VIEW_GRID,
    _VID_BROWSER_VIEW_LIST,
    _VID_BROWSER_VIEW_PREVIEW,
    _VIDEO_EXTS,
    _add_copy_path_actions,
    _configure_path_line_edit,
    _copy_to_clipboard,
    _DirSearchWorker,
    _ElidingLabel,
    _FavoritesDropList,
    _folder_path_for_copy,
    _places_divider_item,
    _PlacesSidebar,
    _SearchableTree,
    _setup_dir_tree,
    _ThumbJob,
    _ThumbSignals,
    _tree_click_toggle_expand,
    _wire_volume_refresh,
)
from .color_widgets import (
    FORM_ROW_MIN_HEIGHT,
    ColorSpaceButton,
    FpsCombo,
    OcioConfigPanel,
    lock_form_row_height,
)
from .convert_tab import (
    _CODEC_HAS_SETTINGS,
    _CODEC_HELP,
    _EXR_COMPRESSION_HELP,
    _EXR_HAS_SETTINGS,
    CodecPicker,
    ConvertTab,
    ExrCompressionSettingsDialog,
    VideoCodecSettingsDialog,
    VideoInput,
    _InputProbeWorker,
    _populate_video_codec_combo,
    _select_video_codec_combo_key,
)
from .sequence_browser import SequenceBrowserDialog
from .video_browser import VideoBrowserDialog

__all__ = [
    "FORM_ROW_MIN_HEIGHT",
    "lock_form_row_height",
    "ColorSpaceButton",
    "FpsCombo",
    "OcioConfigPanel",
    "SequenceBrowserDialog",
    "VideoBrowserDialog",
    "ExrCompressionSettingsDialog",
    "VideoCodecSettingsDialog",
    "VideoInput",
    "CodecPicker",
    "ConvertTab",
    "_CODEC_HELP",
    "_EXR_COMPRESSION_HELP",
    "_CODEC_HAS_SETTINGS",
    "_EXR_HAS_SETTINGS",
    "_InputProbeWorker",
    "_populate_video_codec_combo",
    "_select_video_codec_combo_key",
    "_add_copy_path_actions",
    "_configure_path_line_edit",
    "_copy_to_clipboard",
    "_DirSearchWorker",
    "_ElidingLabel",
    "_FavoritesDropList",
    "_folder_path_for_copy",
    "_places_divider_item",
    "_PlacesSidebar",
    "_SearchableTree",
    "_setup_dir_tree",
    "_ThumbJob",
    "_ThumbSignals",
    "_tree_click_toggle_expand",
    "_wire_volume_refresh",
    "_OS_PLACES",
    "_SEARCH_SKIP_DIRS",
    "_SEARCH_SKIP_ABSPATHS",
    "_SEARCH_SKIP_FILES",
    "_SEARCH_SKIP_SUFFIXES",
    "_VIDEO_EXTS",
    "_MAX_SEARCH_DEPTH",
    "_SEARCH_BATCH_SIZE",
    "_MAX_SEARCH_RESULTS",
    "_SEARCH_DEBOUNCE_MS",
    "_SEQ_BROWSER_VIEW_LIST",
    "_SEQ_BROWSER_VIEW_GRID",
    "_SEQ_BROWSER_VIEW_PREVIEW",
    "_VID_BROWSER_VIEW_LIST",
    "_VID_BROWSER_VIEW_GRID",
    "_VID_BROWSER_VIEW_PREVIEW",
    "_SEQ_THUMB_EDGE",
    "_SEQ_THUMB_ICON",
]
