/* SPDX-License-Identifier: MIT
 *
 * macOS Metal GPU decode: GpuDecoder (decompress) + REDMetal (debayer / IPP2).
 * Falls back to CPU when the clip or GPU is unsupported.
 *
 * Do not copy RED sample sources; this is project-owned glue over the SDK headers.
 */

#include "r3d_gpu.h"

#import <Metal/Metal.h>

#include "r3d_bridge_internal.h"
#include "R3DSDKMetal.h"

#include <condition_variable>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <new>
#include <string>

namespace {

std::mutex g_gpu_mu;
bool g_started = false;
bool g_ok = false;
R3DSDK::EXT_METAL_API g_metal_api;
R3DSDK::REDMetal *g_red_metal = nullptr;
R3DSDK::GpuDecoder *g_gpu_decoder = nullptr;
id<MTLDevice> g_device = nil;
id<MTLCommandQueue> g_queue = nil;
id<MTLBuffer> g_raw_buf = nil;
id<MTLBuffer> g_out_buf = nil;
unsigned char *g_host_raw = nullptr;
size_t g_host_raw_bytes = 0;
R3DSDK::ImageProcessingSettings *g_ip = nullptr;
int g_ip_pipeline = -1;
R3DSDK::Clip *g_ip_clip = nullptr;
bool g_unified = true;

struct DecompressWait {
    std::mutex mu;
    std::condition_variable cv;
    R3DSDK::DecodeStatus status = R3DSDK::DSUnknownError;
    bool done = false;
};

void decompress_callback(R3DSDK::AsyncDecompressJob *item, R3DSDK::DecodeStatus status)
{
    auto *wait = static_cast<DecompressWait *>(item->PrivateData);
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
    void *p = nullptr;
    if (posix_memalign(&p, 64U, size) != 0) {
        return nullptr;
    }
    return static_cast<unsigned char *>(p);
}

id<MTLBuffer> ensure_mtl_buffer(id<MTLBuffer> existing, size_t size)
{
    if (existing && [existing length] >= size) {
        return existing;
    }
    const size_t alloc = (size + 4095U) & ~static_cast<size_t>(4095U);
    MTLResourceOptions opts = g_unified ? MTLResourceStorageModeShared
                                        : MTLResourceStorageModeManaged;
    return [g_device newBufferWithLength:alloc options:opts];
}

void release_gpu_locked()
{
    g_ok = false;
    g_started = false;
    if (g_gpu_decoder) {
        g_gpu_decoder->Close();
        delete g_gpu_decoder;
        g_gpu_decoder = nullptr;
    }
    if (g_red_metal) {
        delete g_red_metal;
        g_red_metal = nullptr;
    }
    delete g_ip;
    g_ip = nullptr;
    g_ip_pipeline = -1;
    g_ip_clip = nullptr;
    if (g_host_raw) {
        std::free(g_host_raw);
        g_host_raw = nullptr;
        g_host_raw_bytes = 0;
    }
    g_raw_buf = nil;
    g_out_buf = nil;
    g_queue = nil;
    g_device = nil;
}

bool startup_locked()
{
    if (g_started) {
        return g_ok;
    }
    g_started = true;
    if (r3d_force_cpu()) {
        r3d_bridge_set_error("R3D Metal disabled (EXR_CONVERTER_R3D_CPU)");
        g_ok = false;
        return false;
    }

    @autoreleasepool {
        g_device = MTLCreateSystemDefaultDevice();
        if (!g_device) {
            r3d_bridge_set_error("R3D Metal: no MTLDevice");
            return false;
        }
        g_unified = g_device.hasUnifiedMemory;
        g_queue = [g_device newCommandQueue];
        if (!g_queue) {
            r3d_bridge_set_error("R3D Metal: failed to create command queue");
            g_device = nil;
            return false;
        }

        g_red_metal = new (std::nothrow) R3DSDK::REDMetal(g_metal_api);
        if (!g_red_metal) {
            r3d_bridge_set_error("R3D Metal: REDMetal alloc failed");
            return false;
        }
        int err = 0;
        R3DSDK::REDMetal::Status cst = g_red_metal->checkCompatibility(g_queue, err);
        if (cst != R3DSDK::REDMetal::Status_Ok) {
            r3d_bridge_set_error(
                std::string("R3D Metal: checkCompatibility failed status=")
                + std::to_string(static_cast<int>(cst)) + " err=" + std::to_string(err));
            release_gpu_locked();
            g_started = true;
            return false;
        }

        g_gpu_decoder = new (std::nothrow) R3DSDK::GpuDecoder();
        if (!g_gpu_decoder) {
            r3d_bridge_set_error("R3D Metal: GpuDecoder alloc failed");
            release_gpu_locked();
            g_started = true;
            return false;
        }
        g_gpu_decoder->Open();
        g_ok = true;
        return true;
    }
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
    return g_ok ? "metal" : "";
}

bool clip_supported(void *clip_ptr)
{
    if (!clip_ptr) {
        return false;
    }
    std::lock_guard<std::mutex> lock(g_gpu_mu);
    if (!startup_locked()) {
        return false;
    }
    auto *clip = static_cast<R3DSDK::Clip *>(clip_ptr);
    return R3DSDK::GpuDecoder::DecodeSupportedForClip(*clip) == R3DSDK::DSDecodeOK;
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
        r3d_bridge_set_error("r3d metal decode: null argument");
        return -1;
    }

    std::lock_guard<std::mutex> lock(g_gpu_mu);
    if (!startup_locked()) {
        return -1;
    }

    auto *clip = static_cast<R3DSDK::Clip *>(clip_ptr);
    if (frame_index >= clip->VideoFrameCount()) {
        r3d_bridge_set_error("r3d metal decode: frame index out of range");
        return -2;
    }

