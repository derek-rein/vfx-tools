/* SPDX-License-Identifier: MIT
 *
 * Optional GPU decode:
 *   macOS        — Metal (GpuDecoder + REDMetal) in r3d_bridge_metal.mm
 *   Linux/Windows — R3DDecoder (CUDA, then OpenCL) in r3d_bridge_decoder.cpp
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace r3d_gpu {

bool force_cpu();

bool startup();

void shutdown();

bool available();

/* True only if GPU decode is already running (does not start it). */
bool ready();

/* "metal", "cuda", "opencl", or empty if GPU is not running. */
const char *kind();

/* *clip* is an R3DSDK::Clip*. */
bool clip_supported(void *clip);

/* Decode into float32 RGB. Returns 0 on success; non-zero to fall back to CPU. */
int decode_frame(
    void *clip,
    uint32_t frame_index,
    int decode_mode,
    int pipeline,
    float *out_rgb_f32,
    size_t out_rgb_bytes,
    uint32_t *out_w,
    uint32_t *out_h);

} // namespace r3d_gpu
