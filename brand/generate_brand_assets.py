#!/usr/bin/env python3
"""生成 PLAT 助手品牌资产（同名覆盖 Octop 构建产物用）。

按 octop 原始资产的真实尺寸输出，路径引用不变、布局零位移：
- logo_horizontal_{dark,white}.png  753×289（横版字标：双圆标 + "PLAT 助手"）
- logo_vertical_{dark,white}.svg    竖版（启动 splash 用）
- favico.svg                        标签页图标（双圆标）
- apple-touch-icon.png / pwa-192.png / pwa-512.png  渐变底 + 白色双圆标

dark = 用于浅色背景（深色内容）；white = 用于深色背景（白色内容），
命名与 octop 现有引用约定一致（Header/Login/Splash 均按 isDark 切换）。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "assets"
OUT.mkdir(parents=True, exist_ok=True)

PLAT_BLUE = (6, 104, 249, 255)  # #0668F9
PLAT_TEAL = (31, 168, 160, 255)  # #1FA8A0
INK = (11, 31, 58, 255)  # #0B1F3A
WHITE = (255, 255, 255, 255)

FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
]


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    raise SystemExit("未找到可用中文字体，请检查 FONT_CANDIDATES")


def draw_mark(
    img: Image.Image,
    cx: float,
    cy: float,
    r: float,
    stroke: int,
    big_color: tuple,
    small_color: tuple,
    inner_fill: tuple | None = None,
) -> None:
    """双圆对话标：大圆描边 + 小圆实心（右下交叠），可选大圆内淡填充。"""
    d = ImageDraw.Draw(img)
    big = (cx - r, cy - r, cx + r, cy + r)
    small_r = r * 0.66
    scx, scy = cx + r * 0.52, cy + r * 0.52
    small = (scx - small_r, scy - small_r, scx + small_r, scy + small_r)
    if inner_fill is not None:
        d.ellipse(big, fill=inner_fill)
    d.ellipse(big, outline=big_color, width=stroke)
    d.ellipse(small, fill=small_color)


def horizontal_logo(text_color: tuple, mark_stroke_scale: float = 1.0) -> Image.Image:
    """753×289 横版字标（双圆标 + "PLAT"），4x 超采样抗锯齿。

    内容只占画布左 ~55%，右侧留白——侧边栏 logo 右侧的版本徽章
    （CurrentVersionBadge / AppVersionBadge）紧跟图片框渲染，若字标
    填满画布会与徽章零间距叠压。留白后可见宽度约 70px，间距自然分开。
    """
    S = 4
    W, H = 753 * S, 289 * S
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    mark_d = int(H * 0.66)
    r = mark_d / 2
    stroke = int(mark_d * 0.135 * mark_stroke_scale)
    draw_mark(
        img,
        cx=int(mark_d / 2 + 8 * S),
        cy=int(H / 2),
        r=r,
        stroke=stroke,
        big_color=PLAT_BLUE,
        small_color=PLAT_TEAL,
    )
    # 右侧字标 "PLAT"（对齐原版 Octop 单词标的用法；全称在标签标题/OA 悬浮窗呈现）
    font = load_font(int(H * 0.46))
    text = "PLAT"
    bbox = font.getbbox(text)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = int(mark_d + 40 * S)
    ty = int((H - th) / 2 - bbox[1])
    ImageDraw.Draw(img).text((tx, ty), text, font=font, fill=text_color)
    return img.resize((753, 289), Image.LANCZOS)


def icon(size: int) -> Image.Image:
    """渐变圆角方底 + 白色双圆标（apple-touch / PWA）。"""
    S = 4
    img = Image.new("RGBA", (size * S, size * S), (0, 0, 0, 0))
    # 135° 渐变：左上蓝 → 右下青
    grad = Image.new("RGBA", (size * S, size * S))
    gd = ImageDraw.Draw(grad)
    for y in range(size * S):
        for x in range(0, size * S, 8):
            t = (x + y) / (2 * size * S)
            col = (
                int(PLAT_BLUE[0] + (PLAT_TEAL[0] - PLAT_BLUE[0]) * t),
                int(PLAT_BLUE[1] + (PLAT_TEAL[1] - PLAT_BLUE[1]) * t),
                int(PLAT_BLUE[2] + (PLAT_TEAL[2] - PLAT_BLUE[2]) * t),
                255,
            )
            gd.rectangle([x, y, x + 8, y + 1], fill=col)
    mask = Image.new("L", (size * S, size * S), 0)
    rad = int(size * S * 0.22)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size * S - 1, size * S - 1], radius=rad, fill=255)
    img.paste(grad, (0, 0), mask)
    # 白色双圆标（PWA maskable 安全区：直径 ≤ 62%）
    draw_mark(
        img,
        cx=size * S / 2,
        cy=size * S / 2,
        r=size * S * 0.26,
        stroke=int(size * S * 0.045),
        big_color=WHITE,
        small_color=WHITE,
        inner_fill=(255, 255, 255, 36),
    )
    return img.resize((size, size), Image.LANCZOS)


SVG_MARK = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vb}" width="{size}" height="{size}">
  <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{big}" stroke-width="{sw}"/>
  <circle cx="{cx}" cy="{cy}" r="{r}" fill="{big}" fill-opacity="0.14"/>
  <circle cx="{scx}" cy="{scy}" r="{sr}" fill="{small}"/>
</svg>
"""

SVG_VERTICAL = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240" width="240" height="240">
  <circle cx="120" cy="96" r="58" fill="none" stroke="{big}" stroke-width="12"/>
  <circle cx="120" cy="96" r="58" fill="{big}" fill-opacity="0.14"/>
  <circle cx="150" cy="126" r="38" fill="{small}"/>
  <text x="120" y="216" text-anchor="middle" font-family="-apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif"
        font-size="34" font-weight="600" letter-spacing="1" fill="{text}">PLAT 助手</text>
</svg>
"""


def main() -> None:
    horizontal_logo(INK).save(OUT / "logo_horizontal_dark.png")
    horizontal_logo(WHITE).save(OUT / "logo_horizontal_white.png")
    icon(180).save(OUT / "apple-touch-icon.png")
    icon(192).save(OUT / "pwa-192.png")
    icon(512).save(OUT / "pwa-512.png")
    (OUT / "favico.svg").write_text(
        SVG_MARK.format(vb=56, size=56, cx=24, cy=24, r=13, sw=4.5, scx=36, scy=36, sr=8.5,
                        big="#0668F9", small="#1FA8A0"),
        encoding="utf-8",
    )
    # splash：dark 文件用于浅色背景（深色字），white 用于深色背景
    (OUT / "logo_vertical_dark.svg").write_text(
        SVG_VERTICAL.format(big="#0668F9", small="#1FA8A0", text="#0B1F3A"), encoding="utf-8"
    )
    (OUT / "logo_vertical_white.svg").write_text(
        SVG_VERTICAL.format(big="#4d8ffb", small="#3fc0b8", text="#FFFFFF"), encoding="utf-8"
    )
    for p in sorted(OUT.iterdir()):
        print(f"生成 {p.name} ({p.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
