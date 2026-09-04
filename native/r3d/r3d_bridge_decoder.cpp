/* SPDX-License-Identifier: MIT
 *
 * Linux / Windows GPU decode via the SDK's managed R3DDecoder (CUDA, then OpenCL).
 * No CUDA/OpenCL headers at compile time — REDDecoder loads them at runtime.
 *
 * GPU decompression is not part of R3DDecoder (CPU decompress + GPU IPP2).
 * N-RAW / R3D NE still get GPU image processing when the device works.
 */

#include "r3d_gpu.h"
#include "r3d_bridge_internal.h"

#include "R3DSDKDecoder.h"

#include <condition_variable>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <new>
#include <string>
#include <vector>

#ifdef _WIN32
#include <malloc.h>
#endif

namespace {

std::mutex g_gpu_mu;
bool g_started = false;
bool g_ok = false;
const char *g_kind = "";
R3DSDK::R3DDecoder *g_decoder = nullptr;
R3DSDK::R3DDecodeJob *g_job = nullptr;
R3DSDK::ImageProcessingSettings *g_ip = nullptr;
int g_ip_pipeline = -1;
R3DSDK::Clip *g_ip_clip = nullptr;
unsigned char *g_out = nullptr;
size_t g_out_bytes = 0;

struct DecodeWait {
    std::mutex mu;
    std::condition_variable cv;
    R3DSDK::R3DStatus status = R3DSDK::R3DStatus_ErrorProcessing;
    bool done = false;
};

void decode_callback(R3DSDK::R3DDecodeJob *item, R3DSDK::R3DStatus status)
{
    auto *wait = static_cast<DecodeWait *>(item->privateData);
    if (!wait) {
        return;
    }
    {
        std::lock_guard<std::mutex> lock(wait->mu);
        wait->status = status;
        wait->done = true;
    }
    wait->cv.notify_one();
}

unsigned char *host_aligned(size_t size)
{
#ifdef _WIN32
    return static_cast<unsigned char *>(_aligned_malloc(size, 64U));
#else
    void *p = nullptr;
    if (posix_memalign(&p, 64U, size) != 0) {
        return nullptr;
    }
    return static_cast<unsigned char *>(p);
#endif
}

void host_aligned_free(void *p)
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

void release_gpu_locked()
{
    g_ok = false;
    g_started = false;
    g_kind = "";
    if (g_job) {
        R3DSDK::R3DDecoder::ReleaseDecodeJob(g_job);
        g_job = nullptr;
    }
    if (g_decoder) {
        R3DSDK::R3DDecoder::ReleaseDecoder(g_decoder);
        g_decoder = nullptr;
    }
    delete g_ip;
    g_ip = nullptr;
    g_ip_pipeline = -1;
    g_ip_clip = nullptr;
    host_aligned_free(g_out);
    g_out = nullptr;
    g_out_bytes = 0;
}

void apply_option_defaults(R3DSDK::R3DDecoderOptions *opts)
{
    opts->setScratchFolder("");
    opts->setMemoryPoolSize(4096U);
    opts->setGPUMemoryPoolSize(4096U);
    opts->setGPUConcurrentFrameCount(1U);
    opts->setDecompressionThreadCount(0U);
    opts->setConcurrentImageCount(1U);
}

bool create_decoder_with_options(R3DSDK::R3DDecoderOptions *opts, const char *kind)
{
    R3DSDK::R3DDecoder *dec = nullptr;
    R3DSDK::R3DStatus st = R3DSDK::R3DDecoder::CreateDecoder(opts, &dec);
    if (st != R3DSDK::R3DStatus_Ok || !dec) {
        r3d_bridge_set_error(
            std::string("R3DDecoder::CreateDecoder failed (") + kind
            + ") status=" + std::to_string(static_cast<int>(st)));
        return false;
    }
    g_decoder = dec;
    g_kind = kind;
    return true;
}

bool try_cuda()
{
    R3DSDK::R3DDecoderOptions *opts = nullptr;
    R3DSDK::R3DStatus st = R3DSDK::R3DDecoderOptions::CreateOptions(&opts);
    if (st != R3DSDK::R3DStatus_Ok || !opts) {
        return false;
    }
    apply_option_defaults(opts);

    std::vector<R3DSDK::CudaDeviceInfo> devices;
    st = R3DSDK::R3DDecoderOptions::GetCudaDeviceList(devices);
    if (st != R3DSDK::R3DStatus_Ok || devices.empty()) {
        R3DSDK::R3DDecoderOptions::ReleaseOptions(opts);
        return false;
    }
    opts->useDevice(devices[0]);
    const bool ok = create_decoder_with_options(opts, "cuda");
    R3DSDK::R3DDecoderOptions::ReleaseOptions(opts);
    return ok;
}

bool try_opencl()
{
    R3DSDK::R3DDecoderOptions *opts = nullptr;
    R3DSDK::R3DStatus st = R3DSDK::R3DDecoderOptions::CreateOptions(&opts);
    if (st != R3DSDK::R3DStatus_Ok || !opts) {
        return false;
    }
    apply_option_defaults(opts);

    std::vector<R3DSDK::OpenCLDeviceInfo> devices;
    st = R3DSDK::R3DDecoderOptions::GetOpenCLDeviceList(devices);
    if (st != R3DSDK::R3DStatus_Ok || devices.empty()) {
        R3DSDK::R3DDecoderOptions::ReleaseOptions(opts);
        return false;
    }
    opts->useDevice(devices[0]);
    const bool ok = create_decoder_with_options(opts, "opencl");
    R3DSDK::R3DDecoderOptions::ReleaseOptions(opts);
    return ok;
}

bool startup_locked()
{
    if (g_started) {
        return g_ok;
    }
    g_started = true;
    if (r3d_force_cpu()) {
        r3d_bridge_set_error("R3D GPU disabled (EXR_CONVERTER_R3D_CPU)");
        g_ok = false;
        return false;
    }

    if (!try_cuda() && !try_opencl()) {
        release_gpu_locked();
        g_started = true;
        r3d_bridge_set_error("R3DDecoder: no CUDA or OpenCL GPU device");
        return false;
    }

    R3DSDK::R3DStatus jst = R3DSDK::R3DDecoder::CreateDecodeJob(&g_job);
    if (jst != R3DSDK::R3DStatus_Ok || !g_job) {
        r3d_bridge_set_error("R3DDecoder::CreateDecodeJob failed");
        release_gpu_locked();
        g_started = true;
        return false;
    }
    g_ok = true;
    r3d_bridge_set_error("");
    return true;
}

} // namespace