    size_t w = 0, h = 0;
    r3d_dims_for_mode(clip->Width(), clip->Height(), decode_mode, w, h);
    const size_t n_pix = w * h * 3U;
    const size_t need_f32 = n_pix * sizeof(float);
    if (out_rgb_bytes < need_f32) {
        r3d_bridge_set_error("r3d metal decode: output buffer too small");
        return -3;
    }

    @autoreleasepool {
        R3DSDK::AsyncDecompressJob job;
        job.Clip = clip;
        job.Mode = r3d_map_mode(decode_mode);
        job.VideoFrameNo = static_cast<size_t>(frame_index);
        job.VideoTrackNo = 0;
        job.AbortDecode = false;
        job.OutputFrameMetadata = nullptr;
        job.Callback = decompress_callback;

        const size_t raw_need = R3DSDK::GpuDecoder::GetSizeBufferNeeded(job);
        if (raw_need == 0) {
            r3d_bridge_set_error("r3d metal decode: GetSizeBufferNeeded returned 0");
            return -4;
        }
        if (g_host_raw_bytes < raw_need) {
            if (g_host_raw) {
                std::free(g_host_raw);
            }
            g_host_raw = host_aligned(raw_need);
            g_host_raw_bytes = g_host_raw ? raw_need : 0;
            if (!g_host_raw) {
                r3d_bridge_set_error("r3d metal decode: host raw alloc failed");
                return -4;
            }
        }

        job.OutputBuffer = g_host_raw;
        job.OutputBufferSize = raw_need;

        DecompressWait wait;
        job.PrivateData = &wait;

        R3DSDK::DecodeStatus ds = g_gpu_decoder->DecodeForGpuSdk(job);
        if (ds != R3DSDK::DSDecodeOK) {
            r3d_bridge_set_error(
                std::string("GpuDecoder::DecodeForGpuSdk failed: ")
                + std::to_string(static_cast<int>(ds)));
            return static_cast<int>(ds);
        }
        {
            std::unique_lock<std::mutex> ul(wait.mu);
            wait.cv.wait(ul, [&] { return wait.done; });
            ds = wait.status;
        }
        if (ds != R3DSDK::DSDecodeOK) {
            r3d_bridge_set_error(
                std::string("GpuDecoder callback failed: ") + std::to_string(static_cast<int>(ds)));
            return static_cast<int>(ds);
        }

        if (!g_ip) {
            g_ip = new (std::nothrow) R3DSDK::ImageProcessingSettings();
            if (!g_ip) {
                r3d_bridge_set_error("r3d metal decode: IP alloc failed");
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

        R3DSDK::DebayerMetalJob *debayer = g_red_metal->createDebayerJob();
        if (!debayer) {
            r3d_bridge_set_error("r3d metal decode: createDebayerJob failed");
            return -5;
        }
        debayer->imageProcessingSettings = g_ip;
        debayer->mode = r3d_map_mode(decode_mode);
        debayer->pixelType = R3DSDK::PixelType_16Bit_RGB_Interleaved;
        debayer->raw_host_mem = g_host_raw;
        debayer->batchMode = false;
        debayer->debayerJobCallback = nullptr;
        debayer->output_device_image = nil;

        const size_t result_size = R3DSDK::DebayerMetalJob::ResultFrameSize(*debayer);
        if (result_size == 0) {
            g_red_metal->releaseDebayerJob(debayer);
            r3d_bridge_set_error("r3d metal decode: ResultFrameSize is 0");
            return -5;
        }

        g_raw_buf = ensure_mtl_buffer(g_raw_buf, raw_need);
        g_out_buf = ensure_mtl_buffer(g_out_buf, result_size);
        if (!g_raw_buf || !g_out_buf) {
            g_red_metal->releaseDebayerJob(debayer);
            r3d_bridge_set_error("r3d metal decode: MTLBuffer alloc failed");
            return -4;
        }

        std::memcpy([g_raw_buf contents], g_host_raw, raw_need);
        if (!g_unified) {
            [g_raw_buf didModifyRange:NSMakeRange(0, raw_need)];
        }

        debayer->raw_device_mem = g_raw_buf;
        debayer->output_device_mem = g_out_buf;
        debayer->output_device_mem_size = result_size;

        int err = 0;
        R3DSDK::REDMetal::Status pst = g_red_metal->process(g_queue, debayer, err);
        if (pst != R3DSDK::REDMetal::Status_Ok) {
            const int code = static_cast<int>(pst);
            g_red_metal->releaseDebayerJob(debayer);
            r3d_bridge_set_error(
                std::string("REDMetal::process failed status=") + std::to_string(code)
                + " err=" + std::to_string(err));
            return -6;
        }

        if (!g_unified) {
            id<MTLCommandBuffer> cmd = [g_queue commandBuffer];
            id<MTLBlitCommandEncoder> blit = [cmd blitCommandEncoder];
            [blit synchronizeResource:g_out_buf];
            [blit endEncoding];
            [cmd commit];
            [cmd waitUntilCompleted];
        }

        const uint16_t *src = static_cast<const uint16_t *>([g_out_buf contents]);
        const size_t packed = n_pix * 2U;
        if (result_size < packed) {
            g_red_metal->releaseDebayerJob(debayer);
            r3d_bridge_set_error("r3d metal decode: result smaller than packed RGB");
            return -6;
        }
        r3d_u16_to_f32(src, out_rgb_f32, n_pix);

        g_red_metal->releaseDebayerJob(debayer);
    }

    if (out_w)
        *out_w = static_cast<uint32_t>(w);
    if (out_h)
        *out_h = static_cast<uint32_t>(h);
    return 0;
}

} // namespace r3d_gpu
