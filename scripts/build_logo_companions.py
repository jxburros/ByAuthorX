#!/usr/bin/env python3
"""Create a logo-led companion shelf using the supplied ByAuthorX vector masters."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil

from PIL import Image, ImageDraw, ImageFont
from vixl import Project

import build_creative_library as library

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets/logo-companion-library"
SOURCES = {
    "pen-line": "design/brand/kit/graphics/pen-line.vixl",
    "ink-dot": "design/brand/kit/graphics/ink-dot.vixl",
    "mark": "design/brand/logo-masters/app-transparent.vixl",
    "tokens": "src/byax/resources/brand/tokens.json",
    "wordmark-light": "src/byax/resources/brand/wordmark-light.png",
    "wordmark-dark": "src/byax/resources/brand/wordmark-dark.png",
}
PARTS = {}
TOKENS = {}


class BrandArt(library.Art):
    def __init__(self, width=640, height=480, theme="light", opaque=False):
        super().__init__(width, height, theme)
        colors = TOKENS["color"]
        self.c = dict(ink=colors["ink"] if theme == "light" else colors["paper"],
                      paper=colors["paper"] if theme == "light" else "#303735",
                      green=colors["green"], red=colors["red"], sage=colors["green"],
                      soft=colors["surface"] if theme == "light" else "#303735",
                      muted=colors["muted"] if theme == "light" else "#BFB9AE",
                      shade=colors["rule"] if theme == "light" else colors["ink"],
                      rule=colors["rule"] if theme == "light" else "#52615B")
        if opaque:
            self.background = colors["paper"] if theme == "light" else colors["ink"]

    def source_part(self, source, part, name, x, y, scale=1):
        original = PARTS[source][part]
        width, height = original["path_view"]
        fill = original["fill"]
        if fill.lower() == TOKENS["color"]["ink"].lower():
            fill = self.c["ink"]
        self.path(name, original["path"], fill, None, x=x, y=y, w=width, h=height)
        if scale != 1:
            self.ops.append(dict(type="resize", target=name, width=width*scale, height=height*scale))
        return name

    def gesture(self, name, x, y, width=380):
        return self.source_part("pen-line", "pen-line", name, x, y, width/629.0314643198849)

    def blot(self, name, x, y, size=18):
        scale = size/17.5
        dot = self.source_part("ink-dot", "ink-dot", name+"-body", x, y, scale)
        fleck = self.source_part("ink-dot", "ink-fleck", name+"-fleck", x-.428571*scale, y+12.5*scale, scale)
        return self.group(name, [dot, fleck])

    def mark(self, name, x, y, height=150):
        scale = height/172
        names=[]
        for part, original in PARTS["mark"].items():
            names.append(self.source_part("mark", part, name+"-"+part,
                                          x+original["x"]*scale, y+original["y"]*scale, scale))
        return self.group(name, names)

    def page(self, name, x, y, w=190, h=250, lines=5, fill="soft"):
        # A working sheet: slightly imperfect edges, without a rounded UI-card frame.
        self.path(name, f"M4 1 L{w-5} 0 L{w} 5 L{w-2} {h-3} L{w-8} {h} L0 {h-1} L2 8 Z",
                  fill, "ink", 2.5, x=x, y=y, w=w, h=h)
        for i in range(lines):
            length=(w-38)*(1 if i % 3 != 2 else .72)
            self.path(f"{name}-line-{i}", f"M0 1 Q{length*.45} {3 if i % 2 else 0} {length} 2",
                      stroke="muted", sw=2, x=x+19, y=y+40+i*28, w=length, h=4)

    def book_page(self, name, x=134, y=205, width=370):
        scale=width/370
        self.path(name, "M0 7 C57 0 122 4 184 24 C246 4 313 0 370 7 L368 180 C304 169 246 172 184 192 C119 172 62 169 2 180 Z M184 24 L184 192",
                  "soft", "ink", 3, x=x, y=y, w=370, h=193)
        if scale != 1:
            self.ops.append(dict(type="resize", target=name, width=width, height=193*scale))
        for side in range(2):
            for i in range(4):
                self.path(f"{name}-text-{side}-{i}", "M0 1 Q59 0 127 10", stroke="muted", sw=2,
                          x=x+(25+side*190)*scale, y=y+(53+i*27)*scale, w=127*scale, h=12*scale)
                if scale != 1:
                    # The path is literal; create at its source size before resizing.
                    self.ops[-1]["width"], self.ops[-1]["height"] = 127, 12
                    self.ops.append(dict(type="resize", target=f"{name}-text-{side}-{i}", width=127*scale, height=12*scale))


ILLUSTRATIONS = ["signature-manuscript", "margin-notes", "loop-bound-book", "author-workbench",
                 "idea-on-paper", "local-copy", "revision-pages", "ready-to-share"]
ANIMATIONS = ["ink-blot-loader", "pen-loop-wait", "reading-breath", "margin-review", "saved-ink", "signature-settle"]
DECORATIONS = ["signature-underline", "loop-corner", "margin-gesture", "chapter-stop", "author-seal", "ink-emphasis"]
BACKGROUNDS = ["manuscript-paper", "green-margin", "midnight-signature", "editorial-frame"]
USES = {
    "signature-manuscript": "Opening, empty-state, or editorial artwork with the supplied pen gesture.",
    "margin-notes": "Review and annotation surfaces; a green margin gesture connects the notes.",
    "loop-bound-book": "Reading, chapters, or long-form writing, with the logo's green loop beneath the page.",
    "author-workbench": "Writing at work: a hand, pen, working page, and one signature ink mark.",
    "idea-on-paper": "A quiet idea or template illustration made from working notes.",
    "local-copy": "Local document artwork; pair with a textual privacy or storage explanation.",
    "revision-pages": "Revisions, comparisons, or history; handwritten green annotation on working pages.",
    "ready-to-share": "Export or completion artwork with one green editorial gesture.",
    "ink-blot-loader": "Compact indeterminate loading from the actual red ink blot and its fleck.",
    "pen-loop-wait": "A gentle processing loop using the supplied green loop, with a still ink accent.",
    "reading-breath": "A quiet waiting pose for a book or document panel.",
    "margin-review": "Review in progress; the supplied pen gesture travels down a margin.",
    "saved-ink": "A 180 ms save confirmation; hold the completed pose after one play.",
    "signature-settle": "A 180 ms pen-gesture settle for an explicit panel reveal or marketing transition.",
}


def illustration(slug, theme):
    a=BrandArt(theme=theme)
    if slug == "signature-manuscript":
        a.page("manuscript",202,70,232,306,7)
        a.gesture("signature",104,353,432)
        a.blot("signature-ink",85,382,19)
        a.path("pen", "M0 108 L51 0 L64 6 L11 115 L0 129 Z M0 129 L11 115", "green", "ink", 2,
               x=457,y=169,w=66,h=132)
    elif slug == "margin-notes":
        a.page("draft",139,94,225,291,7)
        a.page("note",426,148,129,128,2,"paper")
        a.gesture("note-connector",275,250,181)
        a.blot("note-ink",474,219,14)
        a.path("annotation", "M0 14 C2 -1 113 -2 122 11 C129 29 23 35 0 14 Z", stroke="green",sw=3,
               x=160,y=221,w=127,h=37)
    elif slug == "loop-bound-book":
        a.book_page("book",135,145)
        a.gesture("book-signature",115,358,410)
        a.blot("book-ink",94,385,17)
    elif slug == "author-workbench":
        a.page("working-page",125,89,242,286,7)
        # Stylized hand contours support the subject rather than becoming an app mascot.
        a.path("hand", "M0 90 C20 78 42 59 53 39 C60 27 62 0 73 0 C83 0 83 19 77 40 L96 24 C108 14 119 15 120 25 L98 45 C126 23 141 35 131 46 L109 62 C139 42 153 56 139 68 L113 84 C137 78 145 90 132 100 C111 116 79 131 56 133 L37 153 Z",
               "paper","ink",2.5,x=342,y=227,w=156,h=154)
        a.path("pen", "M0 110 L74 0 L84 7 L10 117 L0 129 Z", "green","ink",2,x=333,y=188,w=85,h=130)
        a.path("desk-line", "M0 1 Q215 -1 415 2",stroke="rule",sw=2,x=109,y=410,w=418,h=5)
        a.blot("working-ink",270,302,15)
    elif slug == "idea-on-paper":
        a.page("idea-back",192,93,206,268,4,"paper")
        a.page("idea-front",276,143,183,243,5)
        a.gesture("idea-gesture",122,365,396)
        a.blot("idea-ink",100,393,17)
        a.path("folded-corner", "M0 0 L25 27 L0 25 Z", "paper","ink",2,x=432,y=147,w=28,h=29)
    elif slug == "local-copy":
        a.page("local-draft",160,88,212,284,6)
        a.path("shield", "M0 14 Q66 17 101 0 Q132 17 198 14 L191 128 Q166 184 99 213 Q28 182 8 128 Z",
               "paper","ink",3,x=305,y=155,w=200,h=215)
        a.path("lock", "M0 32 L0 13 C0 -4 32 -4 32 13 L32 32 M-9 32 L41 32 L40 72 L-8 73 Z",
               stroke="ink",sw=3,x=389,y=224,w=43,h=76)
        a.blot("key-ink",398,274,10)
        a.gesture("local-signature",129,394,346)
    elif slug == "revision-pages":
        a.page("before",95,115,192,258,6,"paper")
        a.page("after",336,84,198,288,7)
        a.path("edit-strike", "M0 2 Q46 0 91 3",stroke="green",sw=3,x=115,y=210,w=93,h=5)
        a.gesture("revision-link",175,352,320)
        a.blot("revision-ink",143,375,16)
    elif slug == "ready-to-share":
        a.page("finished-page",196,85,231,293,7)
        a.path("hand-check", "M0 22 Q13 35 22 49 C33 40 52 16 77 0",stroke="green",sw=5,
               x=310,y=294,w=80,h=53)
        a.gesture("share-signature",120,383,385)
        a.blot("share-ink",101,410,16)
    return a


def animation(slug,theme):
    a=BrandArt(112,56,theme) if slug=="ink-blot-loader" else BrandArt(320,224,theme)
    once=slug in ("saved-ink","signature-settle")
    duration=TOKENS["motion"]["durationMs"] if once else 2400
    a.timeline(duration,not once)
    if slug=="ink-blot-loader":
        a.blot("ink",46,18,18)
        a.wave("ink","scale-x",.08,1,phase=-.25)
        a.wave("ink","scale-y",.08,1,phase=-.25)
        a.wave("ink","translate-y",2)
    elif slug=="pen-loop-wait":
        a.source_part("mark","loop","waiting-loop",92,65,1.3)
        a.wave("waiting-loop","translate-y",3)
        a.blot("waiting-ink",63,131,17)
    elif slug=="reading-breath":
        a.book_page("reading",74,64,172)
        targets=[op["name"] for op in a.ops if op["type"]=="shape"]
        a.group("reading-book",targets)
        a.wave("reading-book","translate-y",2)
        a.gesture("reading-gesture",60,165,205)
        a.blot("reading-ink",47,178,11)
    elif slug=="margin-review":
        a.page("review-page",72,32,137,162,4)
        a.source_part("mark","loop","margin-loop",199,56,.5)
        a.wave("margin-loop","translate-y",30,35,phase=-.25)
        a.blot("review-ink",89,47,9)
    elif slug=="saved-ink":
        a.page("saved-page",115,26,95,156,3)
        a.path("save-check","M0 13 L12 26 L40 0",stroke="green",sw=4,x=167,y=156,w=43,h=29)
        a.blot("saved-ink",103,180,12)
        a.group("save-confirmation",["save-check","saved-ink"])
        a.keys("save-confirmation","opacity",[(0,.35,"ease-out"),(duration,1,"ease-out")])
        a.keys("save-confirmation","translate-y",[(0,3,"ease-out"),(duration,0,"ease-out")])
    elif slug=="signature-settle":
        a.gesture("settling-pen",35,91,253)
        a.blot("settling-ink",24,110,12)
        a.group("settling-signature",["settling-pen","settling-ink"])
        a.keys("settling-signature","opacity",[(0,.35,"ease-out"),(duration,1,"ease-out")])
        a.keys("settling-signature","translate-y",[(0,4,"ease-out"),(duration,0,"ease-out")])
    return a


def decoration(slug,theme):
    a=BrandArt(640,320,theme)
    if slug=="signature-underline":
        a.gesture("underline",106,128,456)
        a.blot("underline-ink",84,163,18)
    elif slug=="loop-corner":
        a.source_part("mark","loop","corner-loop",194,108,2.2)
        a.blot("corner-ink",153,207,27)
    elif slug=="margin-gesture":
        a=BrandArt(320,560,theme)
        a.gesture("margin-stroke",34,235,430)
        a.ops.append(dict(type="rotate",target="margin-stroke",value=-90))
    elif slug=="chapter-stop":
        a.line("chapter-rule-left",110,160,291,160,"rule",2)
        a.line("chapter-rule-right",349,160,531,160,"rule",2)
        a.blot("chapter-ink",310,150,18)
    elif slug=="author-seal":
        a.path("seal-border","M92 1 C146 -2 187 41 186 91 C189 139 145 185 93 184 C40 187 -2 140 1 92 C-2 42 41 -2 92 1 Z",
               stroke="rule",sw=2,x=225,y=68,w=189,h=188)
        a.mark("supplied-mark",278,112,100)
    elif slug=="ink-emphasis":
        a.path("emphasis","M0 46 C-4 6 187 -7 253 6 C344 26 289 91 135 90 C51 91 -1 76 0 46 Z",
               stroke="green",sw=4,x=171,y=107,w=300,h=98)
        a.blot("emphasis-ink",471,126,21)
    return a


def background(slug):
    theme="dark" if slug=="midnight-signature" else "light"
    a=BrandArt(1600,900,theme,opaque=True)
    if slug=="manuscript-paper":
        a.gesture("footer-signature",930,718,514)
        a.blot("footer-ink",907,754,23)
        a.line("editorial-rule",112,786,798,786,"rule",1.5)
    elif slug=="green-margin":
        a.page("margin-page",1125,200,332,464,11,"paper")
        a.gesture("margin-signature",1030,706,462)
        a.blot("margin-ink",1004,739,22)
    elif slug=="midnight-signature":
        a.gesture("midnight-gesture",893,620,557)
        a.blot("midnight-ink",862,658,25)
        a.line("midnight-rule",112,786,750,786,"rule",1.5)
    elif slug=="editorial-frame":
        a.path("editorial-edge","M0 0 L0 668 Q590 673 1270 668 L1274 0",stroke="rule",sw=2,
               x=155,y=108,w=1278,h=677)
        a.gesture("frame-signature",931,747,468)
        a.blot("frame-ink",905,780,19)
    return a


def contact_sheet(out,entries):
    selected=[e for e in entries if e["theme"]!="dark"]
    paper=TOKENS["color"]["paper"]
    ink=TOKENS["color"]["ink"]
    cols, cw, ch=4,350,268
    im=Image.new("RGB",(1480,195+math.ceil(len(selected)/cols)*ch),paper)
    draw=ImageDraw.Draw(im)
    display=ImageFont.truetype(str(out/"fonts/IM-Fell-English-400.ttf"),43)
    label=ImageFont.truetype(str(out/"fonts/Manrope-400.ttf"),16)
    wordmark=Image.open(out/"reference/wordmark-light.png").convert("RGBA")
    wordmark.thumbnail((280,85),Image.Resampling.LANCZOS)
    im.paste(wordmark,(40,26),wordmark)
    draw.text((363,33),"Logo companion collection",font=display,fill=ink)
    draw.text((42,95),"24 designs · Supplied pen loops and ink marks · 44 variants · Made with Vixl",font=label,fill="#52615B")
    draw.line((40,141,1440,141),fill="#D7D0C3",width=1)
    for i,e in enumerate(selected):
        x,y=40+(i%cols)*cw,173+(i//cols)*ch
        preview=Image.open(out/e["preview"]).convert("RGBA")
        preview.thumbnail((cw-30,202),Image.Resampling.LANCZOS)
        if e["design"]=="midnight-signature":
            draw.rectangle((x+5,y+5,x+cw-13,y+211),fill=ink)
        im.paste(preview,(x+(cw-preview.width)//2,y+(207-preview.height)//2),preview)
        draw.text((x+10,y+222),e["title"],font=label,fill=ink)
    im.save(out/"contact-sheet.jpg",quality=94)


def gallery(out,entries):
    library.gallery(out,entries)
    file=out/"index.html"
    text=file.read_text()
    replacements={"ByAuthorX Creative Library":"ByAuthorX Logo Companion Library",
                  "ByAuthorX / Creative resources":"ByAuthorX / Logo companions",
                  "Room for what comes next.":"A familiar mark. New possibilities.",
                  "A shelf of original illustrations, small animations, and editorial details, ready for the app or the next campaign. Choose a theme, explore a collection, and download what you need.":
                  "Working pages, green pen loops, and a small red ink mark. A second collection built from the actual ByAuthorX logo geometry, ready for future app surfaces and marketing.",
                  "54 original designs":"24 logo-led designs", "#183e3d":"#252927", "#fffdf7":"#F4EEE3",
                  "#b5d5ce":"#367B6C", "#e5c890":"#AD283E", "#e8ede9":"#F4EEE3",
                  "#102f30":"#252927", "#edf0e9":"#FFFCF7", "#677b76":"#52615B",
                  "#526b62":"#52615B", "#d8e1d9":"#D7D0C3", "#bdcdc4":"#D7D0C3",
                  "#dce4dc":"#D7D0C3", "#bb7661":"#AD283E", "Georgia,serif":"'IM Fell English',serif",
                  "system-ui,sans-serif":"Manrope,system-ui,sans-serif"}
    for old,new in replacements.items():
        text=text.replace(old,new)
    fonts="@font-face{font-family:Manrope;src:url('fonts/Manrope-400.ttf')}@font-face{font-family:'IM Fell English';src:url('fonts/IM-Fell-English-400.ttf')}"
    text=text.replace("<style>","<style>"+fonts)
    text=text.replace("<option>icons</option>","")
    text=text.replace('<div class="brand">ByAuthorX / Logo companions</div>',
                      '<img src="reference/wordmark-light.png" alt="ByAuthorX" width="270" style="max-width:100%;height:auto"><div class="brand" style="margin-top:18px">Logo companions</div>')
    file.write_text(text)


def main():
    global TOKENS
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out",type=Path,default=OUT)
    parser.add_argument("--only")
    args=parser.parse_args()
    out=args.out
    out.mkdir(parents=True,exist_ok=True)
    (out/"reference").mkdir(exist_ok=True)
    references=[]
    for name,relative in SOURCES.items():
        source=ROOT/relative
        if not source.exists():
            source=out/"reference"/(name+Path(relative).suffix)
        if name=="tokens":
            TOKENS=json.loads(source.read_text())
        elif source.suffix == ".vixl":
            p=Project.load(source,workspace=ROOT)
            PARTS[name]={l["name"]:l for l in p.state["layers"] if l["type"]=="shape"}
        target=out/"reference"/(name+source.suffix)
        if source.resolve()!=target.resolve():
            shutil.copy2(source,target)
        references.append(dict(name=name,source=relative,sha256=hashlib.sha256(source.read_bytes()).hexdigest()))
    (out/"fonts").mkdir(exist_ok=True)
    for name in ["Manrope-400.ttf","Manrope-LICENSE.txt","IM-Fell-English-400.ttf","IM-Fell-English-LICENSE.txt"]:
        source=ROOT/"src/byax/resources/fonts"/name
        target=out/"fonts"/name
        if source.exists() and source.resolve()!=target.resolve():
            shutil.copy2(source,target)
    entries=[]
    for category,slugs,factory,themed in [("illustrations",ILLUSTRATIONS,illustration,True),
        ("animations",ANIMATIONS,animation,True),("decorations",DECORATIONS,decoration,True),
        ("backgrounds",BACKGROUNDS,background,False)]:
        for slug in slugs:
            if args.only and slug!=args.only:
                continue
            for theme in ("light","dark") if themed else (None,):
                art=factory(slug,theme) if themed else factory(slug)
                entry=library.build_one(out,category,slug,theme,art)
                entry["use"]=USES.get(slug,"Reusable logo-led editorial artwork for app or marketing.")
                entry["family"]="logo-companions"
                entries.append(entry)
    if args.only:
        library.write_json(out/f"{args.only}-review.json",entries)
        return
    manifest=dict(schema_version=1,name="ByAuthorX Logo Companion Library",design_count=24,variant_count=len(entries),
                  engine=dict(name="Vixl",version="0.26.0",commit=library.VIXL_COMMIT),
                  brand_tokens=TOKENS,reference_masters=references,assets=entries)
    library.write_json(out/"manifest.json",manifest)
    gallery(out,entries)
    contact_sheet(out,entries)
    library.write_json(out/"checksums.json",{str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in sorted(out.rglob("*")) if p.is_file() and p.name!="checksums.json"})
    print(json.dumps(dict(designs=24,variants=len(entries),findings=[e["id"] for e in entries if e["checks"]["issues"] or e["export_warnings"]]),indent=2))


if __name__=="__main__":
    main()
