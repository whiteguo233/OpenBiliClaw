#!/usr/bin/env python3
"""Build the README hero demo from local screenshots.

Usage:
  python3 -m pip install Pillow
  python3 scripts/build_readme_hero_demo.py

The script intentionally uses only screenshots already committed under
docs/images/. It produces docs/images/hero-demo.png plus localized
docs/images/hero-demo-zh.gif and docs/images/hero-demo-en.gif for the
README first screen. docs/images/hero-demo.gif is kept as an English
compatibility alias.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from textwrap import wrap

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Pillow is required to build the README hero demo.\n"
        "Install it with: python3 -m pip install Pillow\n"
        "Then rerun: python3 scripts/build_readme_hero_demo.py"
    ) from exc


ROOT = Path(__file__).resolve().parents[1]
IMAGE_DIR = ROOT / "docs" / "images"
OUTPUT_PNG = IMAGE_DIR / "hero-demo.png"
OUTPUT_GIF = IMAGE_DIR / "hero-demo.gif"
OUTPUT_GIF_ZH = IMAGE_DIR / "hero-demo-zh.gif"
OUTPUT_GIF_EN = IMAGE_DIR / "hero-demo-en.gif"

CANVAS = (1280, 720)
FRAME_DURATION_MS = 2500
PANEL_W = 286
PANEL_H = 452
PANEL_TOP = 198
PANEL_GAP = 18
PANEL_X = 36

BG = "#f8fafc"
INK = "#102033"
MUTED = "#617083"
LINE = "#d8e1ea"
BLUE = "#2563eb"
GREEN = "#16a34a"
ORANGE = "#f97316"
PURPLE = "#7c3aed"
RED = "#dc2626"


def font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    """Return a readable font across macOS, Linux, and minimal CI images."""
    macos = "/System/Library/Fonts/Supplemental"
    candidates = [
        "/System/Library/Fonts/STHeiti Medium.ttc" if bold else "/System/Library/Fonts/STHeiti Light.ttc",
        f"{macos}/Songti.ttc",
        f"{macos}/Arial Bold.ttf" if bold else f"{macos}/Arial.ttf",
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


FONT_TITLE = font(38, bold=True)
FONT_SUBTITLE = font(20)
FONT_PANEL_TITLE = font(23, bold=True)
FONT_SMALL = font(14)
FONT_CHIP = font(15, bold=True)


def text_size(draw: ImageDraw.ImageDraw, text: str, face: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=face)
    return box[2] - box[0], box[3] - box[1]


def rounded_thumbnail(path: Path, size: tuple[int, int]) -> Image.Image:
    if not path.exists():
        raise SystemExit(f"Missing screenshot: {path}")
    with Image.open(path) as source:
        source = source.convert("RGB")
        thumb = ImageOps.contain(source, size, Image.Resampling.LANCZOS)

    layer = Image.new("RGB", size, "white")
    x = (size[0] - thumb.width) // 2
    y = (size[1] - thumb.height) // 2
    layer.paste(thumb, (x, y))

    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0], size[1]), radius=18, fill=255)
    rounded = Image.new("RGB", size, "white")
    rounded.paste(layer, (0, 0), mask)
    return rounded


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    text: str,
    xy: tuple[int, int],
    width_chars: int,
    face: ImageFont.ImageFont,
    fill: str,
    line_gap: int = 5,
) -> int:
    x, y = xy
    for line in wrap(text, width=width_chars):
        draw.text((x, y), line, font=face, fill=fill)
        _, h = text_size(draw, line, face)
        y += h + line_gap
    return y


def draw_chip(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    label: str,
    fill: str,
    text_fill: str = "white",
) -> tuple[int, int]:
    x, y = xy
    w, h = text_size(draw, label, FONT_CHIP)
    pad_x, pad_y = 14, 8
    rect = (x, y, x + w + pad_x * 2, y + h + pad_y * 2)
    draw.rounded_rectangle(rect, radius=16, fill=fill)
    draw.text((x + pad_x, y + pad_y - 1), label, font=FONT_CHIP, fill=text_fill)
    return rect[2], rect[3]


def draw_arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int], fill: str = LINE) -> None:
    sx, sy = start
    ex, ey = end
    draw.line((sx, sy, ex, ey), fill=fill, width=3)
    draw.polygon([(ex, ey), (ex - 10, ey - 6), (ex - 10, ey + 6)], fill=fill)


def draw_panel(
    base: Image.Image,
    index: int,
    title: str,
    body: str,
    screenshot: Path,
    accent: str,
    active: bool,
    overlay: list[tuple[str, str]],
) -> None:
    draw = ImageDraw.Draw(base)
    x = PANEL_X + index * (PANEL_W + PANEL_GAP)
    y = PANEL_TOP
    border = accent if active else LINE
    width = 4 if active else 2
    fill = "#ffffff" if active else "#fbfdff"

    draw.rounded_rectangle((x, y, x + PANEL_W, y + PANEL_H), radius=24, fill=fill, outline=border, width=width)
    draw.text((x + 20, y + 20), title, font=FONT_PANEL_TITLE, fill=INK)
    draw_wrapped(draw, body, (x + 20, y + 56), 28, FONT_SMALL, MUTED, line_gap=4)

    thumb = rounded_thumbnail(screenshot, (PANEL_W - 38, 246))
    base.paste(thumb, (x + 19, y + 112))
    draw.rounded_rectangle((x + 19, y + 112, x + PANEL_W - 19, y + 358), radius=18, outline="#e5edf5", width=1)

    chip_y = y + 374
    chip_x = x + 20
    for label, color in overlay:
        next_x, next_y = draw_chip(draw, (chip_x, chip_y), label, color)
        chip_x = next_x + 8
        if chip_x > x + PANEL_W - 90:
            chip_x = x + 20
            chip_y = next_y + 8


def build_frame(active: int | None = None) -> Image.Image:
    base = Image.new("RGB", CANVAS, BG)
    draw = ImageDraw.Draw(base)

    draw.text((36, 30), "OpenBiliClaw in 10 seconds", font=FONT_TITLE, fill=INK)
    draw.text(
        (38, 78),
        "Cross-platform signals become a private taste profile, reasoned recommendations, and a feedback loop.",
        font=FONT_SUBTITLE,
        fill=MUTED,
    )

    x, y = 38, 126
    for label, color in [
        ("Bilibili", BLUE),
        ("Xiaohongshu", RED),
        ("Douyin", INK),
        ("YouTube", RED),
        ("Web", PURPLE),
    ]:
        x, _ = draw_chip(draw, (x, y), label, color)
        x += 8
    draw_arrow(draw, (x + 6, y + 16), (x + 74, y + 16), "#94a3b8")
    x += 88
    x, _ = draw_chip(draw, (x, y), "Local backend", GREEN)
    draw_arrow(draw, (x + 12, y + 16), (x + 78, y + 16), "#94a3b8")
    draw_chip(draw, (x + 92, y), "SQLite on your machine", ORANGE)

    panels = [
        (
            "1. Signals in",
            "The extension reads your own logged-in sessions across platforms.",
            IMAGE_DIR / "desktop-home.png",
            BLUE,
            [("sources", BLUE), ("local API", GREEN)],
        ),
        (
            "2. Taste profile",
            "The backend turns behavior into interests, cognitive style, and deep needs.",
            IMAGE_DIR / "desktop-profile.png",
            PURPLE,
            [("interests", PURPLE), ("MBTI", BLUE), ("needs", ORANGE)],
        ),
        (
            "3. Reasons",
            "Cards explain why a video or note fits you instead of saying guess you like.",
            IMAGE_DIR / "desktop-cards.png",
            ORANGE,
            [("why this fits", ORANGE), ("mixed sources", BLUE)],
        ),
        (
            "4. Feedback loop",
            "Like, not interested, and chat feedback tune what comes next.",
            IMAGE_DIR / "mobile-recommend.png",
            GREEN,
            [("Like", GREEN), ("Not interested", RED), ("Chat", PURPLE)],
        ),
    ]

    for idx, (title, body, screenshot, accent, overlay) in enumerate(panels):
        draw_panel(base, idx, title, body, screenshot, accent, active is None or active == idx, overlay)
        if idx < 3:
            arrow_y = PANEL_TOP + PANEL_H // 2
            arrow_x = PANEL_X + idx * (PANEL_W + PANEL_GAP) + PANEL_W + 4
            draw_arrow(draw, (arrow_x, arrow_y), (arrow_x + PANEL_GAP - 4, arrow_y), "#cbd5e1")

    return base


def draw_slide_header(
    draw: ImageDraw.ImageDraw,
    step: str,
    title: str,
    subtitle: str,
    accent: str,
) -> None:
    draw.rounded_rectangle((52, 44, 146, 100), radius=28, fill=accent)
    draw.text((79, 57), step, font=font(28, bold=True), fill="white")
    draw.text((172, 38), title, font=font(46, bold=True), fill=INK)
    draw_wrapped(draw, subtitle, (174, 94), 58, font(22), MUTED, line_gap=7)


def build_gif_slide(index: int, lang: str) -> Image.Image:
    base = Image.new("RGB", CANVAS, BG)
    draw = ImageDraw.Draw(base)

    data = localized_slides(lang)[index]
    draw_slide_header(draw, data["step"], data["title"], data["subtitle"], data["accent"])

    left_x = 62
    chip_y = 190
    for label, color in data["chips"]:
        left_x, _ = draw_chip(draw, (left_x, chip_y), label, color)
        left_x += 10

    flow_y = 276
    x = 76
    for i, (label, color) in enumerate(data["callouts"]):
        x, _ = draw_chip(draw, (x, flow_y), label, color)
        if i < len(data["callouts"]) - 1:
            draw_arrow(draw, (x + 12, flow_y + 16), (x + 74, flow_y + 16), "#94a3b8")
            x += 88

    bullets = data["bullets"]
    bullet_y = 378
    for bullet in bullets:
        draw.ellipse((78, bullet_y + 7, 88, bullet_y + 17), fill=data["accent"])
        draw_wrapped(draw, bullet, (104, bullet_y), 45, font(21), INK, line_gap=6)
        bullet_y += 58

    screenshot_size = data["screen_size"]
    thumb = rounded_thumbnail(data["screenshot"], screenshot_size)
    screen_x = 1280 - screenshot_size[0] - 72
    screen_y = 210 if index != 3 else 178
    draw.rounded_rectangle(
        (screen_x - 16, screen_y - 16, screen_x + screenshot_size[0] + 16, screen_y + screenshot_size[1] + 16),
        radius=28,
        fill="#ffffff",
        outline="#dbe5ef",
        width=2,
    )
    base.paste(thumb, (screen_x, screen_y))

    draw.text((68, 660), data["footer"], font=FONT_SMALL, fill=MUTED)
    return base


def localized_slides(lang: str) -> list[dict[str, object]]:
    if lang == "zh":
        return [
            {
                "step": "1",
                "title": "跨平台信号留在本地",
                "subtitle": "浏览器插件把 B 站、小红书、抖音、YouTube 和 Web 的兴趣信号连接到你自己的后端。",
                "accent": BLUE,
                "screenshot": IMAGE_DIR / "desktop-home.png",
                "screen_size": (548, 330),
                "chips": [("B 站", BLUE), ("小红书", RED), ("抖音", INK), ("YouTube", RED), ("Web", PURPLE)],
                "callouts": [("浏览器插件", BLUE), ("本地后端", GREEN), ("SQLite", ORANGE)],
                "bullets": [
                    "跨平台信号沉淀成你自己的记忆。",
                    "画像和推荐池默认由你本机控制。",
                    "每一次反馈都会影响下一轮发现。",
                ],
                "footer": "OpenBiliClaw：本地优先 · 跨平台 · 自进化内容发现",
            },
            {
                "step": "2",
                "title": "生成私有兴趣画像",
                "subtitle": "OpenBiliClaw 从使用、反馈和对话里理解兴趣、认知风格、MBTI 信号和深层需求。",
                "accent": PURPLE,
                "screenshot": IMAGE_DIR / "desktop-profile.png",
                "screen_size": (590, 350),
                "chips": [("兴趣", PURPLE), ("MBTI", BLUE), ("需求", ORANGE), ("风格", GREEN)],
                "callouts": [("不需要平台账号", INK), ("本机 SQLite", ORANGE)],
                "bullets": [
                    "画像来自真实行为，不是手填标签。",
                    "LLM 和 embedding 服务按你的配置调用。",
                    "SQLite 数据库默认留在你的机器上。",
                ],
                "footer": "OpenBiliClaw：本地优先 · 跨平台 · 自进化内容发现",
            },
            {
                "step": "3",
                "title": "推荐卡片解释原因",
                "subtitle": "每张卡片都会说明为什么适合你，让推荐更像懂你的朋友，而不是黑盒信息流。",
                "accent": ORANGE,
                "screenshot": IMAGE_DIR / "desktop-cards.png",
                "screen_size": (590, 350),
                "chips": [("为什么推荐", ORANGE), ("混合来源", BLUE), ("惊喜", PURPLE)],
                "callouts": [("推荐理由", ORANGE), ("打开原站", BLUE)],
                "bullets": [
                    "每张卡片都有自然语言推荐理由。",
                    "推荐池混合平台和主题，不越刷越窄。",
                    "你可以打开、收藏、跳过或继续聊。",
                ],
                "footer": "OpenBiliClaw：本地优先 · 跨平台 · 自进化内容发现",
            },
            {
                "step": "4",
                "title": "反馈调教下一批内容",
                "subtitle": "喜欢、不感兴趣、收藏和聊天反馈会立刻影响 OpenBiliClaw 下一轮探索方向。",
                "accent": GREEN,
                "screenshot": IMAGE_DIR / "mobile-recommend.png",
                "screen_size": (270, 430),
                "chips": [("喜欢", GREEN), ("不感兴趣", RED), ("聊天", PURPLE), ("收藏", ORANGE)],
                "callouts": [("反馈闭环", GREEN), ("下一批更准", PURPLE)],
                "bullets": [
                    "正反馈会强化这个方向。",
                    "负反馈会清掉你不想看的内容。",
                    "聊天反馈能教会更细腻的口味。",
                ],
                "footer": "OpenBiliClaw：本地优先 · 跨平台 · 自进化内容发现",
            },
        ]
    return [
        {
            "step": "1",
            "title": "Signals stay local",
            "subtitle": "Your browser extension connects Bilibili, Xiaohongshu, Douyin, YouTube, and the open web to your own backend.",
            "accent": BLUE,
            "screenshot": IMAGE_DIR / "desktop-home.png",
            "screen_size": (548, 330),
            "chips": [("Bilibili", BLUE), ("Xiaohongshu", RED), ("Douyin", INK), ("YouTube", RED), ("Web", PURPLE)],
            "callouts": [("Extension", BLUE), ("Local backend", GREEN), ("SQLite", ORANGE)],
            "bullets": [
                "Cross-source signals become your own memory.",
                "Your profile and recommendation pool stay under your control.",
                "Every feedback action changes the next discovery cycle.",
            ],
            "footer": "OpenBiliClaw: local-first, cross-platform, self-improving discovery",
        },
        {
            "step": "2",
            "title": "A private taste profile",
            "subtitle": "OpenBiliClaw turns usage, feedback, and dialogue into interests, cognitive style, MBTI signals, and deeper needs.",
            "accent": PURPLE,
            "screenshot": IMAGE_DIR / "desktop-profile.png",
            "screen_size": (590, 350),
            "chips": [("Interests", PURPLE), ("MBTI", BLUE), ("Needs", ORANGE), ("Style", GREEN)],
            "callouts": [("No platform account", INK), ("Local SQLite", ORANGE)],
            "bullets": [
                "Profile evolves from actual behavior, not manual tags.",
                "LLM and embedding providers follow your configuration.",
                "The SQLite database remains on your machine by default.",
            ],
            "footer": "OpenBiliClaw: local-first, cross-platform, self-improving discovery",
        },
        {
            "step": "3",
            "title": "Recommendations with reasons",
            "subtitle": "Cards explain why each item fits you, so recommendations feel like a thoughtful friend instead of a black-box feed.",
            "accent": ORANGE,
            "screenshot": IMAGE_DIR / "desktop-cards.png",
            "screen_size": (590, 350),
            "chips": [("Why this fits", ORANGE), ("Mixed sources", BLUE), ("Surprise", PURPLE)],
            "callouts": [("Reasoned card", ORANGE), ("Open the source", BLUE)],
            "bullets": [
                "Each card carries a plain-language reason.",
                "The pool mixes platforms and topics instead of narrowing down.",
                "You can open, save, dismiss, or discuss a recommendation.",
            ],
            "footer": "OpenBiliClaw: local-first, cross-platform, self-improving discovery",
        },
        {
            "step": "4",
            "title": "Feedback trains the next batch",
            "subtitle": "Like, not interested, save, and chat feedback immediately shape what OpenBiliClaw explores next.",
            "accent": GREEN,
            "screenshot": IMAGE_DIR / "mobile-recommend.png",
            "screen_size": (270, 430),
            "chips": [("Like", GREEN), ("Not interested", RED), ("Chat", PURPLE), ("Save", ORANGE)],
            "callouts": [("Feedback loop", GREEN), ("Better next batch", PURPLE)],
            "bullets": [
                "Positive feedback reinforces a direction.",
                "Negative feedback clears what you do not want.",
                "Chat feedback teaches nuanced taste over time.",
            ],
            "footer": "OpenBiliClaw: local-first, cross-platform, self-improving discovery",
        },
    ]


def save_gif(frames: list[Image.Image], output: Path) -> None:
    quantized = [frame.quantize(colors=192) for frame in frames]
    quantized[0].save(
        output,
        save_all=True,
        append_images=quantized[1:],
        duration=FRAME_DURATION_MS,
        loop=0,
        optimize=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build README hero demo assets.")
    parser.add_argument(
        "--png-only",
        action="store_true",
        help="Only write docs/images/hero-demo.png; skip localized GIFs.",
    )
    args = parser.parse_args()

    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    static = build_frame(active=None)
    static.save(OUTPUT_PNG, optimize=True)

    if not args.png_only:
        zh_frames = [build_gif_slide(i, "zh") for i in range(4)]
        en_frames = [build_gif_slide(i, "en") for i in range(4)]
        save_gif(zh_frames, OUTPUT_GIF_ZH)
        save_gif(en_frames, OUTPUT_GIF_EN)
        shutil.copyfile(OUTPUT_GIF_EN, OUTPUT_GIF)

    print(f"Wrote {OUTPUT_PNG.relative_to(ROOT)}")
    if not args.png_only:
        print(f"Wrote {OUTPUT_GIF_ZH.relative_to(ROOT)}")
        print(f"Wrote {OUTPUT_GIF_EN.relative_to(ROOT)}")
        print(f"Wrote {OUTPUT_GIF.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