namespace r3d_gpu {

bool force_cpu()
{
    return r3d_force_cpu();
}

bool startup()
{
    std::lock_guard<std::mutex> lock(g_gpu_mu);
    return startup_locked();
}

void shutdown()
{
    std::lock_guard<std::mutex> lock(g_gpu_mu);
    release_gpu_locked();
}

bool available()
{
    std::lock_guard<std::mutex> lock(g_gpu_mu);
    if (!g_started) {
        startup_locked();
    }
    return g_ok;
}

bool ready()
{
    std::lock_guard<std::mutex> lock(g_gpu_mu);
    return g_ok;
}

const char *kind()
{
    std::lock_guard<std::mutex> lock(g_gpu_mu);
    return g_ok ? g_kind : "";
}

bool clip_supported(void *clip_ptr)
{
    if (!clip_ptr) {
        return false;
    }
    std::lock_guard<std::mutex> lock(g_gpu_mu);
    return startup_locked();
}

int decode_frame(
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
        r3d_bridge_set_error("r3d decoder: null argument");
        return -1;
    }

    std::lock_guard<std::mutex> lock(g_gpu_mu);
    if (!startup_locked() || !g_decoder || !g_job) {
        return -1;
    }

    auto *clip = static_cast<R3DSDK::Clip *>(clip_ptr);
    if (frame_index >= clip->VideoFrameCount()) {
        r3d_bridge_set_error("r3d decoder: frame index out of range");
        return -2;
    }

    size_t w = 0, h = 0;
    r3d_dims_for_mode(clip->Width(), clip->Height(), decode_mode, w, h);
    const size_t n_pix = w * h * 3U;
    const size_t need_f32 = n_pix * sizeof(float);
    if (out_rgb_bytes < need_f32) {
        r3d_bridge_set_error("r3d decoder: output buffer too small");
        return -3;
    }

    const size_t packed = n_pix * 2U;
    if (g_out_bytes < packed) {
        host_aligned_free(g_out);
        g_out = host_aligned(packed);
        g_out_bytes = g_out ? packed : 0;
        if (!g_out) {
            r3d_bridge_set_error("r3d decoder: output alloc failed");
            return -4;
        }
    }

    if (!g_ip) {
        g_ip = new (std::nothrow) R3DSDK::ImageProcessingSettings();
        if (!g_ip) {
            r3d_bridge_set_error("r3d decoder: IP alloc failed");
            return -4;
        }
        g_ip_pipeline = -1;
        g_ip_clip = nullptr;
    }
    if (g_ip_clip != clip || g_ip_pipeline != pipeline) {
        r3d_apply_pipeline(clip, *g_ip, pipeline);
        g_ip_clip = clip;
        g_ip_pipeline = pipeline;
    }

    g_job->clip = clip;
    g_job->videoTrackNo = 0;
    g_job->videoFrameNo = static_cast<size_t>(frame_index);
    g_job->callback = decode_callback;
    g_job->mode = r3d_map_mode(decode_mode);
    g_job->pixelType = R3DSDK::PixelType_16Bit_RGB_Interleaved;
    g_job->outputBuffer = g_out;
    g_job->bytesPerRow = w * 3U * 2U;
    g_job->outputBufferSize = packed;
    g_job->imageProcessingSettings = g_ip;
    g_job->outputFrameMetadata = nullptr;

    DecodeWait wait;
    g_job->privateData = &wait;

    R3DSDK::R3DStatus st = g_decoder->decode(g_job);
    if (st != R3DSDK::R3DStatus_Ok) {
        r3d_bridge_set_error(
            std::string("R3DDecoder::decode failed: ") + std::to_string(static_cast<int>(st)));
        return -6;
    }
    {
        std::unique_lock<std::mutex> ul(wait.mu);
        wait.cv.wait(ul, [&] { return wait.done; });
        st = wait.status;
    }
    if (st != R3DSDK::R3DStatus_Ok) {
        r3d_bridge_set_error(
            std::string("R3DDecoder callback failed: ") + std::to_string(static_cast<int>(st)));
        return -6;
    }

    r3d_u16_to_f32(reinterpret_cast<const uint16_t *>(g_out), out_rgb_f32, n_pix);
    if (out_w)
        *out_w = static_cast<uint32_t>(w);
    if (out_h)
        *out_h = static_cast<uint32_t>(h);
    return 0;
}

} // namespace r3d_gpu
