//! Minimal QuickTime MOV writer for intra-only ProRes (ap4h / ap4x).
//!
//! Layout: `[ftyp][mdat][moov]` with moov-at-end. Sample entry is a plain
//! VisualSampleEntry plus an explicit `colr`/`nclc` BT.709 tag (oxideav docs
//! warn that unknown 4444 colour metadata can break some NLE decoders).

use std::fs::File;
use std::io::{self, Seek, SeekFrom, Write};
use std::path::Path;

pub struct ProResMovWriter {
    file: File,
    width: u32,
    height: u32,
    fps_num: u32,
    fps_den: u32,
    fourcc: [u8; 4],
    /// Absolute file offsets of each sample start.
    sample_offsets: Vec<u64>,
    sample_sizes: Vec<u32>,
    mdat_size_pos: u64,
    closed: bool,
}

impl ProResMovWriter {
    pub fn create(
        path: &Path,
        width: u32,
        height: u32,
        fps_num: u32,
        fps_den: u32,
        fourcc: [u8; 4],
    ) -> io::Result<Self> {
        if width == 0 || height == 0 || width > 65535 || height > 65535 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "width/height out of range",
            ));
        }
        if fps_num == 0 || fps_den == 0 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "fps numerator/denominator must be > 0",
            ));
        }
        if width % 2 != 0 || height % 2 != 0 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "ProRes requires even width and height",
            ));
        }

        let mut file = File::create(path)?;
        write_ftyp(&mut file)?;

        // mdat header: size placeholder (patched in finish), type 'mdat'.
        let mdat_size_pos = file.stream_position()?;
        file.write_all(&0u32.to_be_bytes())?;
        file.write_all(b"mdat")?;
        let _mdat_data_start = file.stream_position()?;

        Ok(Self {
            file,
            width,
            height,
            fps_num,
            fps_den,
            fourcc,
            sample_offsets: Vec::new(),
            sample_sizes: Vec::new(),
            mdat_size_pos,
            closed: false,
        })
    }

    pub fn write_sample(&mut self, data: &[u8]) -> io::Result<()> {
        if self.closed {
            return Err(io::Error::new(
                io::ErrorKind::Other,
                "writer already finished",
            ));
        }
        if data.is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "empty ProRes sample",
            ));
        }
        if data.len() > u32::MAX as usize {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "sample too large for stsz",
            ));
        }
        let off = self.file.stream_position()?;
        self.file.write_all(data)?;
        self.sample_offsets.push(off);
        self.sample_sizes.push(data.len() as u32);
        Ok(())
    }

    pub fn finish(&mut self) -> io::Result<()> {
        if self.closed {
            return Ok(());
        }
        if self.sample_sizes.is_empty() {
            // Allow close after a failed/cancelled job with zero frames.
            self.closed = true;
            return Ok(());
        }

        let mdat_end = self.file.stream_position()?;
        let mdat_size = mdat_end - self.mdat_size_pos;
        if mdat_size > u32::MAX as u64 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "mdat exceeds 4 GiB (co64 not implemented)",
            ));
        }
        self.file.seek(SeekFrom::Start(self.mdat_size_pos))?;
        self.file.write_all(&(mdat_size as u32).to_be_bytes())?;
        self.file.seek(SeekFrom::Start(mdat_end))?;

        write_moov(
            &mut self.file,
            self.width,
            self.height,
            self.fps_num,
            self.fps_den,
            self.fourcc,
            &self.sample_offsets,
            &self.sample_sizes,
        )?;
        self.file.flush()?;
        self.closed = true;
        Ok(())
    }
}

impl Drop for ProResMovWriter {
    fn drop(&mut self) {
        let _ = self.finish();
    }
}

fn write_ftyp(w: &mut impl Write) -> io::Result<()> {
    // major=qt  , minor=0, compatible=qt  
    let mut body = Vec::with_capacity(16);
    body.extend_from_slice(b"qt  ");
    body.extend_from_slice(&0u32.to_be_bytes());
    body.extend_from_slice(b"qt  ");
    write_box(w, b"ftyp", &body)
}

