# ByAuthorX identity and native motion

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/wordmark-dark.svg">
  <img src="images/wordmark-light.svg" alt="ByAuthorX" width="360">
</picture>

**Your words. Your call.** The supplied brand guide describes a working manuscript: warm, useful, and calm. Expressive marks introduce the app; familiar controls frame the author's writing.

![Native writing view with branded chrome](images/editor.png)

## Tokens and typography

[`src/byax/resources/brand/tokens.json`](../src/byax/resources/brand/tokens.json) is the canonical runtime source. `byax.brand` reads it for the application palette, stylesheet, font roles, and animation duration. The original kit's JSON/CSS remain reference copies. Do not change the approved logo colors when adjusting neutral UI surfaces.

| Role | Value | Use |
| --- | --- | --- |
| Paper | `#F4EEE3` | Chrome and preview surround |
| Ink | `#252927` | Main text |
| Pen green | `#367B6C` | Primary buttons and supplied gestures |
| Ink red | `#AD283E` | Signature artwork; errors also need explicit labels |
| Surface | `#FFFCF7` | Menus, controls, and inspector surfaces |
| Rule | `#D7D0C3` | Quiet separators |
| Muted | `#52615B` | Secondary labels |
| Deep green | `#246052` | Small green text, selections, and focus rings |

Controls use a 6 px radius; panels use 8 px. Layout uses the supplied 4/8 px rhythm. A 2 px deep-green focus border identifies keyboard focus. Primary green buttons carry white text. Color never replaces the control label.

The native workspace keeps a professional control layer: Irongall labels and controls, restrained toolbar buttons, and a white writing surface within warm-paper margins. The document header reports its title, save state, and current view. Outline and Inspector start at 230 and 300 px and remain resizable. Selected paragraph styles have readable labels while retaining canonical engine keys.

When Review has no active items, a small static illustration from the logo companion library accompanies a clear explanation and a local-checks action. Real items replace that empty state; reply/accept/reject controls enable only when the selected item supports them. Interface artwork stays outside authored content and exports.

Other empty states use companion stills in the same way, always beside a readable heading and explanation:

| Surface | Companion design | Runtime file (`ui/`) |
| --- | --- | --- |
| Outline with no headings | `loop-bound-book` | `outline-empty-{light,dark}.png` |
| Layers with no layers | `revision-pages` | `layers-empty-{light,dark}.png` |
| AI chat before the first message | `idea-on-paper` | `ai-empty-{light,dark}.png` |
| AI chat with no provider configured | `local-copy` | `ai-setup-{light,dark}.png` |
| Spelling check with no misspellings | `signature-manuscript` | `spelling-clean-{light,dark}.png` |

`gui_art.themed_art()` chooses the light or dark variant from the effective window palette (window lightness below 50% selects dark). The current application palette is light, so dark variants are dormant until a dark palette ships.

Dialogs and results use a second set of illustrations from the [logo UI library](../assets/logo-ui-library/README.md), again beside a readable heading and explanation:

| Surface | Design | Runtime file (`ui/`) |
| --- | --- | --- |
| Document checks finished with nothing flagged | `checks-clear` | `checks-clear-{light,dark}.png` |
| Document checks found items | `proofread-marks` | `proofread-marks-{light,dark}.png` |
| Export finished | `export-ready` | `export-ready-{light,dark}.png` |
| Background jobs, empty queue | `jobs-empty` | `jobs-empty-{light,dark}.png` |
| Dictation dialog | `dictation-ready` | `dictation-ready-{light,dark}.png` |
| Provider settings | `provider-keys` | `provider-keys-{light,dark}.png` |

A result dialog shows its illustration only when the result proves what the picture says. `checks-clear` appears for a passed check with no findings; a check that could not measure everything shows no artwork, so the application never claims a document is clean without evidence.

![Hand-inked marks, icons and chrome in the writing view](images/editor-marks.png)

## Interface icons

The toolbar and menus carry a hand-inked icon set drawn in Vixl from the logo's own pen line, ink blot and mark (61 designs; [see the library](../assets/logo-ui-library/README.md)). Ink draws the object, one pen-green gesture draws the action, and ink red appears only at a point of origin (the start of a comment). Strokes are 6 units on a 96-unit grid, so they stay legible at 16 px.

Icons always accompany a label or an accessible name. Comment, Check and Pages show icon and text in the toolbar. Bold, Italic and Underline are icon-only because their glyphs are the letters themselves, and keep their name, shortcut and tooltip. Menus draw icons at 20 px for actions that have an established image; other actions stay text-only rather than inventing a symbol. About uses the logo's own micro mark from the application icon, never a redraw.

