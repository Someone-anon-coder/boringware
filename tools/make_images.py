"""Make the hub's brand images: favicon.ico, apple-touch-icon.png and the default social card.

Run once after changing the look; the outputs are committed in static/.
    python3 tools/make_images.py
Needs Pillow and the DejaVu or Noto Sans fonts (any Linux desktop has them).
favicon.svg is written by hand and lives in static/ already.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

STATIC = Path(__file__).resolve().parent.parent / "static"
BG = (28, 32, 40)  # matches the BoringWare logo in the Splitmark repo (docs/branding)
FG = (255, 255, 255)
MUTED = (200, 207, 220)
ACCENT = (122, 162, 255)

FONT_CANDIDATES = {
    "bold": [
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
    "regular": [
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
}


def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES[weight]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise SystemExit(f"No {weight} font found; install fonts-noto-core or fonts-dejavu")


def mark(size: int, fill: tuple = BG) -> Image.Image:
    """The BW mark on a rounded dark square, drawn at 4x and scaled down for clean edges."""
    big = size * 4
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, big - 1, big - 1], radius=big // 5, fill=fill)
    f = font("bold", int(big * 0.5))
    d.text((big / 2, big / 2), "BW", font=f, fill=FG, anchor="mm")
    return img.resize((size, size), Image.LANCZOS)


def og_card() -> Image.Image:
    w, h = 1200, 630
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    tile = mark(160, fill=(52, 60, 76))
    img.paste(tile, (80, 80), tile)
    d.text((80, 300), "BoringWare", font=font("bold", 96), fill=FG)
    d.text((80, 420), "Small Windows tools for boring office jobs.", font=font("regular", 44), fill=MUTED)
    d.text((80, 520), "boringware.pages.dev", font=font("regular", 34), fill=ACCENT)
    return img


def main() -> None:
    mark(256).save(STATIC / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    mark(180).convert("RGB").save(STATIC / "apple-touch-icon.png")
    og_card().save(STATIC / "img" / "og-default.png", optimize=True)
    print("wrote favicon.ico, apple-touch-icon.png, img/og-default.png")


if __name__ == "__main__":
    main()
