"""Draw the 1024x1024 app icon (a hot-air balloon over an open book) into the asset catalog.

Usage: python3 scripts/make_app_icon.py   (needs Pillow)
"""

from pathlib import Path

from PIL import Image, ImageDraw

S = 1024
K = 4  # supersampling for smooth edges
OUT = Path(__file__).resolve().parent.parent / "LittleExplorer/Assets.xcassets/AppIcon.appiconset/AppIcon.png"


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def draw() -> Image.Image:
    n = S * K
    img = Image.new("RGB", (n, n))
    d = ImageDraw.Draw(img)

    top, bottom = (110, 190, 245), (255, 214, 170)
    for y in range(n):
        d.line([(0, y), (n, y)], fill=lerp(top, bottom, y / n))

    def p(*xy):
        return [v * K for v in xy]

    # clouds
    for cx, cy, r in [(190, 300, 60), (250, 280, 75), (320, 305, 55), (760, 210, 50), (815, 190, 65), (870, 215, 45)]:
        d.ellipse(p(cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255))

    # balloon envelope: a circle plus a taper toward the basket, in stripes
    bx, by, br = 512, 345, 225
    env = Image.new("L", (n, n), 0)
    e = ImageDraw.Draw(env)
    e.ellipse(p(bx - br, by - br, bx + br, by + br), fill=255)
    e.polygon(p(bx - 205, by + 105, bx + 205, by + 105, bx + 70, by + 330, bx - 70, by + 330), fill=255)
    stripes = Image.new("RGB", (n, n))
    sd = ImageDraw.Draw(stripes)
    colors = [(235, 70, 70), (255, 200, 60), (235, 70, 70), (70, 160, 230), (235, 70, 70), (255, 200, 60), (235, 70, 70)]
    w = 2 * br / len(colors)
    for i, c in enumerate(colors):
        x0 = bx - br + i * w
        sd.rectangle(p(x0, 0, x0 + w + 1, S), fill=c)
    img.paste(stripes, (0, 0), env)

    # ropes and basket
    for x0, x1 in [(bx - 66, bx - 48), (bx + 66, bx + 48)]:
        d.line(p(x0, by + 330, x1, by + 385), fill=(120, 80, 50), width=8 * K)
    d.rounded_rectangle(p(bx - 58, by + 380, bx + 58, by + 450), radius=14 * K, fill=(170, 110, 60))
    d.line(p(bx - 58, by + 405, bx + 58, by + 405), fill=(130, 80, 40), width=6 * K)

    # open book
    cy = 840
    d.polygon(p(110, cy - 40, 512, cy + 10, 914, cy - 40, 914, cy + 110, 512, cy + 160, 110, cy + 110), fill=(40, 90, 170))
    d.polygon(p(140, cy - 70, 505, cy - 20, 505, cy + 130, 140, cy + 80), fill=(255, 252, 244))
    d.polygon(p(519, cy - 20, 884, cy - 70, 884, cy + 80, 519, cy + 130), fill=(255, 252, 244))
    for i in range(3):
        o = 25 + i * 35
        d.line(p(190, cy - 35 + o, 460, cy + 2 + o), fill=(200, 205, 215), width=7 * K)
        d.line(p(564, cy + 2 + o, 834, cy - 35 + o), fill=(200, 205, 215), width=7 * K)

    return img.resize((S, S), Image.LANCZOS)


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    draw().save(OUT, optimize=True)
    (OUT.parent / "Contents.json").write_text(
        '{\n  "images" : [\n    {\n      "filename" : "AppIcon.png",\n      "idiom" : "universal",\n'
        '      "platform" : "ios",\n      "size" : "1024x1024"\n    }\n  ],\n'
        '  "info" : {\n    "author" : "xcode",\n    "version" : 1\n  }\n}\n'
    )
    print(OUT)
