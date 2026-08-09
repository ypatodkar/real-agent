"""Stub sprite sets — two characters, nine poses each, drawn with PIL.

Stands in for the Imagen roster so the compositor can be proven without a model
or an API key. Deliberately crude: the point is that the pose enum, the canvas
geometry and the mouth-open variant all behave, not that it looks good.

Emits assets/fixtures/sprites/<character>/<pose>.png — RGBA, one canvas size.
"""

import pathlib

from PIL import Image, ImageDraw

W, H = 460, 660
OUT = pathlib.Path(__file__).resolve().parent.parent / "assets" / "fixtures" / "sprites"

# The closed pose set from ARCHITECTURE.md §4.4.2, plus the mouth-open variant.
POSES = [
    "talking",
    "talking_open",
    "listening",
    "nodding",
    "skeptical",
    "reacting",
    "laughing",
    "gesturing",
    "idle",
]

CHARACTERS = {
    "ch_01": {"skin": (238, 205, 176), "shirt": (54, 104, 148), "hair": (46, 38, 34)},
    "ch_02": {"skin": (222, 178, 140), "shirt": (150, 74, 64), "hair": (92, 62, 44)},
}

OUTLINE = (28, 30, 34)
LW = 5


def draw_body(d: ImageDraw.ImageDraw, c: dict, arms: str) -> None:
    d.rounded_rectangle([120, 400, 340, 660], 46, fill=c["shirt"], outline=OUTLINE, width=LW)

    if arms == "crossed":
        d.rounded_rectangle([132, 470, 328, 520], 25, fill=c["shirt"], outline=OUTLINE, width=LW)
        d.rounded_rectangle([132, 505, 328, 552], 24, fill=c["shirt"], outline=OUTLINE, width=LW)
    elif arms == "raised":
        d.rounded_rectangle([56, 372, 128, 500], 34, fill=c["shirt"], outline=OUTLINE, width=LW)
        d.ellipse([52, 340, 128, 412], fill=c["skin"], outline=OUTLINE, width=LW)
    elif arms == "open":
        d.rounded_rectangle([62, 440, 132, 500], 28, fill=c["shirt"], outline=OUTLINE, width=LW)
        d.rounded_rectangle([328, 440, 398, 500], 28, fill=c["shirt"], outline=OUTLINE, width=LW)
        d.ellipse([48, 448, 112, 512], fill=c["skin"], outline=OUTLINE, width=LW)
        d.ellipse([348, 448, 412, 512], fill=c["skin"], outline=OUTLINE, width=LW)


def draw_head(d: ImageDraw.ImageDraw, c: dict, dy: int) -> None:
    d.ellipse([132, 96 + dy, 328, 300 + dy], fill=c["skin"], outline=OUTLINE, width=LW)
    d.chord([132, 74 + dy, 328, 250 + dy], 180, 360, fill=c["hair"], outline=OUTLINE, width=LW)
    d.rounded_rectangle([196, 296 + dy, 264, 410], 22, fill=c["skin"], outline=OUTLINE, width=LW)


def draw_eyes(d: ImageDraw.ImageDraw, style: str, dy: int) -> None:
    if style == "closed":
        for x in (178, 250):
            d.arc([x, 190 + dy, x + 36, 214 + dy], 200, 340, fill=OUTLINE, width=LW)
        return

    for x in (180, 252):
        d.ellipse([x, 184 + dy, x + 32, 220 + dy], fill=(255, 255, 255), outline=OUTLINE, width=4)
        off = 6 if style == "side" else 0
        d.ellipse([x + 10 + off, 195 + dy, x + 24 + off, 209 + dy], fill=OUTLINE)

    if style == "wide":
        for x in (180, 252):
            d.ellipse([x - 5, 179 + dy, x + 37, 225 + dy], outline=OUTLINE, width=3)


def draw_brows(d: ImageDraw.ImageDraw, style: str, dy: int) -> None:
    if style == "raised_one":
        d.line([176, 168 + dy, 214, 158 + dy], fill=OUTLINE, width=LW + 1)
        d.line([250, 170 + dy, 288, 170 + dy], fill=OUTLINE, width=LW + 1)
    elif style == "raised":
        d.line([174, 160 + dy, 214, 154 + dy], fill=OUTLINE, width=LW + 1)
        d.line([250, 154 + dy, 290, 160 + dy], fill=OUTLINE, width=LW + 1)
    else:
        d.line([176, 168 + dy, 214, 166 + dy], fill=OUTLINE, width=LW + 1)
        d.line([250, 166 + dy, 288, 168 + dy], fill=OUTLINE, width=LW + 1)


def draw_mouth(d: ImageDraw.ImageDraw, style: str, dy: int) -> None:
    if style == "open":
        d.ellipse([202, 240 + dy, 258, 284 + dy], fill=(96, 44, 48), outline=OUTLINE, width=4)
    elif style == "wide_open":
        d.ellipse([192, 236 + dy, 268, 292 + dy], fill=(96, 44, 48), outline=OUTLINE, width=4)
        d.chord([192, 232 + dy, 268, 268 + dy], 0, 180, fill=(255, 255, 255))
    elif style == "smile":
        d.arc([196, 232 + dy, 264, 280 + dy], 10, 170, fill=OUTLINE, width=LW)
    elif style == "flat":
        d.line([204, 262 + dy, 256, 262 + dy], fill=OUTLINE, width=LW)
    elif style == "smirk":
        d.arc([200, 236 + dy, 260, 274 + dy], 15, 110, fill=OUTLINE, width=LW)
    else:  # small
        d.arc([206, 240 + dy, 254, 272 + dy], 20, 160, fill=OUTLINE, width=LW)


# pose -> (eyes, brows, mouth, arms, head_offset_y)
SPEC = {
    "talking":      ("open",   "neutral",    "small",     "none",    0),
    "talking_open": ("open",   "neutral",    "open",      "none",    0),
    "listening":    ("open",   "neutral",    "flat",      "none",    0),
    "nodding":      ("closed", "neutral",    "smile",     "none",   10),
    "skeptical":    ("side",   "raised_one", "smirk",     "crossed", 0),
    "reacting":     ("wide",   "raised",     "open",      "raised",  -6),
    "laughing":     ("closed", "raised",     "wide_open", "none",    -4),
    "gesturing":    ("open",   "raised",     "small",     "open",    0),
    "idle":         ("open",   "neutral",    "smile",     "none",    0),
}


def render(character: str, pose: str) -> Image.Image:
    c = CHARACTERS[character]
    eyes, brows, mouth, arms, dy = SPEC[pose]

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    draw_body(d, c, arms)
    draw_head(d, c, dy)
    draw_brows(d, brows, dy)
    draw_eyes(d, eyes, dy)
    draw_mouth(d, mouth, dy)
    return img


def main() -> None:
    for character in CHARACTERS:
        directory = OUT / character
        directory.mkdir(parents=True, exist_ok=True)
        for pose in POSES:
            render(character, pose).save(directory / f"{pose}.png")
        print(f"{character}  {len(POSES)} poses -> {directory}")

    print(f"\n{len(CHARACTERS) * len(POSES)} sprites total, canvas {W}x{H}")


if __name__ == "__main__":
    main()
