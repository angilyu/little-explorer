"""Draw the 1024x1024 app icon (a hot-air balloon over an open book) into the asset catalog, and the
beta app's icon (the same picture with a "BETA" ribbon across the top-right corner).

Usage: python3 scripts/make_app_icon.py   (needs Pillow)
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

S = 1024
K = 4  # supersampling for smooth edges
ASSETS = Path(__file__).resolve().parent.parent / "LittleExplorer/Assets.xcassets"
OUT = ASSETS / "AppIcon.appiconset/AppIcon.png"
BETA_OUT = ASSETS / "AppIcon-Beta.appiconset/AppIcon-Beta.png"
FONTS = ("/System/Library/Fonts/Avenir Next.ttc", "/System/Library/Fonts/HelveticaNeue.ttc")


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


def bold_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONTS:
        for index in range(12):
            try:
                font = ImageFont.truetype(path, size, index=index)
            except OSError:
                break
            if font.getname()[1] in ("Heavy", "Bold"):
                return font
    return ImageFont.load_default(size)


def beta(icon: Image.Image) -> Image.Image:
    """``icon`` with a purple "BETA" ribbon across its top-right corner, placed inside the rounded
    corner iOS cuts off so the whole word shows."""
    n = S * K
    img = icon.resize((n, n), Image.LANCZOS)
    width, middle = 180 * K, 330 * K  # ribbon width; how far its center line sits from the corner
    ribbon = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(ribbon)
    near, far = middle - width // 2, middle + width // 2
    shadow = 10 * K
    d.polygon([(n - far - shadow, 0), (n, far + shadow), (n, near + shadow), (n - near - shadow, 0)],
              fill=(40, 20, 70, 90))
    d.polygon([(n - far, 0), (n, far), (n, near), (n - near, 0)], fill=(120, 60, 200, 255))
    edge = 7 * K
    for offset in (near + 2 * edge, far - 2 * edge):
        d.line([(n - offset, 0), (n, offset)], fill=(255, 255, 255, 150), width=edge // 2)

    font = bold_font(112 * K)
    text = Image.new("RGBA", (n, 200 * K), (0, 0, 0, 0))
    td = ImageDraw.Draw(text)
    td.text((n // 2, 100 * K), "BETA", font=font, fill=(255, 255, 255, 255), anchor="mm")
    text = text.rotate(-45, expand=True, resample=Image.BICUBIC)
    # The ribbon's center line crosses the corner's diagonal at (n - middle/2, middle/2).
    cx, cy = n - middle // 2, middle // 2
    ribbon.alpha_composite(text, (cx - text.width // 2, cy - text.height // 2))
    img = img.convert("RGBA")
    img.alpha_composite(ribbon)
    return img.convert("RGB").resize((S, S), Image.LANCZOS)


def save(img: Image.Image, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, optimize=True)
    (out.parent / "Contents.json").write_text(
        '{\n  "images" : [\n    {\n      "filename" : "' + out.name + '",\n      "idiom" : "universal",\n'
        '      "platform" : "ios",\n      "size" : "1024x1024"\n    }\n  ],\n'
        '  "info" : {\n    "author" : "xcode",\n    "version" : 1\n  }\n}\n'
    )
    print(out)


if __name__ == "__main__":
    icon = draw()
    save(icon, OUT)
    save(beta(icon), BETA_OUT)
