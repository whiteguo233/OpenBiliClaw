"""Compose Chrome Web Store listing screenshots from sanitized current UI."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "docs/images/chrome-web-store/source"
OUTPUT_DIR = ROOT / "docs/images/chrome-web-store"
CANVAS = (1280, 800)
PLATFORMS = (
    "B站",
    "小红书",
    "抖音",
    "TikTok",
    "YouTube",
    "X",
    "知乎",
    "Reddit",
    "Linux.do",
    "微博",
    "Bangumi",
    "V2EX",
    "GitHub",
    "Instagram",
)

BG = "#11151E"
INK = "#F8FAFC"
MUTED = "#B7C0CE"
LINE = "#3A4556"
PINK = "#FF6B96"
PINK_SOFT = "#FFE3EB"
BLUE = "#3186FF"
BLUE_SOFT = "#E8F2FF"
GREEN = "#18A66A"
GREEN_SOFT = "#E5F6EE"
ORANGE = "#E66A3B"


def font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/STHeiti Medium.ttc"
        if bold
        else "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
        if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf",
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


FONT_BRAND = font(21, bold=True)
FONT_KICKER = font(16, bold=True)
FONT_TITLE = font(48, bold=True)
FONT_SUBTITLE = font(23)
FONT_BODY = font(19)
FONT_SMALL = font(15)
FONT_CHIP = font(17, bold=True)


def _gradient() -> Image.Image:
    image = Image.new("RGB", CANVAS, BG)
    pixels = image.load()
    for y in range(CANVAS[1]):
        for x in range(CANVAS[0]):
            pink_weight = max(0.0, 1.0 - ((x - 80) ** 2 + (y - 40) ** 2) ** 0.5 / 760)
            blue_weight = max(0.0, 1.0 - ((x - 1180) ** 2 + (y - 720) ** 2) ** 0.5 / 900)
            base = (17, 21, 30)
            pixels[x, y] = tuple(
                int(
                    base[channel]
                    + pink_weight * ((78, 35, 58)[channel] - base[channel]) * 0.58
                    + blue_weight * ((28, 52, 82)[channel] - base[channel]) * 0.62
                )
                for channel in range(3)
            )
    return image


def _text_width(draw: ImageDraw.ImageDraw, text: str, face: ImageFont.ImageFont) -> int:
    box = draw.textbbox((0, 0), text, font=face)
    return box[2] - box[0]


def _brand(image: Image.Image, draw: ImageDraw.ImageDraw, index: int) -> None:
    icon_path = ROOT / "extension/icons/icon128.png"
    with Image.open(icon_path) as icon_source:
        icon = icon_source.convert("RGBA").resize((44, 44), Image.Resampling.LANCZOS)
    image.alpha_composite(icon, (58, 38))
    draw.text((116, 42), "OpenBiliClaw", font=FONT_BRAND, fill=INK)
    draw.text((116, 68), "本地优先的十四来源内容 Agent", font=FONT_SMALL, fill=MUTED)
    draw.text((1168, 48), f"0{index} / 03", font=FONT_KICKER, fill=MUTED)


def _headline(
    draw: ImageDraw.ImageDraw,
    kicker: str,
    title: str,
    subtitle: str,
    *,
    x: int = 60,
    y: int = 112,
    max_width: int | None = None,
) -> None:
    draw.text((x, y), kicker.upper(), font=FONT_KICKER, fill=ORANGE)
    draw.text((x, y + 30), title, font=FONT_TITLE, fill=INK)
    if max_width and _text_width(draw, subtitle, FONT_SUBTITLE) > max_width:
        midpoint = max(1, len(subtitle) // 2)
        split = subtitle.rfind("，", 0, midpoint + 8)
        if split < 0:
            split = midpoint
        draw.text((x, y + 94), subtitle[: split + 1], font=FONT_SUBTITLE, fill=MUTED)
        draw.text((x, y + 128), subtitle[split + 1 :], font=FONT_SUBTITLE, fill=MUTED)
    else:
        draw.text((x, y + 94), subtitle, font=FONT_SUBTITLE, fill=MUTED)


def _chip(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    label: str,
    *,
    fill: str = "#FFFFFF",
    color: str = INK,
    outline: str = LINE,
) -> int:
    width = _text_width(draw, label, FONT_CHIP) + 34
    draw.rounded_rectangle((x, y, x + width, y + 42), radius=21, fill=fill, outline=outline)
    draw.text((x + 17, y + 9), label, font=FONT_CHIP, fill=color)
    return x + width


def _draw_platform_chips(draw: ImageDraw.ImageDraw, *, x: int, y: int) -> int:
    start_x = x
    row_y = y
    colors = ((PINK_SOFT, PINK), (BLUE_SOFT, BLUE), (GREEN_SOFT, GREEN))
    for index, label in enumerate(PLATFORMS):
        width = _text_width(draw, label, FONT_CHIP) + 34
        if x > start_x and x + width > 1220:
            x = start_x
            row_y += 50
        fill, color = colors[index % len(colors)]
        x = _chip(draw, x, row_y, label, fill=fill, color=color, outline=fill) + 10
    return row_y + 42


def _rounded_screenshot(
    base: Image.Image,
    path: Path,
    box: tuple[int, int, int, int],
    *,
    fit: bool = True,
    radius: int = 24,
    centering: tuple[float, float] = (0.5, 0.5),
) -> None:
    if not path.exists():
        raise FileNotFoundError(f"missing sanitized source screenshot: {path}")
    x1, y1, x2, y2 = box
    size = (x2 - x1, y2 - y1)
    with Image.open(path) as source:
        source = source.convert("RGB")
        if fit:
            layer = ImageOps.fit(
                source,
                size,
                method=Image.Resampling.LANCZOS,
                centering=centering,
            )
        else:
            layer = ImageOps.contain(source, size, method=Image.Resampling.LANCZOS)
            padded = Image.new("RGB", size, "white")
            padded.paste(layer, ((size[0] - layer.width) // 2, (size[1] - layer.height) // 2))
            layer = padded
    shadow = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        (x1 + 7, y1 + 10, x2 + 7, y2 + 10),
        radius=radius,
        fill=(23, 23, 20, 28),
    )
    base.alpha_composite(shadow)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, *size), radius=radius, fill=255)
    base.paste(layer, (x1, y1), mask)
    ImageDraw.Draw(base).rounded_rectangle(box, radius=radius, outline="#D7D4CB", width=2)


def _base(index: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = _gradient().convert("RGBA")
    draw = ImageDraw.Draw(image)
    _brand(image, draw, index)
    return image, draw


def build_local_platform_slide(source_dir: Path) -> Image.Image:
    image, draw = _base(1)
    draw.text((60, 112), "14 SOURCES · ONE PRIVATE AGENT", font=FONT_KICKER, fill=ORANGE)
    draw.text((60, 144), "本地私有的", font=font(46, bold=True), fill=INK)
    draw.text((60, 202), "十四来源内容 Agent", font=font(46, bold=True), fill=INK)
    draw.text((60, 268), "把分散的信息流，变成真正懂你", font=FONT_SUBTITLE, fill=MUTED)
    draw.text((60, 302), "且可反馈的跨平台推荐。", font=FONT_SUBTITLE, fill=MUTED)
    _draw_platform_chips(draw, x=60, y=354)
    draw.rounded_rectangle((60, 468, 475, 688), radius=26, fill="#FFFFFFD9", outline=LINE)
    draw.text((84, 496), "你的数据，不是我们的数据", font=font(23, bold=True), fill=INK)
    for offset, text in enumerate(
        ("本地后端运行", "画像与反馈默认保存在本机", "状态文案不冒充实时登录验证")
    ):
        cy = 550 + offset * 42
        draw.ellipse((84, cy + 5, 94, cy + 15), fill=GREEN)
        draw.text((108, cy), text, font=FONT_BODY, fill=MUTED)
    _rounded_screenshot(image, source_dir / "desktop-recommend.png", (520, 128, 1218, 712))
    return image.convert("RGB")


def build_three_surfaces_slide(source_dir: Path) -> Image.Image:
    image, draw = _base(2)
    _headline(
        draw,
        "Three surfaces",
        "插件、PC、手机，一套体验",
        "浏览器侧边栏随手看，桌面端深度管理，手机端轻量反馈。",
    )
    labels = (("PC Web", 62), ("浏览器插件", 756), ("Mobile Web", 1004))
    for label, x in labels:
        draw.text((x, 238), label, font=FONT_KICKER, fill=MUTED)
    _rounded_screenshot(image, source_dir / "desktop-recommend.png", (58, 270, 734, 704), radius=22)
    _rounded_screenshot(
        image,
        source_dir / "extension-recommend.png",
        (752, 270, 984, 704),
        fit=False,
        radius=22,
    )
    _rounded_screenshot(
        image,
        source_dir / "mobile-recommend.png",
        (1000, 270, 1222, 704),
        fit=False,
        radius=22,
    )
    return image.convert("RGB")


def build_recommendation_slide(source_dir: Path) -> Image.Image:
    image, draw = _base(3)
    _headline(
        draw,
        "Cross-platform recommendations",
        "一次浏览，汇合十四个来源",
        "统一筛选、解释推荐理由，再用喜欢、不感兴趣和对话继续校准。",
        x=60,
        y=112,
        max_width=1120,
    )
    draw.rounded_rectangle((60, 276, 258, 674), radius=26, fill="#FFFFFFD9", outline=LINE)
    draw.text((84, 304), "不是简单聚合", font=font(23, bold=True), fill=INK)
    bullets = (
        ("跨平台混排", PINK),
        ("每条都有理由", BLUE),
        ("反馈即时进入画像", GREEN),
    )
    for index, (label, color) in enumerate(bullets):
        y = 374 + index * 82
        draw.rounded_rectangle((84, y, 108, y + 24), radius=8, fill=color)
        draw.text((84, y + 34), label, font=FONT_BODY, fill=MUTED)
    _rounded_screenshot(image, source_dir / "desktop-recommend.png", (286, 258, 1220, 704))
    return image.convert("RGB")


def build_profile_slide(source_dir: Path) -> Image.Image:
    image, draw = _base(4)
    _headline(
        draw,
        "Trainable private profile",
        "画像不是黑盒：看得见，也改得动",
        "兴趣、避雷、认知风格和推荐理由都可查看；反馈会继续修正它。",
    )
    _rounded_screenshot(image, source_dir / "desktop-profile.png", (58, 262, 970, 704))
    draw.rounded_rectangle((994, 262, 1222, 704), radius=26, fill="#FFFFFFD9", outline=LINE)
    draw.text((1022, 294), "你始终有控制权", font=font(22, bold=True), fill=INK)
    points = (
        ("查看", "画像如何理解你", PINK_SOFT, PINK),
        ("纠正", "兴趣与避雷方向", BLUE_SOFT, BLUE),
        ("反馈", "喜欢 / 少来点 / 对话", GREEN_SOFT, GREEN),
    )
    for index, (title, detail, fill, color) in enumerate(points):
        y = 364 + index * 98
        draw.rounded_rectangle((1020, y, 1198, y + 76), radius=18, fill=fill)
        draw.text((1038, y + 12), title, font=FONT_CHIP, fill=color)
        draw.text((1038, y + 42), detail, font=FONT_SMALL, fill=MUTED)
    return image.convert("RGB")


def build_settings_slide(source_dir: Path) -> Image.Image:
    image, draw = _base(5)
    _headline(
        draw,
        "Truthful status · Local data",
        "登录状态说人话，数据默认在本机",
        "区分“已保存凭据”“待验证”“无需登录”，不再把本地令牌误报成接入成功。",
        max_width=1100,
    )
    draw.rounded_rectangle((58, 286, 306, 704), radius=26, fill="#FFFFFFD9", outline=LINE)
    status_rows = (
        ("凭据已就绪", "只说明本地已保存", GREEN, GREEN_SOFT),
        ("状态待验证", "没有假装访问平台", BLUE, BLUE_SOFT),
        ("无需登录", "公开内容直接发现", MUTED, "#EFEEE9"),
    )
    draw.text((84, 314), "接入状态 ≠ 来源开关", font=font(22, bold=True), fill=INK)
    for index, (title, detail, color, fill) in enumerate(status_rows):
        y = 378 + index * 92
        draw.rounded_rectangle((82, y, 282, y + 70), radius=17, fill=fill)
        draw.ellipse((98, y + 17, 110, y + 29), fill=color)
        draw.text((122, y + 10), title, font=FONT_CHIP, fill=INK)
        draw.text((98, y + 40), detail, font=FONT_SMALL, fill=MUTED)
    _rounded_screenshot(
        image,
        source_dir / "desktop-settings.png",
        (332, 270, 1220, 704),
        centering=(0.5, 0.7),
    )
    return image.convert("RGB")


def _platform_row(draw: ImageDraw.ImageDraw, *, x: int, y: int) -> int:
    return _draw_platform_chips(draw, x=x, y=y)


def _status_legend(draw: ImageDraw.ImageDraw, *, x: int, y: int) -> None:
    rows = (
        ("凭据已就绪", "本地已保存", GREEN, "#173A31"),
        ("状态待验证", "没有假装访问平台", BLUE, "#1D3555"),
        ("无需登录", "公开内容直接发现", "#A8B0BC", "#303845"),
    )
    for index, (title, detail, color, fill) in enumerate(rows):
        top = y + index * 104
        draw.rounded_rectangle((x, top, x + 224, top + 84), radius=20, fill=fill, outline=LINE)
        draw.ellipse((x + 18, top + 20, x + 30, top + 32), fill=color)
        draw.text((x + 42, top + 11), title, font=FONT_CHIP, fill=INK)
        draw.text((x + 18, top + 48), detail, font=FONT_SMALL, fill=MUTED)


def build_hero_slide(source_dir: Path) -> Image.Image:
    image, draw = _base(1)
    draw.text((60, 112), "十四来源内容推荐，", font=font(48, bold=True), fill=INK)
    draw.text((60, 170), "数据默认留在本机", font=font(48, bold=True), fill=INK)
    draw.text((62, 234), "有头图、有理由，也能继续反馈调教。", font=FONT_SUBTITLE, fill=MUTED)
    chips_bottom = _platform_row(draw, x=60, y=272)
    screenshot_top = chips_bottom + 12
    _rounded_screenshot(
        image,
        source_dir / "desktop-recommend.png",
        (58, screenshot_top, 1222, 746),
        centering=(0.61, 0.28),
        radius=24,
    )
    return image.convert("RGB")


def build_concise_three_surfaces_slide(source_dir: Path) -> Image.Image:
    image, draw = _base(2)
    draw.text((60, 112), "PC、插件、手机，一套推荐体验", font=font(44, bold=True), fill=INK)
    draw.text((62, 171), "同一批内容，随手看、及时存、继续反馈。", font=FONT_SUBTITLE, fill=MUTED)
    labels = (("PC Web", 62), ("浏览器插件", 746), ("Mobile Web", 1000))
    for label, x in labels:
        draw.text((x, 214), label, font=FONT_KICKER, fill=MUTED)
    _rounded_screenshot(
        image,
        source_dir / "desktop-recommend.png",
        (58, 248, 716, 746),
        centering=(0.62, 0.48),
        radius=22,
    )
    _rounded_screenshot(
        image,
        source_dir / "extension-recommend.png",
        (738, 248, 982, 746),
        radius=22,
    )
    _rounded_screenshot(
        image,
        source_dir / "mobile-recommend.png",
        (998, 248, 1224, 746),
        radius=22,
    )
    return image.convert("RGB")


def build_truthful_status_slide(source_dir: Path) -> Image.Image:
    image, draw = _base(3)
    draw.text((60, 112), "登录状态说人话，数据默认在本机", font=font(44, bold=True), fill=INK)
    draw.text(
        (62, 171), "区分来源开关、凭据就绪、待验证与无需登录。", font=FONT_SUBTITLE, fill=MUTED
    )
    _status_legend(draw, x=60, y=264)
    _rounded_screenshot(
        image,
        source_dir / "desktop-settings.png",
        (314, 232, 1222, 746),
        centering=(0.64, 0.68),
        radius=24,
    )
    return image.convert("RGB")


def build_assets(source_dir: Path, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    builders = (
        ("01-seven-platform-recommendations.png", build_hero_slide),
        ("02-three-surfaces.png", build_concise_three_surfaces_slide),
        ("03-truthful-status-local-data.png", build_truthful_status_slide),
    )
    expected = {filename for filename, _ in builders}
    for stale in output_dir.glob("*.png"):
        if stale.name not in expected:
            stale.unlink()
    outputs: list[Path] = []
    for filename, builder in builders:
        image = builder(source_dir)
        if image.size != CANVAS:
            raise RuntimeError(f"invalid asset dimensions for {filename}: {image.size}")
        path = output_dir / filename
        image.convert("RGB").save(path, optimize=True)
        outputs.append(path)
        print(path)
    return outputs


if __name__ == "__main__":
    build_assets(SOURCE_DIR, OUTPUT_DIR)
