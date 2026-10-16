# ByAuthorX promo film

`byauthorx-promo.mp4` is a 93-second, 1920×1080, 30 fps promo with a synthesized piano score.
It was made entirely with [Vixl](https://github.com/jxburros/Vixl) 0.27 from the brand kit,
the bundled brand fonts (Irongall, Scholium, Onionskin, Source Serif 4), the logo companion
illustrations and the editor screenshots in `docs/images/`.

| # | Scene | Message |
| --- | --- | --- |
| 1 | Blank page | Every book begins on a blank page. |
| 2 | The usual deal | Accounts, cloud lock-in, rewritten voices: struck through in red pen. |
| 3 | Logo | Wordmark, pen line drawn from the ink dot, "Your words. Your call." |
| 4 | Writing desk | The native editor. |
| 5 | Review | Suggestions wait until you accept them. |
| 6 | Optional AI | Chat answers, Work proposes; no account, no telemetry, keys in the OS keychain. |
| 7 | Hand-inked marks | Red pen for spelling, green for grammar; exports stay exact. |
| 8 | One master | `.byax` to PDF, Word, OpenDocument, RTF, HTML, Markdown and plain text. |
| 9 | Programmable | `byax` and `byax-mcp` drive the same engine. |
| 10 | End card | Free and open source, Windows 11 and Ubuntu 24.04. |

Every scene is an editable Vixl timeline in `scenes/` (with the operations that built it), and
`scenes/film.json` is the film spec. To rebuild everything (about eight minutes):

```bash
pip install "vixl-engine @ https://github.com/jxburros/Vixl/archive/refs/tags/v0.27.0.tar.gz"  # ffmpeg must be on PATH
python design/promo/build_promo.py
```

`--no-film` rebuilds only the scene documents, which is handy with
`vixl -p design/promo/scenes/04-editor.vixl timeline-sheet --out sheet.png` for checking motion.