`gui_icons.themed_icon(name, widget)` slices the baked atlases (16, 24, 32 and 48 px, rendered from the vectors, not resampled) into a `QIcon`, so every logical size has an exact pixmap on ordinary and 2x displays, and chooses light or dark from the effective palette like the stills. Pressed and checked toolbar buttons use a paper background with a deep-green border instead of a solid green block, so an ink icon never sits on dark green.

## Interface type

Each kind of interface text has a font role in `tokens.json`, and `byax.brand.stylesheet()` applies them:

| Role (`font.*`) | Family | Where it sets text |
| --- | --- | --- |
| `body` | Irongall | The application font: menus, toolbar buttons, labels, inputs, tabs and the status message (11 pt by default) |
| `display` | Irongall | The same face at larger sizes: the About headline, the document title, dock titles (Outline, Inspector) and empty-state headings |
| `fallback` | Manrope Regular/Semibold | Any character Irongall lacks (Cyrillic, Greek, Latin Extended-B), and every label under a high-contrast theme |
| `annotation` | Scholium | Helper text under an empty-state heading and the AI tab's status and hint lines |
| `counter` | Onionskin | The word, character and reading-time counters in the status bar |
| `reading` | Source Serif 4 | Registered and selectable for reading and document styles |

Irongall is the application font because it is the wordmark's own serif: its sturdy strokes hold up at 11 pt, and it makes every label read as part of the same hand as the logo. Scholium is drawn from the illustrations and is set at 16 px in ink colour because its pen line is thin, and Onionskin's fixed pitch keeps the counters steady as they change. The brand faces cover Latin only, so the application font and every role list Manrope after their own family: a character they lack is set in Manrope rather than in whatever the system picks. Under a platform high-contrast theme the stylesheet is dropped and the application font becomes Manrope alone, because a plain face reads best for readers who asked for contrast. Tenuis, Brobdingnag and Decussate are bundled and selectable in documents but the interface does not use them: a hairline, an ultra-black slab and a stitched face are display faces for artwork. The fonts are drawn and compiled by `scripts/build_brand_fonts.py`; its `--install-only` step copies the built files into `src/byax/resources/fonts`, where `register_bundled_fonts()` registers every `*.ttf` at startup and `bundled_font_path()` fingerprints them for font identity, embedding and reproduction records.

Manrope, IM FELL English and Source Serif 4 are bundled with their SIL OFL notices, and the six brand fonts are original ByAuthorX designs under the project's MIT license. Existing document styles, content, revision history, and export typography are preserved; branding does not inject decoration into authored documents. The writing page stays white.

Six custom display faces drawn with Vixl live in [assets/brand-fonts](../assets/brand-fonts/README.md): Irongall (from the wordmark), Scholium (from the companion illustrations), Tenuis (tall and thin), Brobdingnag (big and bold), Onionskin (a typewriter face) and Decussate (every letter stitched from X marks). They have editable Vixl glyph masters and checked specimens, and are bundled with the application as described above; document typography is unchanged.

## Logos and platform icons

The full light wordmark appears at 240 px in the writing toolbar, with 20 px surrounding clearance, and at 360 px in About. Preserve aspect ratio, the loop, the rough terminal, and at least half a capital B's height of clear space. The approved name is outlined artwork; do not retype or redraw it as a substitute.

Qt's SVG Tiny renderer skips nested SVG elements in these supplied exports. Native widgets therefore draw the supplied 960 px transparent wordmark PNG at its intended logical size, preserving fidelity through common display scales. Original SVG and Vixl masters remain available for design and documentation. The README and this page use light/dark SVG variants via `picture`.

The Qt application icon uses the simplified micro mark at 16–24 px and the X/loop mark at larger sizes. The runtime contains PNG sizes through 1024 px. Windows executable/installer icons use the supplied multiresolution ICO. Linux packaging installs the scalable transparent master plus matching hicolor PNG sizes. Avatars and social previews are available in `design/brand/social`; website manifests and duplicate raster export families are not part of the native desktop build.

## Motion contract

