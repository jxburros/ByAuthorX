"""Build the ByAuthorX promo film with Vixl.

Every scene is an editable `.vixl` timeline in `scenes/`; the score is a Vixl
synth document; `film-export` stitches them into `byauthorx-promo.mp4`.

    python design/promo/build_promo.py [--vixl PATH] [--draft]

Requires Vixl 0.27+ and ffmpeg on PATH.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BUILD = HERE / "scenes"

W, H, FPS = 1920, 1080, 30
TYPE_MS = 60

PAPER = "#F4EEE3"
SURFACE = "#FFFCF7"
INK = "#252927"
GREEN = "#367B6C"
DEEP = "#246052"
RED = "#AD283E"
RULE = "#D7D0C3"
MUTED = "#52615B"

FONTS = ROOT / "assets/brand-fonts/fonts"
IRONGALL = str(FONTS / "irongall/Irongall-Regular.ttf")
SCHOLIUM = str(FONTS / "scholium/Scholium-Regular.ttf")
ONIONSKIN = str(FONTS / "onionskin/Onionskin-Regular.ttf")
SERIF = str(ROOT / "design/brand/kit/fonts/Source-Serif-4-400.ttf")

IMAGES = ROOT / "docs/images"
COMPANION = ROOT / "assets/logo-companion-library/illustrations"
UI_ART = ROOT / "assets/logo-ui-library/illustrations"
LOGO = ROOT / "design/brand/kit/logos/primary.vixl"

VIXL = "vixl"


def run(*args: str) -> dict:
    proc = subprocess.run([VIXL, "--json", *args], cwd=HERE, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"vixl {' '.join(args[:3])} failed:\n{proc.stdout}\n{proc.stderr}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def apply(doc: Path, ops: list[dict]) -> None:
    ops_file = BUILD / f"{doc.stem}.operations.json"
    ops_file.write_text(json.dumps(ops, indent=1))
    run("-p", str(doc), "apply", str(ops_file))


def scene(name: str, ops: list[dict], ms: int, background: str = PAPER, base: Path | None = None) -> Path:
    doc = BUILD / f"{name}.vixl"
    doc.unlink(missing_ok=True)
    if base:
        shutil.copy(base, doc)
        head = [{"type": "canvas", "width": W, "height": H, "background": background}]
    else:
        run("new", f"{W}x{H}", "--background", background, "-o", str(doc))
        head = []
    head.append({"type": "timeline-set", "duration": ms, "fps": FPS, "loop": 1})
    apply(doc, head + ops)
    return doc


# ---------------------------------------------------------------- helpers

def text(name, value, x, y, size, font=SERIF, color=INK, width=None, align="left", **extra):
    ops = [{"type": "text", "name": name, "text": value, "font": font, "size": size,
            "color": color, "x": x, "y": y, "align": align, **extra}]
    if width:
        ops.append({"type": "text-layout", "target": name, "width": width, "wrap": "pretty"})
    return ops


def fade_up(target, start, duration=700, distance=28):
    return [{"type": "animate-preset", "target": target, "preset": "slide-in-up", "start": start,
             "duration": duration, "distance": distance, "easing": "ease-out-cubic"}]


def fade_in(target, start, duration=600):
    return [{"type": "animate-preset", "target": target, "preset": "fade-in", "start": start,
             "duration": duration}]


def words_in(target, start, stagger=70, duration=520):
    return [{"type": "text-animate", "target": target, "preset": "fade-up", "unit": "word",
             "start": start, "duration": duration, "stagger": stagger, "mode": "in"}]


def draw(target, start, duration=700, easing="ease-in-out-sine"):
    return [{"type": "keyframe", "target": target, "property": "trim_end", "time": 0, "value": 0,
             "easing": "hold"},
            {"type": "keyframe", "target": target, "property": "trim_end", "time": start, "value": 0,
             "easing": easing},
            {"type": "keyframe", "target": target, "property": "trim_end", "time": start + duration,
             "value": 100}]


def pen(name, path, x, y, color=GREEN, width=5):
    return [{"type": "shape", "shape": "path", "name": name, "path": path, "x": x, "y": y,
             "stroke": color, "stroke_width": width, "fill": "none", "line_cap": "round"}]


def image(name, path, x, y, width=None, height=None):
    if width and not height:
        w, h = Image.open(path).size
        height = round(width * h / w)
    op = {"type": "add", "name": name, "path": str(path), "x": x, "y": y}
    if width:
        op.update(width=width, height=height)
    return [op]


def bounds(doc: Path) -> dict[str, list[float]]:
    """Canvas bounds [x, y, w, h] of every named layer, measured by Vixl."""
    return {layer["name"]: layer["canvas_bounds"] for layer in run("-p", str(doc), "layers")
            if "canvas_bounds" in layer}


def wave_wipe(start, end, hold=0):
    """Reveal the logo's pen line from the ink dot.

    The line is a filled outline, so trim cannot draw it; a paper-coloured rectangle at the
    bottom of the wordmark group grows under it and the line is clipped to that rectangle.
    Coordinates are the wordmark group's own (its artwork is drawn at about 2x inside).
    """
    return [{"type": "shape", "shape": "rectangle", "name": "wipe", "x": 0, "y": 0, "width": 2,
             "height": 90, "fill": PAPER},
            {"type": "reparent", "targets": ["wipe"], "into": "wordmark", "keep": "local", "index": 0},
            {"type": "move", "target": "wipe", "x": 0, "y": 104},
            {"type": "clip", "target": "loose-wave", "base": "wipe"},
            {"type": "keyframe", "target": "wipe", "property": "width", "time": 0, "value": 2,
             "easing": "hold"},
            {"type": "keyframe", "target": "wipe", "property": "width", "time": start, "value": 2,
             "easing": "ease-in-out-sine"},
            {"type": "keyframe", "target": "wipe", "property": "width", "time": end, "value": 700}]


def card(name, x, y, w, h, fill=SURFACE, radius=14, shadow=True, stroke=None):
    op = {"type": "shape", "shape": "rounded-rectangle", "name": name, "x": x, "y": y,
          "width": w, "height": h, "radius": radius, "fill": fill}
    if stroke:
        op.update(stroke=stroke, stroke_width=2)
    ops = [op]
    if shadow:
        ops.append({"type": "layer-style", "target": name, "name": "drop-shadow",
                    "settings": {"color": "rgba(37,41,39,0.16)", "dx": 0, "dy": 14, "blur": 28}})
    return ops


def screenshot(name, path, x, y, scale, overlay=(), members=()):
    """A screenshot on a soft card, grouped with its overlays so they move as one."""
    w, h = round(1320 * scale), round(900 * scale)
    return (card(f"{name}-card", x - 10, y - 10, w + 20, h + 20, radius=16)
            + image(f"{name}-shot", path, x, y, width=w, height=h)
            + list(overlay)
            + [{"type": "group", "name": name, "targets": [f"{name}-card", f"{name}-shot", *members]}])


def eyebrow(value, x, y, start, color=GREEN):
    return (text("eyebrow", value.upper(), x, y, 26, font=SCHOLIUM, color=color, tracking=4)
            + fade_in("eyebrow", start, 500))


# ---------------------------------------------------------------- scenes

def s01_blank():
    ms = 8000
    ops = [{"type": "shape", "shape": "rectangle", "name": "page", "x": 360, "y": 90, "width": 1200,
            "height": 1000, "fill": SURFACE},
           {"type": "layer-style", "target": "page", "name": "drop-shadow",
            "settings": {"color": "rgba(37,41,39,0.12)", "dx": 0, "dy": 10, "blur": 34}},
           {"type": "animate", "target": "page", "property": "translate-y", "from": 70, "to": 0,
            "start": 0, "duration": 1400, "easing": "ease-out-cubic"}]
    ops += fade_in("page", 0, 900)
    lines = [("line1a", "Every book begins", 1300), ("line1b", "on a blank page.", 2500)]
    for i, (name, value, t0) in enumerate(lines):
        ops += text(name, value, 520, 300 + i * 96, 80)
        ops += [{"type": "text-animate", "target": name, "preset": "typewriter", "unit": "char",
                 "start": t0, "duration": 40, "stagger": TYPE_MS, "mode": "in"}]
    ops += text("line2", "What happens next should be up to you.", 520, 560, 44, font=SCHOLIUM,
                color=MUTED)
    ops += words_in("line2", 4300, stagger=110, duration=600)
    ops += pen("underline", "M0 10 C 160 2, 330 16, 520 6 S 700 0, 760 8", 524, 640, width=5)
    ops += draw("underline", 5600, 900)
    ops += [{"type": "shape", "shape": "ellipse", "name": "dot", "x": 508, "y": 638, "width": 22,
             "height": 22, "fill": RED}]
    ops += [{"type": "animate-preset", "target": "dot", "preset": "pop-in", "start": 5400,
             "duration": 350}]
    doc = scene("01-blank", ops, ms)
    # caret: waits at the start, hides while typing, then rests after the last letter
    b = bounds(doc)
    a_x, a_y = b["line1a"][0], b["line1a"][1]
    e_x, e_y = b["line1b"][0] + b["line1b"][2] + 10, b["line1b"][1]
    typed = 2500 + TYPE_MS * len(lines[1][1])
    caret = []
    for name, x, y, on, off in [("caret-wait", a_x - 4, a_y, 700, 1300), ("caret-rest", e_x, e_y, typed, ms)]:
        caret += [{"type": "shape", "shape": "rectangle", "name": name, "x": x, "y": y, "width": 5,
                   "height": 84, "fill": GREEN},
                  {"type": "keyframe", "target": name, "property": "opacity", "time": 0,
                   "value": 0, "easing": "hold"}]
        for k, t in enumerate(range(on, off, 500)):
            caret.append({"type": "keyframe", "target": name, "property": "opacity", "time": t,
                          "value": 1 - k % 2, "easing": "hold"})
        if off < ms:
            caret.append({"type": "keyframe", "target": name, "property": "opacity", "time": off,
                          "value": 0, "easing": "hold"})
    apply(doc, caret)
    return doc, ms


def s02_friction():
    ms = 10000
    ops = eyebrow("The usual deal", 200, 170, 200, color=RED)
    ops += text("head", "Most writing tools want something back.", 200, 220, 72, font=IRONGALL,
                width=1500)
    ops += words_in("head", 400, stagger=90, duration=600)
    asks = ["Make an account first.", "Keep your draft in our cloud.",
            "Let the assistant rewrite your voice.", "Hope the export looks right."]
    for i, line in enumerate(asks):
        y = 420 + i * 120
        t0 = 1700 + i * 450
        ops += [{"type": "shape", "shape": "ellipse", "name": f"bullet{i}", "x": 210, "y": y + 22,
                 "width": 14, "height": 14, "fill": MUTED}]
        ops += text(f"ask{i}", line, 260, y, 50, color=INK)
        ops += fade_up(f"ask{i}", t0, 650, 24) + fade_in(f"bullet{i}", t0, 400)
    ops += text("answer", "Not here.", 1340, 800, 96, font=IRONGALL, color=GREEN)
    ops += [{"type": "animate-preset", "target": "answer", "preset": "pop-in", "start": 7300,
             "duration": 600}]
    ops += [{"type": "rotate", "target": "answer", "value": -6}]
    doc = scene("02-friction", ops, ms)
    b = bounds(doc)
    strikes = []
    for i in range(len(asks)):
        x, y, w, h = b[f"ask{i}"]
        length = w + 36
        strikes += pen(f"strike{i}", f"M0 10 C {length*0.3:.0f} 2, {length*0.6:.0f} 16, {length:.0f} 4",
                       x - 18, y + h * 0.5 - 8, color=RED, width=6)
        strikes += draw(f"strike{i}", 4600 + i * 520, 480, "ease-out-quad")
        strikes += [{"type": "animate", "target": f"ask{i}", "property": "opacity", "to": 0.45,
                     "start": 4700 + i * 520, "duration": 400}]
    apply(doc, strikes)
    return doc, ms


def s03_logo():
    ms = 7500
    letters = ["letter-0-B", "letter-1-y", "letter-2-A", "letter-3-u", "letter-4-t", "letter-5-h",
               "letter-6-o", "letter-7-r"]
    ops = [{"type": "scale", "target": "wordmark", "value": 0.78, "anchor": "center"},
           {"type": "move", "target": "wordmark", "x": "center", "y": 250}]
    for i, letter in enumerate(letters):
        ops += [{"type": "animate-preset", "target": letter, "preset": "slide-in-up", "start": 200 + i * 90,
                 "duration": 650, "distance": 40, "easing": "ease-out-back"}]
    for i, stroke in enumerate(["X-fall", "X-rise"]):
        t0 = 1050 + i * 140
        ops += [{"type": "keyframe", "target": stroke, "property": "opacity", "time": 0, "value": 0,
                 "easing": "hold"},
                {"type": "keyframe", "target": stroke, "property": "opacity", "time": t0, "value": 0,
                 "easing": "ease-out"},
                {"type": "keyframe", "target": stroke, "property": "opacity", "time": t0 + 250, "value": 1},
                {"type": "pivot", "target": stroke, "value": "center"},
                {"type": "animate", "target": stroke, "property": "scale", "from": 0.6, "to": 1,
                 "start": t0, "duration": 450, "easing": "ease-out-back"}]
    ops += [{"type": "animate-preset", "targets": ["red-ink-terminal", "red-ink-terminal-fleck"],
             "preset": "pop-in", "start": 1700, "duration": 380}]
    ops += wave_wipe(1850, 3100)
    ops += text("tagline", "Your words. Your call.", "center", 690, 76, font=IRONGALL, align="center")
    ops += words_in("tagline", 3400, stagger=160, duration=650)
    ops += text("sub", "A native word processor for people who write.", "center", 800, 36,
                font=SCHOLIUM, color=MUTED, align="center")
    ops += fade_up("sub", 4500, 800, 16)
    return scene("03-logo", ops, ms, base=LOGO), ms


def feature(name, ms, eyebrow_text, head, body, shot, shot_x, text_x, overlay=(), members=(),
            body_start=1500):
    ops = screenshot("app", shot, shot_x, 180, 0.82, overlay, members)
    ops += [{"type": "animate", "target": "app", "property": "translate-x",
             "from": 120 if shot_x > text_x else -120, "to": 0, "start": 0, "duration": 1100,
             "easing": "ease-out-cubic"}]
    ops += fade_in("app", 0, 800)
    ops += [{"type": "pivot", "target": "app", "value": "center"},
            {"type": "animate", "target": "app", "property": "scale", "from": 1, "to": 1.035,
             "start": 1100, "duration": ms - 1100, "easing": "linear"}]
    ops += eyebrow(eyebrow_text, text_x, 250, 300)
    ops += text("head", head, text_x, 300, 62, font=IRONGALL, width=560)
    ops += words_in("head", 500, stagger=80, duration=600)
    for i, line in enumerate(body):
        y = 560 + i * 105
        ops += pen(f"tick{i}", "M0 14 L 10 24 L 30 0", text_x, y + 8, color=GREEN, width=5)
        ops += draw(f"tick{i}", body_start + i * 420, 350)
        ops += text(f"body{i}", line, text_x + 50, y, 34, font=SCHOLIUM, color=INK)
        ops += fade_up(f"body{i}", body_start + 80 + i * 420, 600, 14)
    return scene(name, ops, ms), ms


def s04_editor():
    return feature("04-editor", 11000, "The writing desk", "A calm page, and familiar controls.",
                   ["Outline, pages and inspector at hand",
                    "Typing and autosave stay quiet",
                    "Light or dark theme, and reduced motion"],
                   IMAGES / "editor.png", 760, 120)


def s05_review():
    # screenshot at x=100, scale 0.84: the review card sits at (1037..1303, 248..301) in the shot
    sx, sy, k = 100, 180, 0.82

    def at(px, py):
        return sx + px * k, sy + py * k
    ops = []
    for name, (x0, y0, x1, y1), pad, color, t0 in [("ring", (1037, 248, 1303, 301), 16, RED, 2600),
                                                  ("ring2", (1029, 772, 1311, 808), 9, GREEN, 4200)]:
        (ax, ay), (bx, by) = at(x0, y0), at(x1, y1)
        ops += [{"type": "shape", "shape": "ellipse", "name": name, "x": ax - 26, "y": ay - pad,
                 "width": bx - ax + 52, "height": by - ay + 2 * pad, "fill": "none", "stroke": color,
                 "stroke_width": 5, "line_cap": "round", "stroke_align": "center", "rotation": -2}]
        ops += draw(name, t0, 800)
    overlay = ops
    return feature("05-review", 11000, "Review", "Suggestions wait for your say.",
                   ["Comments and AI ideas wait in Review",
                    "Accept or reject each one, line by line",
                    "Nothing changes until you decide"],
                   IMAGES / "editor-review.png", sx, 1250, overlay=overlay, members=("ring", "ring2"),
                   body_start=1700)


def s06_ai():
    ms = 11500
    ops = eyebrow("Optional AI", "center", 110, 200)
    ops += [{"type": "align", "target": "eyebrow", "alignment": "center-x"}]
    ops += text("head", "Help when you ask. Silence when you don't.", "center", 160, 66,
                font=IRONGALL, align="center")
    ops += [{"type": "align", "target": "head", "alignment": "center-x"}]
    ops += words_in("head", 400, stagger=70, duration=600)
    # Chat / Work switch
    ops += card("switch", 760, 300, 400, 76, fill=SURFACE, radius=38, shadow=False, stroke=RULE)
    ops += [{"type": "shape", "shape": "rounded-rectangle", "name": "knob", "x": 766, "y": 306,
             "width": 194, "height": 64, "radius": 32, "fill": GREEN}]
    ops += text("chat-label", "Chat", 760, 318, 34, font=IRONGALL, color=INK, align="center")
    ops += [{"type": "text-layout", "target": "chat-label", "width": 200}]
    ops += text("work-label", "Work", 960, 318, 34, font=IRONGALL, color=INK, align="center")
    ops += [{"type": "text-layout", "target": "work-label", "width": 200}]
    ops += [{"type": "keyframe", "target": "chat-label", "property": "color", "time": 0, "value": SURFACE,
             "easing": "hold"},
            {"type": "keyframe", "target": "chat-label", "property": "color", "time": 5000,
             "value": SURFACE, "easing": "ease-in-out"},
            {"type": "keyframe", "target": "chat-label", "property": "color", "time": 5400, "value": INK},
            {"type": "keyframe", "target": "work-label", "property": "color", "time": 0, "value": INK,
             "easing": "hold"},
            {"type": "keyframe", "target": "work-label", "property": "color", "time": 5000, "value": INK,
             "easing": "ease-in-out"},
            {"type": "keyframe", "target": "work-label", "property": "color", "time": 5400,
             "value": SURFACE},
            {"type": "keyframe", "target": "knob", "property": "translate-x", "time": 0, "value": 0,
             "easing": "hold"},
            {"type": "keyframe", "target": "knob", "property": "translate-x", "time": 5000, "value": 0,
             "easing": "ease-in-out-cubic"},
            {"type": "keyframe", "target": "knob", "property": "translate-x", "time": 5400, "value": 194}]
    ops += [{"type": "group", "name": "switcher", "targets": ["switch", "knob", "chat-label", "work-label"]}]
    ops += fade_up("switcher", 1100, 600, 16)
    # two cards
    for i, (key, title, line, art, t0) in enumerate([
        ("chat", "Chat answers.", "Ask about your draft. It never touches the page.",
         COMPANION / "idea-on-paper-light@2x.png", 1700),
        ("work", "Work proposes.", "Exact edits go to Review. You approve each one.",
         COMPANION / "margin-notes-light@2x.png", 5300),
    ]):
        x = 300 + i * 680
        ops += card(f"{key}-card", x, 430, 640, 400, stroke=RULE)
        ops += image(f"{key}-art", art, x + 10, 520, width=300)
        ops += text(f"{key}-title", title, x + 310, 500, 46, font=IRONGALL)
        ops += text(f"{key}-line", line, x + 310, 580, 32, font=SCHOLIUM, color=MUTED, width=300)
        ops += [{"type": "group", "name": key, "targets": [f"{key}-card", f"{key}-art",
                                                          f"{key}-title", f"{key}-line"]}]
        ops += fade_up(key, t0, 750, 40)
    chips = ["No account", "No telemetry", "Keys stay in your OS keychain", "Spelling checks run locally"]
    x = 0
    sizes = []
    for c in chips:
        sizes.append(len(c) * 15 + 70)
    total = sum(sizes) + 24 * (len(chips) - 1)
    x = (W - total) / 2
    for i, (c, w) in enumerate(zip(chips, sizes)):
        ops += card(f"chip{i}-bg", x, 900, w, 64, fill=PAPER, radius=32, shadow=False, stroke=GREEN)
        ops += text(f"chip{i}-t", c, x, 914, 28, font=SCHOLIUM, color=DEEP, align="center")
        ops += [{"type": "text-layout", "target": f"chip{i}-t", "width": w},
                {"type": "group", "name": f"chip{i}", "targets": [f"chip{i}-bg", f"chip{i}-t"]}]
        ops += [{"type": "animate-preset", "target": f"chip{i}", "preset": "pop-in",
                 "start": 7300 + i * 260, "duration": 450}]
        x += w + 24
    return scene("06-ai", ops, ms), ms


def s07_marks():
    ms = 9000
    k = 1.6
    # centre the page's text block (about 625, 375 in the screenshot) left of centre
    ops = image("marks", IMAGES / "editor-marks.png", 960 - 625 * k - 200, 430 - 375 * k,
                width=round(1320 * k), height=round(900 * k))
    ops += [{"type": "pivot", "target": "marks", "value": [0.47, 0.42]},
            {"type": "animate", "target": "marks", "property": "scale", "from": 1.0, "to": 1.07,
             "start": 0, "duration": ms, "easing": "linear"}]
    ops += [{"type": "gradient", "name": "veil", "x": 0, "y": 640, "width": W, "height": 440,
             "stops": [{"offset": 0, "color": "rgba(244,238,227,0)"},
                       {"offset": 0.38, "color": "rgba(244,238,227,0.96)"},
                       {"offset": 1, "color": PAPER}]}]
    ops += eyebrow("Hand-inked marks", 160, 820, 600)
    ops += text("head", "Red pen for spelling. Green for grammar.", 160, 862, 60, font=IRONGALL)
    ops += words_in("head", 900, stagger=80, duration=600)
    ops += text("note", "Interface only: print and every export keep clean, exact lines.", 160, 960,
                32, font=SCHOLIUM, color=MUTED)
    ops += fade_up("note", 2600, 700, 14)
    return scene("07-marks", ops, ms), ms


def s08_formats():
    ms = 10500
    ops = eyebrow("One master", "center", 100, 200)
    ops += [{"type": "align", "target": "eyebrow", "alignment": "center-x"}]
    ops += text("head", "Write once. Open it anywhere.", "center", 145, 70, font=IRONGALL, align="center")
    ops += [{"type": "align", "target": "head", "alignment": "center-x"}]
    ops += words_in("head", 400, stagger=90, duration=600)
    cx, cy = 960, 590
    ops += card("master", cx - 150, cy - 190, 300, 380, radius=10, stroke=INK)
    ops += image("master-art", COMPANION / "revision-pages-light@2x.png", cx - 140, cy - 150, width=280)
    ops += text("master-ext", ".byax", cx - 150, cy + 95, 52, font=IRONGALL, align="center")
    ops += [{"type": "text-layout", "target": "master-ext", "width": 300},
            {"type": "group", "name": "master-doc", "targets": ["master", "master-art", "master-ext"]},
            {"type": "pivot", "target": "master-doc", "value": "center"}]
    ops += [{"type": "animate-preset", "target": "master-doc", "preset": "zoom-in", "start": 900,
             "duration": 700, "easing": "ease-out-back"}]
    formats = [("PDF", -560, -150), ("Word", -620, 40), ("OpenDocument", -560, 230),
               ("RTF", 560, -150), ("HTML", 620, 40), ("Markdown", 560, 230), ("Plain text", 0, 330)]
    for i, (label, dx, dy) in enumerate(formats):
        w = len(label) * 22 + 70
        x, y = cx + dx - w / 2, cy + dy - 34
        if dx:
            sx = cx + (150 if dx > 0 else -150)
            ex = x if dx > 0 else x + w
            path = f"M0 0 C {(ex - sx) * 0.5:.0f} 0, {(ex - sx) * 0.5:.0f} {dy:.0f}, {ex - sx:.0f} {dy:.0f}"
            ops += pen(f"link{i}", path, sx, cy, color=GREEN, width=4)
        else:
            ops += pen(f"link{i}", f"M0 0 L 0 {dy - 34 - 190:.0f}", cx, cy + 190, color=GREEN, width=4)
        ops += draw(f"link{i}", 1700 + i * 200, 500)
        ops += card(f"fmt{i}-bg", x, y, w, 68, fill=SURFACE, radius=34, shadow=True, stroke=GREEN)
        ops += text(f"fmt{i}-t", label, x, y + 14, 34, font=IRONGALL, align="center")
        ops += [{"type": "text-layout", "target": f"fmt{i}-t", "width": w},
                {"type": "group", "name": f"fmt{i}", "targets": [f"fmt{i}-bg", f"fmt{i}-t"]},
                {"type": "pivot", "target": f"fmt{i}", "value": "center"}]
        ops += [{"type": "animate-preset", "target": f"fmt{i}", "preset": "pop-in",
                 "start": 2100 + i * 200, "duration": 450}]
    ops += text("note", "Exports never replace the master, and every lossy step comes with a report.",
                "center", 1000, 32, font=SCHOLIUM, color=MUTED, align="center")
    ops += [{"type": "align", "target": "note", "alignment": "center-x"}]
    ops += fade_up("note", 4300, 700, 14)
    return scene("08-formats", ops, ms), ms


def s09_engine():
    ms = 11000
    ops = eyebrow("Programmable", 140, 230, 200, color="#8FC2B4")
    ops += text("head", "The same engine, from the command line.", 140, 280, 64, font=IRONGALL,
                color=PAPER, width=620)
    ops += words_in("head", 400, stagger=80, duration=600)
    ops += text("body", "byax, byax-mcp and a loopback REST service call the desktop's engine: "
                "atomic edits, revision checks, history and validated exports.", 140, 560, 32,
                font=SCHOLIUM, color="#CFC8BA", width=600)
    ops += fade_up("body", 1500, 700, 14)
    ops += card("term", 840, 220, 960, 640, fill="#1B1F1D", radius=16, stroke="#3A403D")
    for i, c in enumerate(["#AD283E", "#C9A24A", "#367B6C"]):
        ops += [{"type": "shape", "shape": "ellipse", "name": f"light{i}", "x": 872 + i * 30, "y": 246,
                 "width": 16, "height": 16, "fill": c}]
    ops += [{"type": "group", "name": "terminal", "targets": ["term", "light0", "light1", "light2"]}]
    ops += fade_up("terminal", 600, 800, 30)
    lines = [
        ("$ byax new --out chapter.byax --title Draft", PAPER),
        ("  # a native document, with history", "#8FC2B4"),
        ("$ byax check chapter.byax --suite structure", PAPER),
        ("  # deterministic checks, no model needed", "#8FC2B4"),
        ("$ byax export chapter.byax --out chapter.pdf", PAPER),
        ("  # the master stays untouched", "#8FC2B4"),
        ("$ byax-mcp", PAPER),
        ("  # the same engine, for your agent", "#8FC2B4"),
    ]
    t = 1800
    for i, (line, color) in enumerate(lines):
        y = 300 + i * 66
        ops += text(f"cmd{i}", line, 880, y, 27, font=ONIONSKIN, color=color)
        if line.startswith("$"):
            per = 26
            ops += [{"type": "text-animate", "target": f"cmd{i}", "preset": "typewriter", "unit": "char",
                     "start": t, "duration": 30, "stagger": per, "mode": "in"}]
            t += per * len(line) + 250
        else:
            ops += fade_in(f"cmd{i}", t, 250)
            t += 450
    return scene("09-engine", ops, ms, background=INK), ms


def s10_end():
    ms = 9000
    ops = [{"type": "scale", "target": "wordmark", "value": 0.6, "anchor": "center"},
           {"type": "move", "target": "wordmark", "x": "center", "y": 230}]
    ops += fade_in("wordmark", 0, 900)
    ops += wave_wipe(500, 1700)
    ops += text("tagline", "Your words. Your call.", "center", 590, 72, font=IRONGALL, align="center")
    ops += words_in("tagline", 1500, stagger=150, duration=600)
    ops += text("facts", "Free and open source  ·  Windows 11 and Ubuntu 24.04  ·  No account needed",
                "center", 720, 34, font=SCHOLIUM, color=MUTED, align="center")
    ops += fade_up("facts", 2700, 700, 14)
    ops += card("cta-bg", 660, 820, 600, 84, fill=GREEN, radius=10, shadow=False)
    ops += text("cta", "github.com/jxburros/ByAuthorX", 660, 842, 36, font=IRONGALL, color="#FFFFFF",
                align="center")
    ops += [{"type": "text-layout", "target": "cta", "width": 600},
            {"type": "group", "name": "button", "targets": ["cta-bg", "cta"]},
            {"type": "pivot", "target": "button", "value": "center"}]
    ops += [{"type": "animate-preset", "target": "button", "preset": "pop-in", "start": 3500,
             "duration": 550}]
    ops += [{"type": "align", "targets": ["tagline"], "alignment": "center-x", "relative_to": "canvas"},
            {"type": "align", "targets": ["facts"], "alignment": "center-x", "relative_to": "canvas"}]
    return scene("10-end", ops, ms, base=LOGO), ms


SCENES = [s01_blank, s02_friction, s03_logo, s04_editor, s05_review, s06_ai, s07_marks,
          s08_formats, s09_engine, s10_end]
TRANSITION = 600


# ---------------------------------------------------------------- score

def score(starts: list[int], total: int) -> Path:
    """A quiet piano bed in F major at 84 bpm with soft accents on each cut."""
    beat = 60000 / 84
    bar = beat * 4
    # F, C/E, Dm, Bb, F/A, Gm7, Bb, C (MIDI roots, chord tones)
    prog = [[53, 57, 60, 65], [52, 55, 60, 64], [50, 57, 62, 65], [46, 53, 58, 62],
            [45, 53, 57, 60], [43, 53, 58, 62], [46, 53, 58, 65], [48, 55, 60, 64]]
    arp, bass, pad = [], [], []
    bars = int(total // bar) + 1
    for b in range(bars):
        chord = prog[b % len(prog)]
        t0 = b * bar
        bass.append({"start": round(t0), "duration": round(bar * 0.95), "midi": chord[0] - 12,
                     "velocity": 0.55})
        pattern = [1, 2, 3, 2, 1, 2, 3, 2] if b % 2 == 0 else [1, 3, 2, 3, 1, 3, 2, 3]
        for j, idx in enumerate(pattern):
            note = chord[idx] + 12
            vel = 0.5 if j % 4 == 0 else 0.34
            arp.append({"start": round(t0 + j * beat / 2), "duration": round(beat * 0.9), "midi": note,
                        "velocity": vel})
        if b % 4 == 3:
            pad.append({"start": round(t0 + beat * 3), "duration": round(beat * 2),
                        "midi": chord[3] + 24, "velocity": 0.3})
    end = total
    arp = [n for n in arp if n["start"] < end - 600]
    bass = [n for n in bass if n["start"] < end - 600]
    ops = [{"type": "timeline-set", "duration": total, "fps": FPS, "loop": 1},
           {"type": "audio-track", "name": "arp", "synth": "piano", "start": 0, "duration": total,
            "notes": arp, "volume": 0.42, "fade_in": 1500, "fade_out": 2500, "pan": 0.1},
           {"type": "audio-track", "name": "bass", "synth": "piano", "start": 0, "duration": total,
            "notes": bass, "volume": 0.38, "fade_in": 2500, "fade_out": 2500, "pan": -0.1},
           {"type": "audio-track", "name": "bells", "synth": "bell", "start": 0, "duration": total,
            "notes": pad + [{"start": s + 150, "duration": 1600, "midi": 77, "velocity": 0.35}
                            for s in starts[1:]],
            "volume": 0.25}]
    # typewriter clicks in the opening line, pen scratches on the strike-throughs
    clicks = [{"start": t0 + i * TYPE_MS, "duration": 40, "midi": 90, "velocity": 0.25}
              for t0, n in [(1300, 17), (2500, 16)] for i in range(n)]
    ops.append({"type": "audio-track", "name": "keys", "synth": "click", "start": 0, "duration": 4000,
                "notes": clicks, "volume": 0.35})
    strikes = [{"start": starts[1] + 4600 + i * 520, "duration": 420, "midi": 60, "velocity": 0.5}
               for i in range(4)]
    ops.append({"type": "audio-track", "name": "strikes", "synth": "whoosh", "start": 0,
                "duration": total, "notes": strikes, "volume": 0.2})
    doc = BUILD / "score.vixl"
    doc.unlink(missing_ok=True)
    run("new", "64x64", "-o", str(doc))
    apply(doc, ops)
    wav = BUILD / "score.wav"
    wav.unlink(missing_ok=True)
    run("-p", str(doc), "export", str(wav))
    return wav


# ---------------------------------------------------------------- film

def main() -> None:
    global VIXL
    parser = argparse.ArgumentParser()
    parser.add_argument("--vixl", default=shutil.which("vixl") or "vixl")
    parser.add_argument("--no-film", action="store_true")
    parser.add_argument("--out", default=str(HERE / "byauthorx-promo.mp4"))
    args = parser.parse_args()
    VIXL = args.vixl
    BUILD.mkdir(exist_ok=True)

    shots, starts, t = [], [], 0
    for i, build in enumerate(SCENES, 1):
        print(f"scene {i:02d}: {build.__name__}", flush=True)
        doc, ms = build()
        trans = TRANSITION if shots else 0
        t -= trans
        starts.append(max(t, 0))
        shot = {"source": str(doc.relative_to(HERE)), "duration": ms}
        if trans:
            shot["transition"] = trans
        shots.append(shot)
        t += ms
    total = t
    print(f"film length {total / 1000:.1f}s", flush=True)
    if args.no_film:
        return
    wav = score(starts, total)
    spec = {"version": 1, "width": W, "height": H, "fps": FPS, "shots": shots,
            "audio": [{"source": str(wav.relative_to(HERE)), "start": 0, "volume": 2.0}]}
    out = Path(args.out)
    out.unlink(missing_ok=True)
    request = BUILD / "film.json"
    request.write_text(json.dumps({"output": str(out.relative_to(HERE)), "spec": spec}, indent=1))
    result = run("workflow", "film-export", "--request", str(request), "--workspace", str(HERE))
    print(json.dumps({k: v for k, v in result.items() if k not in ("shots",)}, indent=1)[:2000])


if __name__ == "__main__":
    main()
