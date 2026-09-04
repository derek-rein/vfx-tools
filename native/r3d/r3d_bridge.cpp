/* SPDX-License-Identifier: MIT
 *
 * C ABI bridge for the RED R3D SDK (CPU decode + optional GPU: Metal / CUDA / OpenCL).
 * Links against RED static lib + loads redistributable dylibs at InitializeSdk.
 *
 * Redistribute only this bridge object code + RED Redistributable/ dynamic
 * libraries (original form, private app directory). Never ship RED headers,
 * static libs, sample sources, or SDK documentation.
 */

#include "r3d_bridge.h"
#include "r3d_bridge_internal.h"
#include "r3d_gpu.h"

#include "R3DSDK.h"

#include <cstdlib>
#include <cstring>
#include <mutex>
#include <new>
#include <string>

#ifdef _WIN32
#include <malloc.h>
#endif

namespace {

std::mutex g_mu;
bool g_initialized = false;
std::string g_last_error;

struct BridgeClip {
    R3DSDK::Clip *clip = nullptr;
    bool gpu_supported = false;
    R3DSDK::ImageProcessingSettings *ip = nullptr;
    int ip_pipeline = -1;
    unsigned char *u16 = nullptr;
    size_t u16_bytes = 0;
};

unsigned char *aligned_alloc16(size_t size)
{
#ifdef _WIN32
    return static_cast<unsigned char *>(_aligned_malloc(size, 16U));
#else
    void *p = nullptr;
    if (posix_memalign(&p, 16U, size) != 0) {
        return nullptr;
    }
    return static_cast<unsigned char *>(p);
#endif
}

void aligned_free16(void *p)
{
    if (!p) {
        return;
    }
#ifdef _WIN32
    _aligned_free(p);
#else
    std::free(p);
#endif
}

BridgeClip *as_bridge(void *p)
{
    return static_cast<BridgeClip *>(p);
}

R3DSDK::Clip *as_clip(void *p)
{
    BridgeClip *b = as_bridge(p);
    return b ? b->clip : nullptr;
}

int cpu_decode_frame(
    BridgeClip *bridge,
    uint32_t frame_index,
    int decode_mode,
    int pipeline,
    float *out_rgb_f32,
    size_t out_rgb_bytes,
    uint32_t *out_w,
    uint32_t *out_h)
{
    R3DSDK::Clip *clip = bridge->clip;
    if (frame_index >= clip->VideoFrameCount()) {
        r3d_bridge_set_error("r3d_bridge_decode_frame: frame index out of range");
        return -2;
    }

    size_t w = 0, h = 0;
    r3d_dims_for_mode(clip->Width(), clip->Height(), decode_mode, w, h);
    const size_t n_pix = w * h * 3U;
    const size_t need_f32 = n_pix * sizeof(float);
    if (out_rgb_bytes < need_f32) {
        r3d_bridge_set_error("r3d_bridge_decode_frame: output buffer too small");
        return -3;
    }

    const size_t mem_needed = n_pix * 2U; // 16-bit packed RGB
    if (bridge->u16_bytes < mem_needed) {
        aligned_free16(bridge->u16);
        bridge->u16 = aligned_alloc16(mem_needed);
        bridge->u16_bytes = bridge->u16 ? mem_needed : 0;
        if (!bridge->u16) {
            r3d_bridge_set_error("r3d_bridge_decode_frame: alloc failed");
            return -4;
        }
    }

    if (!bridge->ip) {
        bridge->ip = new (std::nothrow) R3DSDK::ImageProcessingSettings();
        if (!bridge->ip) {
            r3d_bridge_set_error("r3d_bridge_decode_frame: IP alloc failed");
            return -4;
        }
        bridge->ip_pipeline = -1;
    }
    if (bridge->ip_pipeline != pipeline) {
        r3d_apply_pipeline(clip, *bridge->ip, pipeline);
        bridge->ip_pipeline = pipeline;
    }

    R3DSDK::VideoDecodeJob job;
    job.OutputBufferSize = mem_needed;
    job.OutputBuffer = bridge->u16;
    job.Mode = r3d_map_mode(decode_mode);
    job.PixelType = R3DSDK::PixelType_16Bit_RGB_Interleaved;
    job.ImageProcessing = bridge->ip;

    R3DSDK::DecodeStatus st = clip->DecodeVideoFrame(static_cast<size_t>(frame_index), job);
    if (st != R3DSDK::DSDecodeOK) {
        r3d_bridge_set_error(
            std::string("DecodeVideoFrame failed: ") + std::to_string(static_cast<int>(st)));
        return static_cast<int>(st);
    }

    r3d_u16_to_f32(reinterpret_cast<const uint16_t *>(bridge->u16), out_rgb_f32, n_pix);

    if (out_w)
        *out_w = static_cast<uint32_t>(w);
    if (out_h)
        *out_h = static_cast<uint32_t>(h);
    return 0;
}

} // namespace

