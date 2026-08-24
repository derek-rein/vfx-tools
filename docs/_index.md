---
title: Documentation
---

Guides for running and integrating **EXR Converter**. Edit these files under
`docs/` in the repo — the site is generated with Hugo (`site/`) and published to
[GitHub Pages](https://derek-rein.github.io/exr-converter/).

When you change CLI flags, GUI behavior, codecs, OCIO defaults, packaging that
users see, or integrations, update the matching guide here in the **same** PR
as the code (and [CHANGELOG.md](../CHANGELOG.md)). See
[AGENTS.md — Documentation](../AGENTS.md#documentation-required).

## Guides

| Guide | Topics |
|-------|--------|
| [CLI](./cli.md) | `video2exr`, `exr2video`, GUI launch flags |
| [GUI](./gui.md) | Tabs, browsers, overlays, preferences, post-convert |
| [Nuke](./nuke.md) | Nuke menu integration |
| [R3D / N-RAW](./r3d.md) | Optional RED R3D SDK (proprietary; build + license) |
| [Releasing](./releasing.md) | Pointer to maintainer release process |
| [12-bit ProRes (oxideav)](./plan-12bit-prores-oxideav.md) | Experimental RDD-36 12-bit ProRes (release builds) |
