# ByAuthorX logo companion library

24 additional designs and 44 variants made with Vixl, using the actual supplied ByAuthorX logo geometry. The green pen loop, irregular red ink mark and its small fleck, warm manuscript paper, and slightly uneven editorial lines give these assets a closer relationship to the logo.

Open **[index.html](index.html)** to browse the collection. **[contact-sheet.jpg](contact-sheet.jpg)** shows all 24 designs. Asset links open/save files; when browsing locally, media may open in a new tab where you can use Save As. Search, theme filters, and motion controls work without a server or network access.

| Collection | Designs | Variants |
| --- | ---: | ---: |
| Writing, annotation, book, privacy, revision, and export illustrations | 8 | 16 |
| Four waiting loops and two short confirmation/reveal animations | 6 | 12 |
| Pen gestures, margin details, chapter stop, author seal, and emphasis mark | 6 | 12 |
| Editorial backgrounds with open copy space | 4 | 4 |

Illustrations, animations, and decorations have light/dark variants. Backgrounds include three paper compositions and one dark composition. This is an additional shelf beside the [first creative library](../creative-library/README.md).

## How the collection matches the logo

The generator reads the canonical [brand tokens](../../src/byax/resources/brand/tokens.json): paper `#F4EEE3`, ink `#252927`, pen green `#367B6C`, and ink red `#AD283E`. The supplied green path and red blot/fleck are reused directly from editable vector masters; the author seal also places the supplied X/loop mark. The red blot remains irregular, with its small fleck. Logo paths retain their original proportions. Light/dark companions adapt neutral page fills and outlines while preserving the approved green and red.

These are illustrations and supporting graphics. The existing wordmark and app icons remain the logo family. Use the supplied logos for official identification, and preserve their required clear space. Keep decorative artwork outside actual document content unless an author explicitly chooses to insert it. The catalog uses the supplied IM FELL English and Manrope fonts with their bundled OFL notices.

## Ready-to-use files

Every variant includes an editable `.vixl` master, JSON operation recipe, true vector SVG, and transparent PNG. Illustration PNGs are 640 × 480 with 1280 × 960 `@2x` versions. Decorations include native-size and `@2x` PNGs. The vertical margin gesture has a 320 × 560 canvas; other decorations use 640 × 320. Backgrounds are opaque 1600 × 900 PNGs/SVGs with room for future copy.

Animations include transparent WebP and APNG, theme-matted GIF, a static SVG/PNG alternative, and a sampled contact sheet. The ink-blot loader is 112 × 56; the other animations are 320 × 224. Static posters show the resting/completed pose, so they are useful for reduced motion.

| Motion asset | Timing | Playback | Use |
| --- | --- | --- | --- |
| `ink-blot-loader` | 2.4 s | Loop | Compact indeterminate loading from the logo's ink mark |
| `pen-loop-wait` | 2.4 s | Loop | Gentle vertical motion of the original green loop |
| `reading-breath` | 2.4 s | Loop | Quiet book/document waiting illustration |
| `margin-review` | 2.4 s | Loop | Review activity along a document margin |
| `saved-ink` | 180 ms | Once | Explicit save confirmation; hold the final frame |
| `signature-settle` | 180 ms | Once | Pen-gesture reveal; hold the final frame |

The two finite animations use the canonical 180 ms motion duration with ease-out timing. The four loops are optional assets for an actual waiting task or deliberate marketing placement. They are not installed as ambient app animations. Stop waiting motion when its task finishes or its surface becomes hidden, and provide a still alternative under reduced motion. The catalog respects system reduced-motion requests and offers a manual pause button.

For native Qt, PNGs work with `QPixmap`; the theme-matted GIF alternatives work with a suitable `QMovie` player. Transparent WebP/APNG playback depends on the installed player/plugins. Match the GIF background to paper `#F4EEE3` or ink `#252927`. For web use, transparent WebP preserves smooth edges; choose SVG for scalable still artwork. Meaningful app status still needs a readable text label.

## Edit and regenerate

The pinned Vixl authoring dependency matches the first library:

```bash
python -m venv .venv-assets
.venv-assets/bin/python -m pip install -r assets/logo-companion-library/requirements.txt
.venv-assets/bin/python scripts/build_logo_companions.py
```

On Windows, use `.venv-assets\Scripts\python.exe` after creating the environment. Use `--only ink-blot-loader --out /path/to/proof` for a single design. A full rebuild overwrites generated outputs. Save hand-edited masters elsewhere first.

The source pen-line, ink-dot, and app-mark masters, canonical tokens, and approved wordmark previews are also included in `reference/`. `manifest.json` records source paths and SHA-256 hashes. They make the pack self-contained for editing and provenance; they are not extra new designs. Font files and their licenses live in `fonts/`.

The build uses Vixl 0.26.0 at `afaee339bfc2fa7959c8b6399dc0740f6023bc86`. As in the first library, one-play export metadata is normalized without recompressing pixels. Reexporting a finite animation directly from Vixl should include the same normalization or a host-player one-play limit.

The delivered `validation.json` records master, vector, image, animation, and catalog checks; the manifest retains Vixl bounds/motion/composition and asset-contract results. `checksums.json` covers the delivered files. Artwork follows the repository's [MIT license](../../LICENSE); fonts retain their included OFL notices. Vixl remains a separate authoring tool under its own license. No Vixl engine code is added to the app runtime.
