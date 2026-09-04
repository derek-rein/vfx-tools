/* SPDX-License-Identifier: MIT
 *
 * Shared helpers for the R3D C ABI bridge (CPU + optional Metal).
 * Not installed; not a public API.
 */
#pragma once

#include "r3d_bridge.h"

#include "R3DSDK.h"

#include <cstddef>
#include <cstdint>
#include <string>

void r3d_bridge_set_error(const std::string &msg);

/* True when EXR_CONVERTER_R3D_CPU is set to a non-empty / non-zero value. */
bool r3d_force_cpu();

R3DSDK::VideoDecodeMode r3d_map_mode(int mode);

void r3d_dims_for_mode(size_t full_w, size_t full_h, int mode, size_t &w, size_t &h);

void r3d_apply_pipeline(R3DSDK::Clip *clip, R3DSDK::ImageProcessingSettings &ip, int pipeline);

void r3d_u16_to_f32(const uint16_t *src, float *dst, size_t n);