fn write_box(w: &mut impl Write, typ: &[u8; 4], body: &[u8]) -> io::Result<()> {
    let size = 8u64 + body.len() as u64;
    if size > u32::MAX as u64 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "box too large",
        ));
    }
    w.write_all(&(size as u32).to_be_bytes())?;
    w.write_all(typ)?;
    w.write_all(body)
}

#[allow(clippy::too_many_arguments)]
fn write_moov(
    w: &mut impl Write,
    width: u32,
    height: u32,
    fps_num: u32,
    fps_den: u32,
    fourcc: [u8; 4],
    sample_offsets: &[u64],
    sample_sizes: &[u32],
) -> io::Result<()> {
    let n = sample_sizes.len() as u32;
    let timescale = fps_num;
    let duration = n as u64 * u64::from(fps_den);

    let mut moov = Vec::new();
    {
        // mvhd v0
        let mut mvhd = Vec::with_capacity(100);
        mvhd.extend_from_slice(&0u32.to_be_bytes()); // creation
        mvhd.extend_from_slice(&0u32.to_be_bytes()); // modification
        mvhd.extend_from_slice(&timescale.to_be_bytes());
        mvhd.extend_from_slice(&(duration as u32).to_be_bytes());
        mvhd.extend_from_slice(&0x00010000u32.to_be_bytes()); // rate 1.0
        mvhd.extend_from_slice(&0x0100u16.to_be_bytes()); // volume
        mvhd.extend_from_slice(&[0u8; 10]); // reserved
        // unity matrix
        mvhd.extend_from_slice(&0x00010000u32.to_be_bytes());
        mvhd.extend_from_slice(&0u32.to_be_bytes());
        mvhd.extend_from_slice(&0u32.to_be_bytes());
        mvhd.extend_from_slice(&0u32.to_be_bytes());
        mvhd.extend_from_slice(&0x00010000u32.to_be_bytes());
        mvhd.extend_from_slice(&0u32.to_be_bytes());
        mvhd.extend_from_slice(&0u32.to_be_bytes());
        mvhd.extend_from_slice(&0u32.to_be_bytes());
        mvhd.extend_from_slice(&0x40000000u32.to_be_bytes());
        mvhd.extend_from_slice(&[0u8; 24]); // pre_defined
        mvhd.extend_from_slice(&2u32.to_be_bytes()); // next_track_ID
        append_full_box(&mut moov, b"mvhd", 0, 0, &mvhd);
    }

    let mut trak = Vec::new();
    {
        // tkhd v0, flags=0x000003 (enabled + in movie)
        let mut tkhd = Vec::with_capacity(84);
        tkhd.extend_from_slice(&0u32.to_be_bytes());
        tkhd.extend_from_slice(&0u32.to_be_bytes());
        tkhd.extend_from_slice(&1u32.to_be_bytes()); // track_ID
        tkhd.extend_from_slice(&0u32.to_be_bytes()); // reserved
        tkhd.extend_from_slice(&(duration as u32).to_be_bytes());
        tkhd.extend_from_slice(&[0u8; 8]); // reserved
        tkhd.extend_from_slice(&0u16.to_be_bytes()); // layer
        tkhd.extend_from_slice(&0u16.to_be_bytes()); // alternate_group
        tkhd.extend_from_slice(&0u16.to_be_bytes()); // volume (video)
        tkhd.extend_from_slice(&0u16.to_be_bytes()); // reserved
        tkhd.extend_from_slice(&0x00010000u32.to_be_bytes());
        tkhd.extend_from_slice(&0u32.to_be_bytes());
        tkhd.extend_from_slice(&0u32.to_be_bytes());
        tkhd.extend_from_slice(&0u32.to_be_bytes());
        tkhd.extend_from_slice(&0x00010000u32.to_be_bytes());
        tkhd.extend_from_slice(&0u32.to_be_bytes());
        tkhd.extend_from_slice(&0u32.to_be_bytes());
        tkhd.extend_from_slice(&0u32.to_be_bytes());
        tkhd.extend_from_slice(&0x40000000u32.to_be_bytes());
        // width/height as 16.16
        tkhd.extend_from_slice(&(width << 16).to_be_bytes());
        tkhd.extend_from_slice(&(height << 16).to_be_bytes());
        append_full_box(&mut trak, b"tkhd", 0, 0x000003, &tkhd);
    }

    let mut mdia = Vec::new();
    {
        let mut mdhd = Vec::with_capacity(24);
        mdhd.extend_from_slice(&0u32.to_be_bytes());
        mdhd.extend_from_slice(&0u32.to_be_bytes());
        mdhd.extend_from_slice(&timescale.to_be_bytes());
        mdhd.extend_from_slice(&(duration as u32).to_be_bytes());
        // language = 'und' → 0x55C4, quality = 0
        mdhd.extend_from_slice(&0x55C4u16.to_be_bytes());
        mdhd.extend_from_slice(&0u16.to_be_bytes());
        append_full_box(&mut mdia, b"mdhd", 0, 0, &mdhd);
    }
    {
        // hdlr
        let mut hdlr = Vec::new();
        hdlr.extend_from_slice(&0u32.to_be_bytes()); // pre_defined
        hdlr.extend_from_slice(b"vide");
        hdlr.extend_from_slice(&[0u8; 12]);
        hdlr.extend_from_slice(b"OxideAV ProRes\0");
        append_full_box(&mut mdia, b"hdlr", 0, 0, &hdlr);
    }

    let mut minf = Vec::new();
    {
        // vmhd
        let mut vmhd = Vec::new();
        vmhd.extend_from_slice(&0u16.to_be_bytes()); // graphicsmode
        vmhd.extend_from_slice(&[0u8; 6]); // opcolor
        append_full_box(&mut minf, b"vmhd", 0, 1, &vmhd);
    }
    {
        // dinf / dref / url  (flag=1 → media in same file)
        let mut dref_content = Vec::new();
        dref_content.extend_from_slice(&1u32.to_be_bytes());
        append_full_box_to(&mut dref_content, b"url ", 0, 1, &[]);
        let mut dinf = Vec::new();
        append_full_box(&mut dinf, b"dref", 0, 0, &dref_content);
        append_box(&mut minf, b"dinf", &dinf);
    }

    let mut stbl = Vec::new();
    {
        // stsd
        let mut stsd_content = Vec::new();
        stsd_content.extend_from_slice(&1u32.to_be_bytes()); // entry_count
        let entry = prores_sample_entry(width, height, fourcc);
        stsd_content.extend_from_slice(&entry);
        append_full_box(&mut stbl, b"stsd", 0, 0, &stsd_content);
    }
    {
        // stts — one run, n samples, delta = fps_den (timescale = fps_num)
        let mut stts = Vec::new();
        stts.extend_from_slice(&1u32.to_be_bytes());
        stts.extend_from_slice(&n.to_be_bytes());
        stts.extend_from_slice(&fps_den.to_be_bytes());
        append_full_box(&mut stbl, b"stts", 0, 0, &stts);
    }
    {
        // stsc — one chunk, all samples
        let mut stsc = Vec::new();
        stsc.extend_from_slice(&1u32.to_be_bytes());
        stsc.extend_from_slice(&1u32.to_be_bytes()); // first_chunk
        stsc.extend_from_slice(&n.to_be_bytes()); // samples_per_chunk
        stsc.extend_from_slice(&1u32.to_be_bytes()); // sample_description_index
        append_full_box(&mut stbl, b"stsc", 0, 0, &stsc);
    }
    {
        // stsz
        let mut stsz = Vec::new();
        stsz.extend_from_slice(&0u32.to_be_bytes()); // sample_size = 0 → table
        stsz.extend_from_slice(&n.to_be_bytes());
        for s in sample_sizes {
            stsz.extend_from_slice(&s.to_be_bytes());
        }
        append_full_box(&mut stbl, b"stsz", 0, 0, &stsz);
    }
    {
        // stco — single chunk at first sample offset
        let mut stco = Vec::new();
        stco.extend_from_slice(&1u32.to_be_bytes());
        let chunk_off = sample_offsets[0];
        if chunk_off > u32::MAX as u64 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "chunk offset exceeds 4 GiB",
            ));
        }
        stco.extend_from_slice(&(chunk_off as u32).to_be_bytes());
        append_full_box(&mut stbl, b"stco", 0, 0, &stco);
    }
    // stss — all samples are sync (ProRes intra)
    {
        let mut stss = Vec::new();
        stss.extend_from_slice(&n.to_be_bytes());
        for i in 1..=n {
            stss.extend_from_slice(&i.to_be_bytes());
        }
        append_full_box(&mut stbl, b"stss", 0, 0, &stss);
    }

    append_box(&mut minf, b"stbl", &stbl);
    append_box(&mut mdia, b"minf", &minf);
    append_box(&mut trak, b"mdia", &mdia);
    append_box(&mut moov, b"trak", &trak);
    write_box(w, b"moov", &moov)
}