void r3d_bridge_set_error(const std::string &msg)
{
    g_last_error = msg;
}

bool r3d_force_cpu()
{
    const char *e = std::getenv("EXR_CONVERTER_R3D_CPU");
    if (!e || !e[0]) {
        return false;
    }
    return !(e[0] == '0' && e[1] == '\0');
}

R3DSDK::VideoDecodeMode r3d_map_mode(int mode)
{
    switch (mode) {
    case R3D_DECODE_HALF_PREMIUM:
        return R3DSDK::DECODE_HALF_RES_PREMIUM;
    case R3D_DECODE_HALF_GOOD:
        return R3DSDK::DECODE_HALF_RES_GOOD;
    case R3D_DECODE_QUARTER_GOOD:
        return R3DSDK::DECODE_QUARTER_RES_GOOD;
    case R3D_DECODE_EIGHTH_GOOD:
        return R3DSDK::DECODE_EIGHT_RES_GOOD;
    case R3D_DECODE_SIXTEENTH_GOOD:
        return R3DSDK::DECODE_SIXTEENTH_RES_GOOD;
    case R3D_DECODE_FULL_PREMIUM:
    default:
        return R3DSDK::DECODE_FULL_RES_PREMIUM;
    }
}

void r3d_dims_for_mode(size_t full_w, size_t full_h, int mode, size_t &w, size_t &h)
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

void r3d_apply_pipeline(
    R3DSDK::Clip *clip, R3DSDK::ImageProcessingSettings &ip, int pipeline)
{
    clip->GetDefaultImageProcessingSettings(ip);

    if (pipeline == R3D_PIPELINE_CLIP_DEFAULT) {
        return;
    }

    // IPP2 primary RAW development → REDWideGamutRGB + Log3G10 (ideal for OCIO).
    ip.Version = R3DSDK::ColorVersion3;
    ip.ImagePipelineMode = R3DSDK::Primary_Development_Only;
    ip.ColorSpace = R3DSDK::ImageColorREDWideGamutRGB;
    ip.GammaCurve = R3DSDK::ImageGammaLog3G10;
}

void r3d_u16_to_f32(const uint16_t *src, float *dst, size_t n)
{
    const float scale = 1.0f / 65535.0f;
#if defined(__clang__)
#pragma clang loop vectorize(enable) interleave(enable)
#endif
    for (size_t i = 0; i < n; ++i) {
        dst[i] = static_cast<float>(src[i]) * scale;
    }
}

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
        r3d_bridge_set_error("r3d_bridge_initialize: empty libs path");
        return -1;
    }

    unsigned int components = OPTION_RED_NONE;
    if (!r3d_force_cpu()) {
#if defined(__APPLE__)
        components = OPTION_RED_METAL;
#else
        components = OPTION_RED_DECODER;
#endif
    }

    R3DSDK::InitializeStatus st = R3DSDK::InitializeSdk(libs_path, components);
    if (st != R3DSDK::ISInitializeOK && components != OPTION_RED_NONE) {
        R3DSDK::FinalizeSdk();
        st = R3DSDK::InitializeSdk(libs_path, OPTION_RED_NONE);
        if (st == R3DSDK::ISInitializeOK) {
            r3d_bridge_set_error("InitializeSdk: GPU component unavailable, using CPU");
        }
    }
    if (st != R3DSDK::ISInitializeOK) {
        r3d_bridge_set_error(
            std::string("InitializeSdk failed: ") + std::to_string(static_cast<int>(st))
            + " path=" + libs_path);
        R3DSDK::FinalizeSdk();
        return static_cast<int>(st);
    }
    g_initialized = true;
    g_last_error.clear();
    return 0;
}

