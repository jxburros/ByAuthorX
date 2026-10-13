# ByAuthorX creative library

54 original designs, with 94 variants, made with [Vixl](https://github.com/jxburros/Vixl). This is a reserve of reusable artwork for future app surfaces and marketing. The app's deep teal, warm paper, sage, and gold palette ties the collection together. No campaign wording is baked into the artwork.

Open **[index.html](index.html)** to browse, search, filter by collection or theme, pause motion, and open/save individual files. It works locally without a server, network requests, or dependencies. Browsers may open media links in a new tab when browsing local files; use Save As there. When served over HTTP, the same links download directly. **[contact-sheet.jpg](contact-sheet.jpg)** shows every design at a glance. **[manifest.json](manifest.json)** lists paths, dimensions, themes, usage ideas, timing, and validation results.

| Collection | Designs | Variants | Contents |
| --- | ---: | ---: | --- |
| Illustrations | 12 | 24 | Writing desk, blank page, story garden, document library, review, dictation, privacy, export, focus, ideas, outline, celebration |
| Icons | 18 | 36 | Documents, writing, navigation, review, status, and utility symbols |
| Animations | 10 | 20 | Eight gentle loops; two completion animations that play once |
| Decorations | 8 | 8 | Botanical sprig, flourish, sparks, ink swoosh, laurels, divider, bookmark tab, confetti |
| Backgrounds | 6 | 6 | 1600 × 900 compositions with open space on the left for future copy |

Illustrations, icons, and animations have light and dark variants. Decorations are supplied in the core palette; recolor their editable vector layers as needed. Backgrounds include both light and dark compositions.

## Pick the right file

Every variant includes a layered `.vixl` master, a JSON operation recipe, a true vector SVG, and a PNG. SVGs contain paths and shapes, with no embedded raster artwork or external assets.

- **Illustrations:** transparent 640 × 480 PNGs and 1280 × 960 `@2x` PNGs.
- **Icons:** transparent PNGs at 32, 64, 128, and 256 px. The SVG scales to other sizes. Match the theme to the surrounding UI.
- **Decorations:** transparent 640 × 320 PNGs, 1280 × 640 `@2x` PNGs, and SVGs.
- **Backgrounds:** opaque 1600 × 900 PNGs and SVGs. Keep future headlines in the left portion; review contrast after adding copy.
- **Animations:** transparent WebP and APNG at 24 fps, theme-matted GIF alternatives, a static SVG/PNG, and a sampled contact sheet. The compact `ink-dots` loader is 128 × 48; the other animations are 256 × 256. `ink-dots`, `quill-writing`, and `orbit-spark` also include PNG frame ZIPs with timing metadata for custom players.

The artwork has padding around the visible motif. For small loading indicators, size the canvas with that padding in mind, or crop the transparent margin before integration. SVG/PNG files beside animations are static posters; the SVG files do not animate.

## Motion shelf

| Asset | Duration | Playback | Suggested use |
| --- | ---: | --- | --- |
| `ink-dots` | 1.6 s | Loop | Compact indeterminate loading |
| `quill-writing` | 1.6 s | Loop | Drafting or text processing |
| `page-breathe` | 1.6 s | Loop | Opening or retrieving a document |
| `orbit-spark` | 1.6 s | Loop | Background processing |
| `voice-wave` | 1.6 s | Loop | Dictation activity; illustrative, not a live level meter |
| `review-scan` | 1.6 s | Loop | Reviewing or checking a document |
| `book-idle` | 2.4 s | Loop | Gentle waiting in a larger panel |
| `thinking-spark` | 2.4 s | Loop | Ideas or optional assistance |
| `success-bloom` | 1.2 s | Once | Completion; hold the final frame |
| `saved-stamp` | 1.2 s | Once | Save confirmation; hold the final frame |

Use the matching `*.png` when reduced motion is enabled. For loops the PNG shows the initial pose; for completion animations it shows the completed pose. The catalog follows the operating system's reduced-motion preference and has a manual pause control. A host app must make its own choice of reduced-motion setting and player behavior.

**Qt desktop:** PNGs work with `QPixmap`. GIFs are a practical `QMovie` option; they have a warm-paper or dark-teal matte, so match the widget background. APNG/WebP playback depends on the installed image plugins/player; retain the GIF or PNG alternatives. Stop any loading animation when its task finishes or its widget is hidden. Pair meaningful status indicators with readable text; these assets carry no accessible status message themselves.

**Web/marketing:** animated WebP is compact and preserves smooth alpha; APNG preserves full pixel color. Use the SVG for scalable static artwork. GIF is a compatibility option and has limited colors and an opaque theme matte. For a browser implementation, swap the animated source for the static PNG under `prefers-reduced-motion: reduce`, as the catalog does.

## Edit and regenerate

Use an isolated authoring environment; Vixl is not added to the application's runtime dependencies:

```bash
python -m venv .venv-assets
.venv-assets/bin/python -m pip install -r assets/creative-library/requirements.txt
.venv-assets/bin/python scripts/build_creative_library.py
```

On Windows, use `.venv-assets\Scripts\python.exe` for both commands after creating the environment. The generator is offline after installation and needs no AI credentials. It overwrites its generated files; put hand-edited masters in a separate folder before rebuilding.

For one design, use `--only ink-dots --out /path/to/proof`. A partial build produces that design's files and review JSON; the full build produces the catalog, manifest, contact sheet, and checksums. Customize palette roles in `PALETTES`, or adjust named shapes and motion tracks in the `.vixl` master. Each recipe's `canvas` and `operations` can also be replayed through Vixl's Python API.

The engine is Vixl 0.26.0 at commit `afaee339bfc2fa7959c8b6399dc0740f6023bc86`. Vixl's exporters interpret loop counts as additional repeats, so the generator normalizes completion-file playback metadata to exactly one play without recompressing pixels. Reexporting those masters directly should include the same normalization or an explicit host-player one-play limit.

## Validation and provenance

The generator runs Vixl bounds, motion, and composition checks plus a contract requiring editable shapes and no text layers. Results and any review notes remain in `manifest.json`; the deliverable verification report is in `validation.json`. `checksums.json` records SHA-256 hashes of the generated pack. The browser catalog is also checked for filters, downloads, and reduced-motion behavior.

Original artwork is covered by the repository's [MIT license](../../LICENSE). Vixl remains a separately licensed authoring tool under its [PolyForm Small Business license](https://github.com/jxburros/Vixl/blob/afaee339bfc2fa7959c8b6399dc0740f6023bc86/LICENSE); its code is not bundled into this asset pack. The contact sheet uses the repository's existing DejaVu font. No stock illustrations, provider-generated imagery, or Vixl emoji artwork are included.
