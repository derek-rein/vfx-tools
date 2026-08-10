/* SPDX-License-Identifier: MIT
 *
 * C ABI bridge for the RED R3D SDK (CPU decode path).
 * Links against RED static lib + loads redistributable dylibs at InitializeSdk.
 *
 * Redistribute only this bridge object code + RED Redistributable/ dynamic
 * libraries (original form, private app directory). Never ship RED headers,
 * static libs, sample sources, or SDK documentation.
 */

#include "r3d_bridge.h"

#include "R3DSDK.h"

#include <cstdlib>
#include <cstring>
#include <mutex>
#include <new>
#include <string>

using namespace R3DSDK;

namespace {

std::mutex g_mu;
bool g_initialized = false;
std::string g_last_error;

void set_error(const std::string &msg)
{
    g_last_error = msg;
}

unsigned char *aligned_malloc(size_t &size_needed, size_t &offset_out)
{
    // R3D requires 16-byte alignment for output buffers.
    unsigned char *buffer = static_cast<unsigned char *>(std::malloc(size_needed + 15U));
    if (!buffer) {
        offset_out = 0;
        size_needed = 0;
        return nullptr;
    }
    uintptr_t ptr = reinterpret_cast<uintptr_t>(buffer);
    if ((ptr % 16U) == 0U) {
        offset_out = 0;
        return buffer;
    }
    offset_out = 16U - (ptr % 16U);
    return buffer + offset_out;
}

VideoDecodeMode map_mode(int mode)
{
    switch (mode) {
    case R3D_DECODE_HALF_PREMIUM:
        return DECODE_HALF_RES_PREMIUM;
    case R3D_DECODE_HALF_GOOD:
        return DECODE_HALF_RES_GOOD;
    case R3D_DECODE_QUARTER_GOOD:
        return DECODE_QUARTER_RES_GOOD;
    case R3D_DECODE_EIGHTH_GOOD:
        return DECODE_EIGHT_RES_GOOD;
    case R3D_DECODE_SIXTEENTH_GOOD:
        return DECODE_SIXTEENTH_RES_GOOD;
    case R3D_DECODE_FULL_PREMIUM:
    default:
        return DECODE_FULL_RES_PREMIUM;
    }
}

void dims_for_mode(size_t full_w, size_t full_h, int mode, size_t &w, size_t &h)
{
    size_t div = 1;
    switch (mode) {
    case R3D_DECODE_HALF_PREMIUM:
    case R3D_DECODE_HALF_GOOD:
        div = 2;
        break;
    case R3D_DECODE_QUARTER_GOOD:
        div = 4;
        break;
    case R3D_DECODE_EIGHTH_GOOD:
        div = 8;
        break;
    case R3D_DECODE_SIXTEENTH_GOOD:
        div = 16;
        break;
    default:
        div = 1;
        break;
    }
    w = full_w / div;
    h = full_h / div;
    if (w < 1)
        w = 1;
    if (h < 1)
        h = 1;
}

void apply_pipeline(Clip *clip, ImageProcessingSettings &ip, int pipeline)
{
    clip->GetDefaultImageProcessingSettings(ip);

    if (pipeline == R3D_PIPELINE_CLIP_DEFAULT) {
        return;
    }

    // IPP2 primary RAW development → REDWideGamutRGB + Log3G10 (ideal for OCIO).
    // Request ColorVersion3; the SDK raises the floor to MinimumColorVersion() if needed.
    ip.Version = ColorVersion3;
    ip.ImagePipelineMode = Primary_Development_Only;
    // Also set explicit RWG/Log3G10 for legacy clips that cannot use Primary mode.
    ip.ColorSpace = ImageColorREDWideGamutRGB;
    ip.GammaCurve = ImageGammaLog3G10;
}

} // namespace