| Trigger | Motion | Completion |
| --- | --- | --- |
| Writing/pages switch | New view fades from 35% to full opacity | 180 ms, ease out |
| Inspector tab change or dock reveal | Panel content fades in | 180 ms, ease out |
| Background worker or durable job | Small green pen loop writes, holds, and erases beside the task label | 2.4 s loop, only while work is pending |
| Explicit successful save | Small checkmark draws beside “Saved locally” | 180 ms, then holds briefly and hides |
| About opens | Original green signature stroke writes itself once | 180 ms, ease out |
| AI assistant request in flight | Red ink blot (`ink-blot-loader`) breathes beside “… is thinking” in the AI tab | 2.4 s loop, only while the request is pending |
| Spelling check running | Green margin gesture (`margin-review`) writes and erases beside **Check spelling** | 2.4 s loop, only while the check runs |
| Document checks running | Ticks write beside each line of a page (`checking-page`), replacing the still in the empty Review panel | 2.4 s loop, only while checks run; same size as the still, so nothing moves |
| Dictation recording | The pen line becomes the voice (`listening-wave`), replacing the still in the Dictation dialog | 2.4 s loop, only while audio is recording; pause, stop, failure and closing the dialog all end it |
| A proofreader's mark appears | The stroke writes itself left to right | 180 ms, ease out; instant under reduced motion; only for marks not already shown |

Qt `QPropertyAnimation` uses `OutCubic` for interface fades. Vixl-authored artwork uses a linear playback clock because its stroke timing is already baked. About and save feedback include the exact 180 ms endpoint; their original centerlines, proportions, stroke widths, and colors are preserved. Finished fade effects are removed. Rapid view switches settle at full visibility. Typing, autosave, and idle time do not trigger artwork.

Busy artwork appears after a 200 ms delay to avoid flashing on quick tasks. A reserved status-bar slot prevents layout shifts. Overlapping workers/jobs keep one indicator active until the last task finishes; completion, cancellation, uncertainty, failure, and polling errors clear the corresponding activity. The loop stops when hidden or minimized. Explicit save confirmation never interrupts a running task, and its artwork hides after 1.2 s.

The AI tab's two loops are `gui_art.TaskLoop` widgets owned by a single task: `start()` shows and plays, `stop()` hides. They stop while hidden or minimized, never run as ambient decoration, and under reduced motion draw the delivered still instead (the fully written hold pose at 1.2 s). The status-bar pen loop still reports the same work globally; the tab-local loop shows where the result will appear. The existing `saved-ink` checkmark remains the save confirmation; the companion shelf's `saved-ink` and `pen-loop-wait` are already the sources for those profiles.

Native previews show [work in progress](images/editor-busy.png), [explicit save confirmation](images/editor-saved.png), and [a populated review panel](images/editor-review.png). The review illustration gives way to the author's actual review items.

**View → Reduce motion** persists in local QSettings. When enabled, changes appear immediately and active transitions finish immediately. `BYAX_REDUCED_MOTION=1` forces the same behavior for managed desktops or headless runs. Qt accessibility motion preferences, where exposed by the installed Qt/platform combination, also force reduced motion and update live. The preference cannot override an active system/environment request for reduced motion. On older Qt builds without accessibility motion hints, use the menu or environment setting.

![About window with the approved identity and pen gesture](images/about.png)

## Hand-drawn marks and rules

Lines that sit on the page are drawn the way the logo's underline is: a pen line with a slight wave and uneven crests, not a ruler.

**Proofreader's marks.** Findings from the spelling and grammar suites (the AI tab's spelling check, and **Run document checks** when a checking profile includes them) are drawn under the exact words: a red pen squiggle for spelling, a green pen squiggle for grammar. They are interface only. They are never saved into a document, and never appear in Pages, print or any export. A mark records the text of the block it was measured on and is drawn only while the block still reads exactly that, so it hides the moment you edit the block and can never sit under the wrong word. Replacing a word, ignoring it or adding it to the dictionary removes its mark with its list entry. Hover a mark for its label (the same text the lists show), so meaning never rests on color alone. New marks write themselves in over 180 ms; reduced motion draws them at once.

**Underline and strikethrough.** In the writing view, authored underline and strikethrough are drawn as pen strokes in the text's own color. The editor stores them as custom character properties instead of Qt's native lines, so typing inside a struck run simply continues the stroke. Pages, print and every export are resolved from the document model by `layout.resolve_pages`, which always builds typographically exact lines whatever the editor is doing, and the switch is thread-local so a background export can never inherit it. Hyperlinks keep their native underline.

**Preference.** **View → Hand-drawn marks** (on by default; stored as `appearance/handDrawnMarks`) switches the editor between pen strokes and plain lines: native underline and strikethrough, and regular zigzag proof marks. When the platform asks for high contrast, marks use the text color at a heavier weight.

**Chrome rules.** The quiet `Rule` separators (under the toolbar and dock titles, above the status bar, between menu items and between toolbar groups) are Vixl-authored hand-inked strokes applied through the stylesheet (`ui/rule-h`, `rule-v`), and the selected tab's underline is a short pen-green stroke (`tab-underline`). They are decoration only: the keyboard focus ring stays a 2 px solid deep-green border, and the platform high-contrast theme clears the brand stylesheet and the rules with it.

