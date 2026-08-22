//! Full-range RGB48 → limited-range BT.709 YUV444P12 (planar LE u16).

/// Convert packed RGB48 (H×W×3, full-range 0..=65535) to planar YUV444P12Le
/// (low 12 bits significant, limited / video range).
///
/// Matrix is BT.709. Limited range matches typical ProRes carriage:
/// Y ∈ \[256, 3760\], Cb/Cr centred at 2048 for 12-bit.
pub fn rgb48_to_yuv444_p12_le(rgb: &[u16], width: usize, height: usize) -> (Vec<u8>, Vec<u8>, Vec<u8>) {
    let n = width * height;
    debug_assert_eq!(rgb.len(), n * 3);

    let mut y_plane = vec![0u8; n * 2];
    let mut cb_plane = vec![0u8; n * 2];
    let mut cr_plane = vec![0u8; n * 2];

    // BT.709 luma / chroma (R,G,B in 0..1).
    const KR: f64 = 0.2126;
    const KB: f64 = 0.0722;
    const KG: f64 = 1.0 - KR - KB; // 0.7152
    // Limited-range scale for 12-bit (8-bit 16/235/128 × 16).
    const Y_SCALE: f64 = 219.0 * 16.0;
    const Y_OFFSET: f64 = 16.0 * 16.0;
    const C_SCALE: f64 = 224.0 * 16.0;
    const C_OFFSET: f64 = 128.0 * 16.0;

    for i in 0..n {
        let r = f64::from(rgb[i * 3]) / 65535.0;
        let g = f64::from(rgb[i * 3 + 1]) / 65535.0;
        let b = f64::from(rgb[i * 3 + 2]) / 65535.0;

        let y_p = KR * r + KG * g + KB * b;
        let cb_p = (b - y_p) / 1.8556;
        let cr_p = (r - y_p) / 1.5748;

        let y = (Y_OFFSET + Y_SCALE * y_p).round().clamp(0.0, 4095.0) as u16;
        let cb = (C_OFFSET + C_SCALE * cb_p).round().clamp(0.0, 4095.0) as u16;
        let cr = (C_OFFSET + C_SCALE * cr_p).round().clamp(0.0, 4095.0) as u16;

        let o = i * 2;
        y_plane[o..o + 2].copy_from_slice(&y.to_le_bytes());
        cb_plane[o..o + 2].copy_from_slice(&cb.to_le_bytes());
        cr_plane[o..o + 2].copy_from_slice(&cr.to_le_bytes());
    }

    (y_plane, cb_plane, cr_plane)
}