extern "C" {

int r3d_bridge_available(void)
{
    return 1;
}

int r3d_bridge_initialize(const char *libs_path)
{
    std::lock_guard<std::mutex> lock(g_mu);
    if (g_initialized) {
        return 0;
    }
    if (!libs_path || !libs_path[0]) {
        set_error("r3d_bridge_initialize: empty libs path");
        return -1;
    }
    InitializeStatus st = InitializeSdk(libs_path, OPTION_RED_NONE);
    if (st != ISInitializeOK) {
        set_error(std::string("InitializeSdk failed: ") + std::to_string(static_cast<int>(st))
                  + " path=" + libs_path);
        // Still finalize so a later retry is clean.
        FinalizeSdk();
        return static_cast<int>(st);
    }
    g_initialized = true;
    g_last_error.clear();
    return 0;
}

void r3d_bridge_finalize(void)
{
    std::lock_guard<std::mutex> lock(g_mu);
    if (g_initialized) {
        FinalizeSdk();
        g_initialized = false;
    }
}

int r3d_bridge_is_initialized(void)
{
    std::lock_guard<std::mutex> lock(g_mu);
    return g_initialized ? 1 : 0;
}

void r3d_bridge_sdk_version(char *buf, size_t buf_len)
{
    if (!buf || buf_len == 0) {
        return;
    }
    const char *v = GetSdkVersion();
    if (!v) {
        buf[0] = '\0';
        return;
    }
    std::strncpy(buf, v, buf_len - 1);
    buf[buf_len - 1] = '\0';
}

int r3d_bridge_identify(const char *utf8_path)
{
    if (!utf8_path) {
        return R3D_FILE_UNKNOWN;
    }
    return IdentifyFile(utf8_path);
}

void *r3d_bridge_open(const char *utf8_path)
{
    if (!utf8_path || !utf8_path[0]) {
        set_error("r3d_bridge_open: empty path");
        return nullptr;
    }
    if (!r3d_bridge_is_initialized()) {
        set_error("r3d_bridge_open: SDK not initialized");
        return nullptr;
    }
    Clip *clip = new (std::nothrow) Clip(utf8_path);
    if (!clip) {
        set_error("r3d_bridge_open: out of memory");
        return nullptr;
    }
    if (clip->Status() != LSClipLoaded) {
        set_error(std::string("Failed to load clip: ") + utf8_path
                  + " status=" + std::to_string(static_cast<int>(clip->Status())));
        delete clip;
        return nullptr;
    }
    return clip;
}

void r3d_bridge_close(void *clip)
{
    if (!clip) {
        return;
    }
    delete static_cast<Clip *>(clip);
}

int r3d_bridge_clip_info(void *clip_ptr, R3DBridgeClipInfo *out)
{
    if (!clip_ptr || !out) {
        set_error("r3d_bridge_clip_info: null argument");
        return -1;
    }
    Clip *clip = static_cast<Clip *>(clip_ptr);
    std::memset(out, 0, sizeof(*out));
    out->width = static_cast<uint32_t>(clip->Width());
    out->height = static_cast<uint32_t>(clip->Height());
    out->frame_count = static_cast<uint32_t>(clip->VideoFrameCount());
    out->fps = clip->VideoAudioFramerate();
    // Hint for OCIO auto-detect (primary Log3G10 / RWG).
    std::strncpy(out->colorspace_hint, "Log3G10 REDWideGamutRGB", sizeof(out->colorspace_hint) - 1);
    r3d_bridge_sdk_version(out->sdk_version, sizeof(out->sdk_version));
    return 0;
}

size_t r3d_bridge_decode_buffer_bytes(
    void *clip_ptr, int decode_mode, uint32_t *out_w, uint32_t *out_h)
{
    if (!clip_ptr) {
        return 0;
    }
    Clip *clip = static_cast<Clip *>(clip_ptr);
    size_t w = 0, h = 0;
    dims_for_mode(clip->Width(), clip->Height(), decode_mode, w, h);
    if (out_w)
        *out_w = static_cast<uint32_t>(w);
    if (out_h)
        *out_h = static_cast<uint32_t>(h);
    // 16-bit RGB interleaved
    return w * h * 3U * 2U;
}

int r3d_bridge_decode_frame(
    void *clip_ptr,
    uint32_t frame_index,
    int decode_mode,
    int pipeline,
    float *out_rgb_f32,
    size_t out_rgb_bytes,
    uint32_t *out_w,
    uint32_t *out_h)
{
    if (!clip_ptr || !out_rgb_f32) {
        set_error("r3d_bridge_decode_frame: null argument");
        return -1;
    }
    Clip *clip = static_cast<Clip *>(clip_ptr);
    if (frame_index >= clip->VideoFrameCount()) {
        set_error("r3d_bridge_decode_frame: frame index out of range");
        return -2;
    }

    size_t w = 0, h = 0;
    dims_for_mode(clip->Width(), clip->Height(), decode_mode, w, h);
    const size_t n_pix = w * h * 3U;
    const size_t need_f32 = n_pix * sizeof(float);
    if (out_rgb_bytes < need_f32) {
        set_error("r3d_bridge_decode_frame: output buffer too small");
        return -3;
    }

    size_t mem_needed = n_pix * 2U; // 16-bit
    size_t align_offset = 0;
    size_t adjusted = mem_needed;
    unsigned char *img = aligned_malloc(adjusted, align_offset);
    if (!img) {
        set_error("r3d_bridge_decode_frame: alloc failed");
        return -4;
    }
    // Pointer returned by aligned_malloc may be offset; free base separately.
    unsigned char *base = img - align_offset;

    ImageProcessingSettings *ip = new (std::nothrow) ImageProcessingSettings();
    if (!ip) {
        std::free(base);
        set_error("r3d_bridge_decode_frame: IP alloc failed");
        return -4;
    }
    apply_pipeline(clip, *ip, pipeline);

    VideoDecodeJob job;
    job.OutputBufferSize = mem_needed;
    job.OutputBuffer = img;
    job.Mode = map_mode(decode_mode);
    job.PixelType = PixelType_16Bit_RGB_Interleaved;
    job.ImageProcessing = ip;

    DecodeStatus st = clip->DecodeVideoFrame(static_cast<size_t>(frame_index), job);
    if (st != DSDecodeOK) {
        delete ip;
        std::free(base);
        set_error(std::string("DecodeVideoFrame failed: ") + std::to_string(static_cast<int>(st)));
        return static_cast<int>(st);
    }

    // Convert 16-bit interleaved RGB → float32 0–1.
    const uint16_t *src = reinterpret_cast<const uint16_t *>(img);
    const float scale = 1.0f / 65535.0f;
    for (size_t i = 0; i < n_pix; ++i) {
        out_rgb_f32[i] = static_cast<float>(src[i]) * scale;
    }

    if (out_w)
        *out_w = static_cast<uint32_t>(w);
    if (out_h)
        *out_h = static_cast<uint32_t>(h);

    delete ip;
    std::free(base);
    return 0;
}

const char *r3d_bridge_last_error(void)
{
    return g_last_error.c_str();
}

int r3d_bridge_metadata_string(
    void *clip_ptr, const char *key, char *buf, size_t buf_len)
{
    if (!clip_ptr || !key || !buf || buf_len == 0) {
        set_error("r3d_bridge_metadata_string: null argument");
        return -1;
    }
    buf[0] = '\0';
    Clip *clip = static_cast<Clip *>(clip_ptr);
    if (!clip->MetadataExists(key)) {
        return 0;
    }
    std::string val = clip->MetadataItemAsString(key);
    if (val.empty()) {
        return 0;
    }
    std::strncpy(buf, val.c_str(), buf_len - 1);
    buf[buf_len - 1] = '\0';
    return 1;
}

int r3d_bridge_absolute_timecode(
    void *clip_ptr, uint32_t frame_index, char *buf, size_t buf_len)
{
    if (!clip_ptr || !buf || buf_len == 0) {
        set_error("r3d_bridge_absolute_timecode: null argument");
        return -1;
    }
    buf[0] = '\0';
    Clip *clip = static_cast<Clip *>(clip_ptr);
    if (frame_index >= clip->VideoFrameCount()) {
        return 0;
    }
    const char *tc = clip->AbsoluteTimecode(static_cast<size_t>(frame_index));
    if (!tc || !tc[0]) {
        return 0;
    }
    std::strncpy(buf, tc, buf_len - 1);
    buf[buf_len - 1] = '\0';
    return 1;
}

int r3d_bridge_edge_timecode(
    void *clip_ptr, uint32_t frame_index, char *buf, size_t buf_len)
{
    if (!clip_ptr || !buf || buf_len == 0) {
        set_error("r3d_bridge_edge_timecode: null argument");
        return -1;
    }
    buf[0] = '\0';
    Clip *clip = static_cast<Clip *>(clip_ptr);
    if (frame_index >= clip->VideoFrameCount()) {
        return 0;
    }
    const char *tc = clip->EdgeTimecode(static_cast<size_t>(frame_index));
    if (!tc || !tc[0]) {
        return 0;
    }
    std::strncpy(buf, tc, buf_len - 1);
    buf[buf_len - 1] = '\0';
    return 1;
}

} // extern "C"