fn prores_sample_entry(width: u32, height: u32, fourcc: [u8; 4]) -> Vec<u8> {
    // [size][fourcc][VisualSampleEntry 78][colr]
    let mut visual = [0u8; 78];
    visual[6] = 0;
    visual[7] = 1; // data_reference_index
    visual[24..26].copy_from_slice(&(width as u16).to_be_bytes());
    visual[26..28].copy_from_slice(&(height as u16).to_be_bytes());
    let dpi = 72u32 << 16;
    visual[28..32].copy_from_slice(&dpi.to_be_bytes());
    visual[32..36].copy_from_slice(&dpi.to_be_bytes());
    visual[40..42].copy_from_slice(&1u16.to_be_bytes()); // frame_count
    // Pascal compressor name
    let name = b"Apple ProRes";
    visual[42] = name.len() as u8;
    visual[43..43 + name.len()].copy_from_slice(name);
    // depth: 24 (no alpha). Apple often uses 32 for 4444+alpha.
    visual[74..76].copy_from_slice(&0x0018u16.to_be_bytes());
    visual[76..78].copy_from_slice(&(-1i16).to_be_bytes());

    // colr / nclc — BT.709 (primaries=1, transfer=1, matrix=1)
    let mut colr = Vec::with_capacity(18);
    colr.extend_from_slice(b"nclc");
    colr.extend_from_slice(&1u16.to_be_bytes());
    colr.extend_from_slice(&1u16.to_be_bytes());
    colr.extend_from_slice(&1u16.to_be_bytes());
    let colr_box = make_box(b"colr", &colr);

    let body_len = visual.len() + colr_box.len();
    let size = 8 + body_len;
    let mut out = Vec::with_capacity(size);
    out.extend_from_slice(&(size as u32).to_be_bytes());
    out.extend_from_slice(&fourcc);
    out.extend_from_slice(&visual);
    out.extend_from_slice(&colr_box);
    out
}

fn make_box(typ: &[u8; 4], body: &[u8]) -> Vec<u8> {
    let size = 8 + body.len();
    let mut out = Vec::with_capacity(size);
    out.extend_from_slice(&(size as u32).to_be_bytes());
    out.extend_from_slice(typ);
    out.extend_from_slice(body);
    out
}

fn append_box(buf: &mut Vec<u8>, typ: &[u8; 4], body: &[u8]) {
    buf.extend_from_slice(&make_box(typ, body));
}

fn append_full_box(buf: &mut Vec<u8>, typ: &[u8; 4], version: u8, flags: u32, body: &[u8]) {
    let mut full = Vec::with_capacity(4 + body.len());
    full.push(version);
    full.push(((flags >> 16) & 0xff) as u8);
    full.push(((flags >> 8) & 0xff) as u8);
    full.push((flags & 0xff) as u8);
    full.extend_from_slice(body);
    append_box(buf, typ, &full);
}

fn append_full_box_to(buf: &mut Vec<u8>, typ: &[u8; 4], version: u8, flags: u32, body: &[u8]) {
    append_full_box(buf, typ, version, flags, body);
}