## Authoring with Vixl

The integrated profiles come from the [logo companion library](../assets/logo-companion-library/README.md): `pen-loop-wait`, `saved-ink`, `signature-settle`, `ink-blot-loader`, `margin-review`, and the static `margin-notes`, `idea-on-paper`, `local-copy`, `signature-manuscript`, `revision-pages`, and `loop-bound-book` illustrations. Only these runtime PNG/JSON files are copied into the package; the full shelf (masters, recipes, SVG, WebP/APNG/GIF exports) stays in `assets/`. Their editable masters retain original pen centerlines and writing/erasing tracks. Native assets live in `src/byax/resources/brand/ui`; each sprite manifest records its source hash, timing, crop, selected layers, loop mode, poster time, and Vixl check results.

Install Vixl into the separate authoring environment, regenerate a companion master if needed, then bake the selected profiles:

```bash
python -m venv .venv-assets
.venv-assets/bin/python -m pip install -r assets/logo-companion-library/requirements.txt
.venv-assets/bin/python scripts/build_ui_art.py
```

On Windows use `.venv-assets\Scripts\python.exe`. The bake script renders all frames in Vixl, crops only transparent margins, and uses Pillow to arrange those frames into PNG sprites. The save profile selects only its checkmark and ink accent so the status bar carries no miniature document illustration. Busy motion includes the exact loop endpoint; finite motion includes the exact completed pose. Poster PNGs are independent of the final frame so reduced-motion waiting states remain fully drawn. The application and frozen builds use these committed PNG/JSON resources, with no Vixl runtime dependency or animation-format plugin requirement.

The AI-tab loops and empty-state stills do not need Vixl: `scripts/build_ui_art.py --companions-only` unpacks the delivered transparent APNG frames with Pillow (no resampling), crops transparent margins, closes each loop on its opening pose, uses the delivered reduced-motion PNG as the poster, and copies the illustration PNGs. Each manifest records the APNG and poster hashes and the library's Vixl check result; `provenance.json` records each still's source and hash. A full `build_ui_art.py` run also refreshes them.

The icons, dialog illustrations, the checking and dictation loops and the chrome rules come from the [logo UI library](../assets/logo-ui-library/README.md). Rebuild them in the same authoring environment, then refresh the runtime copies (Pillow only, no Vixl needed for the second step):

```bash
.venv-assets/bin/python scripts/build_logo_ui.py
.venv-assets/bin/python scripts/build_ui_art.py --companions-only
```

Every design passes Vixl's bounds, motion and composition checks and an asset contract; each icon also carries a palette rule limited to ink, pen green and ink red. Renders and animations are byte-identical between builds. The runtime icon atlases (`ui/icons-{light,dark}-{16,24,32,48}.png`, `ui/icons.json`) and each still, loop and rule record their source and hash in `ui/provenance.json`.

The earlier [pen-settle experiment](../design/brand/motion/pen-settle.vixl), its contact sheet, and `scripts/build_brand_motion.py` remain as source references. They do not rebuild the current `ui/` profiles.

## Verification

The [editable sources](../design/brand/README.md) preserve the supplied guide, examples, and new timeline. Vixl is the animation authoring tool; native playback stays in `gui_motion.py`. Preserve the immediate reduced-motion path when adding transitions.

`tests/test_ui_icons_marks.py` checks the library against its own checksums and manifest, the icon atlases (every designed icon present, exact pixmaps at every size, distinct drawings, brand colors only), the new stills, loops and rules with their provenance, icons and icon-only buttons in the toolbar and menus, the illustrated result headers and when they appear, the checking and dictation loops starting and stopping with their tasks (including failure and closing), that pen-stroke decorations exist only in the editing view and never reach `resolve_pages` or another thread, that marks sit under the word they measure, hide when their block changes, write over 180 ms and not under reduced motion, never enter the document, and fall back to plain lines. `tests/test_brand_motion.py`, `tests/test_ui_art.py`, and `tests/test_gui_ai.py` exercise resources, companion provenance and loop endpoints, palette-based variant choice, task-owned AI/spelling loops, exact sprite endpoints, completion/interruption, reduced motion, hidden/minimized windows, concurrent workers, terminal job states, explicit save versus autosave, style-key mapping, and document preservation. Existing GUI and engine tests remain required. Screenshots are captured from Qt's offscreen native renderer using a sample document (`python scripts/capture_screens.py` regenerates them after a typography or branding change); its Source Serif 4 style is an explicit specimen choice. The busy capture holds a local task open for inspection. Windows installer appearance, Linux desktop icon caches, screen-reader announcements, and physical high-DPI displays require target-desktop verification.