void r3d_bridge_finalize(void)
{
    std::lock_guard<std::mutex> lock(g_mu);
    r3d_gpu::shutdown();
    if (g_initialized) {
        R3DSDK::FinalizeSdk();
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
    const char *v = R3DSDK::GetSdkVersion();
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
    return R3DSDK::IdentifyFile(utf8_path);
}

void *r3d_bridge_open(const char *utf8_path)
{
    if (!utf8_path || !utf8_path[0]) {
        r3d_bridge_set_error("r3d_bridge_open: empty path");
        return nullptr;
    }
    if (!r3d_bridge_is_initialized()) {
        r3d_bridge_set_error("r3d_bridge_open: SDK not initialized");
        return nullptr;
    }
    BridgeClip *bridge = new (std::nothrow) BridgeClip();
    if (!bridge) {
        r3d_bridge_set_error("r3d_bridge_open: out of memory");
        return nullptr;
    }
    bridge->clip = new (std::nothrow) R3DSDK::Clip(utf8_path);
    if (!bridge->clip) {
        delete bridge;
        r3d_bridge_set_error("r3d_bridge_open: out of memory");
        return nullptr;
    }
    if (bridge->clip->Status() != R3DSDK::LSClipLoaded) {
        r3d_bridge_set_error(
            std::string("Failed to load clip: ") + utf8_path
            + " status=" + std::to_string(static_cast<int>(bridge->clip->Status())));
        delete bridge->clip;
        delete bridge;
        return nullptr;
    }
    r3d_gpu::startup();
    bridge->gpu_supported = r3d_gpu::available() && r3d_gpu::clip_supported(bridge->clip);
    return bridge;
}

void r3d_bridge_close(void *clip)
{
    BridgeClip *bridge = as_bridge(clip);
    if (!bridge) {
        return;
    }
    delete bridge->ip;
    aligned_free16(bridge->u16);
    delete bridge->clip;
    delete bridge;
}

int r3d_bridge_clip_info(void *clip_ptr, R3DBridgeClipInfo *out)
{
    R3DSDK::Clip *clip = as_clip(clip_ptr);
    if (!clip || !out) {
        r3d_bridge_set_error("r3d_bridge_clip_info: null argument");
        return -1;
    }
    std::memset(out, 0, sizeof(*out));
    out->width = static_cast<uint32_t>(clip->Width());
    out->height = static_cast<uint32_t>(clip->Height());
    out->frame_count = static_cast<uint32_t>(clip->VideoFrameCount());
    out->fps = clip->VideoAudioFramerate();
    std::strncpy(out->colorspace_hint, "Log3G10 REDWideGamutRGB", sizeof(out->colorspace_hint) - 1);
    r3d_bridge_sdk_version(out->sdk_version, sizeof(out->sdk_version));
    return 0;
}

size_t r3d_bridge_decode_buffer_bytes(
    void *clip_ptr, int decode_mode, uint32_t *out_w, uint32_t *out_h)
{
    R3DSDK::Clip *clip = as_clip(clip_ptr);
    if (!clip) {
        return 0;
    }
    size_t w = 0, h = 0;
    r3d_dims_for_mode(clip->Width(), clip->Height(), decode_mode, w, h);
    if (out_w)
        *out_w = static_cast<uint32_t>(w);
    if (out_h)
        *out_h = static_cast<uint32_t>(h);
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
    BridgeClip *bridge = as_bridge(clip_ptr);
    if (!bridge || !bridge->clip || !out_rgb_f32) {
        r3d_bridge_set_error("r3d_bridge_decode_frame: null argument");
        return -1;
    }

    if (bridge->gpu_supported) {
        int rc = r3d_gpu::decode_frame(
            bridge->clip,
            frame_index,
            decode_mode,
            pipeline,
            out_rgb_f32,
            out_rgb_bytes,
            out_w,
            out_h);
        if (rc == 0) {
            return 0;
        }
        // Infrastructure / process failure: fall back to CPU for the rest of this clip.
        if (rc < 0) {
            bridge->gpu_supported = false;
        } else {
            // SDK-level decode error (dropped frame, I/O, …) — CPU will likely fail too.
            return cpu_decode_frame(
                bridge, frame_index, decode_mode, pipeline, out_rgb_f32, out_rgb_bytes, out_w, out_h);
        }
    }

    return cpu_decode_frame(
        bridge, frame_index, decode_mode, pipeline, out_rgb_f32, out_rgb_bytes, out_w, out_h);
}

const char *r3d_bridge_last_error(void)
{
    return g_last_error.c_str();
}

const char *r3d_bridge_decoder_kind(void)
{
    if (!g_initialized) {
        return "";
    }
    if (r3d_gpu::ready()) {
        const char *kind = r3d_gpu::kind();
        if (kind && kind[0]) {
            return kind;
        }
    }
#if defined(__APPLE__)
    if (!r3d_force_cpu()) {
        return "metal";
    }
#endif
    return "cpu";
}

int r3d_bridge_clip_uses_gpu(void *clip)
{
    BridgeClip *bridge = as_bridge(clip);
    if (!bridge) {
        return 0;
    }
    return bridge->gpu_supported ? 1 : 0;
}

int r3d_bridge_metadata_string(
    void *clip_ptr, const char *key, char *buf, size_t buf_len)
{
    R3DSDK::Clip *clip = as_clip(clip_ptr);
    if (!clip || !key || !buf || buf_len == 0) {
        r3d_bridge_set_error("r3d_bridge_metadata_string: null argument");
        return -1;
    }
    buf[0] = '\0';
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
    R3DSDK::Clip *clip = as_clip(clip_ptr);
    if (!clip || !buf || buf_len == 0) {
        r3d_bridge_set_error("r3d_bridge_absolute_timecode: null argument");
        return -1;
    }
    buf[0] = '\0';
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
    R3DSDK::Clip *clip = as_clip(clip_ptr);
    if (!clip || !buf || buf_len == 0) {
        r3d_bridge_set_error("r3d_bridge_edge_timecode: null argument");
        return -1;
    }
    buf[0] = '\0';
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
