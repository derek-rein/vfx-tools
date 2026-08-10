/* SPDX-License-Identifier: MIT
 *
 * Thin C ABI over the proprietary RED R3D SDK.
 *
 * Build only when you have a local copy of the official R3D SDK (not redistributed
 * with this repository). See docs/r3d.md.
 *
 * This header is project-owned and may be redistributed with the open-source app.
 * Do NOT ship RED headers, static libraries, or SDK documentation.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#if defined(_WIN32)
#  if defined(R3D_BRIDGE_EXPORTS)
#    define R3D_BRIDGE_API __declspec(dllexport)
#  else
#    define R3D_BRIDGE_API __declspec(dllimport)
#  endif
#else
#  define R3D_BRIDGE_API __attribute__((visibility("default")))
#endif

/* Decode quality / resolution ladder (maps to R3D VideoDecodeMode). */
enum R3DBridgeDecodeMode {
    R3D_DECODE_FULL_PREMIUM = 0,
    R3D_DECODE_HALF_PREMIUM = 1,
    R3D_DECODE_HALF_GOOD = 2,
    R3D_DECODE_QUARTER_GOOD = 3,
    R3D_DECODE_EIGHTH_GOOD = 4,
    R3D_DECODE_SIXTEENTH_GOOD = 5,
};

/* Color pipeline for conversion (IPP2 primary development when possible). */
enum R3DBridgePipeline {
    /* IPP2 primary RAW → REDWideGamutRGB + Log3G10 (best for OCIO → ACEScg). */
    R3D_PIPELINE_PRIMARY_LOG3G10 = 0,
    /* Clip / RMD defaults as stored on set. */
    R3D_PIPELINE_CLIP_DEFAULT = 1,
};

/* IdentifyFile result. */
enum R3DBridgeFileId {
    R3D_FILE_UNKNOWN = 0,
    R3D_FILE_R3D = 1,
    R3D_FILE_NEV_NRAW = 3,
    R3D_FILE_R3D_NE = 4,
};

typedef struct R3DBridgeClipInfo {
    uint32_t width;
    uint32_t height;
    uint32_t frame_count;
    float fps;
    int file_id; /* R3DBridgeFileId */
    char sdk_version[256];
    char colorspace_hint[64]; /* e.g. "Log3G10 REDWideGamutRGB" */
} R3DBridgeClipInfo;

/* Returns 1 if the bridge was built with R3D SDK linkage. Always 1 for this binary. */
R3D_BRIDGE_API int r3d_bridge_available(void);

/* Initialize the SDK. *libs_path* must be a UTF-8 folder containing REDR3D.* etc.
 * Call once before open/decode. Returns 0 on success. */
R3D_BRIDGE_API int r3d_bridge_initialize(const char *libs_path);

/* Finalize SDK (safe to call even if initialize failed). */
R3D_BRIDGE_API void r3d_bridge_finalize(void);

/* 1 if InitializeSdk succeeded in this process. */
R3D_BRIDGE_API int r3d_bridge_is_initialized(void);

/* Copy SDK version string into buf (NUL-terminated). */
R3D_BRIDGE_API void r3d_bridge_sdk_version(char *buf, size_t buf_len);

/* Quick file type without full parse. */
R3D_BRIDGE_API int r3d_bridge_identify(const char *utf8_path);

/* Open clip (spanning multi-part R3D loads automatically). Opaque handle. */
R3D_BRIDGE_API void *r3d_bridge_open(const char *utf8_path);

R3D_BRIDGE_API void r3d_bridge_close(void *clip);

/* Fill info for an open clip. Returns 0 on success. */
R3D_BRIDGE_API int r3d_bridge_clip_info(void *clip, R3DBridgeClipInfo *out);

/* Decode frame_index (0-based) into *out_rgb_f32* as contiguous H×W×3 float32
 * interleaved RGB in 0–1 (or log-encoded 0–1 for Log3G10 primary).
 * *out_w / *out_h* receive the decoded dimensions for the chosen mode.
 * Returns 0 on success. */
R3D_BRIDGE_API int r3d_bridge_decode_frame(
    void *clip,
    uint32_t frame_index,
    int decode_mode, /* R3DBridgeDecodeMode */
    int pipeline, /* R3DBridgePipeline */
    float *out_rgb_f32,
    size_t out_rgb_bytes,
    uint32_t *out_w,
    uint32_t *out_h);

/* Bytes required for a decode buffer at the given mode (aligned size may be larger). */
R3D_BRIDGE_API size_t r3d_bridge_decode_buffer_bytes(
    void *clip, int decode_mode, uint32_t *out_w, uint32_t *out_h);

/* Human-readable last error (thread-local best-effort; process-wide is fine). */
R3D_BRIDGE_API const char *r3d_bridge_last_error(void);

/* Clip-level metadata string for *key* (case-insensitive RMD key).
 * Writes NUL-terminated UTF-8 into *buf*. Returns 1 if present and non-empty,
 * 0 if missing / empty, -1 on error. */
R3D_BRIDGE_API int r3d_bridge_metadata_string(
    void *clip, const char *key, char *buf, size_t buf_len);

/* Absolute (TOD) timecode for *frame_index* (0-based). Returns 1 on success. */
R3D_BRIDGE_API int r3d_bridge_absolute_timecode(
    void *clip, uint32_t frame_index, char *buf, size_t buf_len);

/* Edge (run-record) timecode for *frame_index* (0-based). Returns 1 on success. */
R3D_BRIDGE_API int r3d_bridge_edge_timecode(
    void *clip, uint32_t frame_index, char *buf, size_t buf_len);

#ifdef __cplusplus
}
#endif
