"""Header icons drawn with Pillow (the Mac version used SF Symbols)."""
from __future__ import annotations

from PIL import Image, ImageDraw

SKY = (46, 168, 255)


def _tint(alpha: int, bg: tuple[int, int, int]) -> tuple[int, int, int]:
    a = alpha / 255
    return tuple(round(SKY[i] * a + bg[i] * (1 - a)) for i in range(3))


def kind_icon(kind: str | None, size: int = 40, dark: bool = False) -> Image.Image:
    bg = (32, 32, 32) if dark else (255, 255, 255)
    k = 8                                   # draw 8x then downsample for smooth edges
    S = size * k
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.23), fill=_tint(34, bg) + (255,))
    c = SKY + (255,)
    u = S / 40
    if kind == "audio":
        for i, h in enumerate([8, 16, 24, 14, 20, 10]):
            x = (8.5 + i * 4.2) * u
            d.rounded_rectangle([x, (20 - h / 2) * u, x + 2.6 * u, (20 + h / 2) * u], radius=1.3 * u, fill=c)
    elif kind == "video":
        d.rounded_rectangle([7 * u, 11 * u, 33 * u, 29 * u], radius=3 * u, outline=c, width=int(2.4 * u))
        d.polygon([(17 * u, 15 * u), (17 * u, 25 * u), (25.5 * u, 20 * u)], fill=c)
    elif kind == "image":
        d.rounded_rectangle([7 * u, 9 * u, 33 * u, 31 * u], radius=3 * u, outline=c, width=int(2.4 * u))
        d.ellipse([12 * u, 13.5 * u, 17 * u, 18.5 * u], fill=c)
        d.polygon([(9 * u, 29 * u), (17.5 * u, 21 * u), (22 * u, 25 * u), (26 * u, 20 * u), (31.5 * u, 29 * u)], fill=c)
    else:                                   # pdf / document / app
        d.rounded_rectangle([11 * u, 7 * u, 29 * u, 33 * u], radius=2.5 * u, outline=c, width=int(2.4 * u))
        for i in range(4 if kind in ("pdf", "document") else 3):
            y = (14 + i * 4.5) * u
            d.rounded_rectangle([15 * u, y, (25 if i < 3 else 21) * u, y + 1.8 * u], radius=u, fill=c)
    return im.resize((size, size), Image.LANCZOS)
