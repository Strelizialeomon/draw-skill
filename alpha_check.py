#!/usr/bin/env python3
"""透明通道体检：判 AI 出图的"透明底"是真的还是假的。需要 Pillow（draw.py 依旧零依赖）。

判定（沿用 2026-10-05 出素材时实测的探针）：四角各取 20×20 区域的 alpha 均值，
与底部中间（高 70% 以下、宽 30%~70%）区域的 alpha 均值——
两者都 < 10 → 「真透明」；否则「不是真透明」（没通道 / 白底 / 棋盘格画进图里，都归这档）。

用法：
  python3 alpha_check.py <图片> [<图片>...] [--bg RRGGBB]
  --bg 1b1d3c   # 把图按 alpha 叠到该底色上，生成预览 <原名>_on_1b1d3c.jpg
"""
import argparse
import sys
from pathlib import Path

try:
    from PIL import Image, ImageStat
    HAS_PIL = True
except ImportError:  # 允许 draw.py 在没装 Pillow 的机器上安全 import 本模块
    HAS_PIL = False

CORNER = 20            # 四角探针边长（像素）
ALPHA_THRESHOLD = 10   # alpha 均值低于它算「透」（沿用 gen_image.py 实测值）


def _region_mean(alpha, box):
    """区域 alpha 均值；坐标裁到图范围内，空区域算 0。"""
    x0, y0, x1, y1 = box
    w, h = alpha.size
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return ImageStat.Stat(alpha.crop((x0, y0, x1, y1))).mean[0]


def analyze(path):
    """返回逐行报告（list[str]），末行为结论。"""
    if not HAS_PIL:
        raise RuntimeError("需要 Pillow：pip install Pillow")
    path = Path(path)
    im = Image.open(path)
    w, h = im.size
    alpha = im.convert("RGBA").getchannel("A")
    hist = alpha.histogram()
    total = w * h
    fully = hist[0] / total * 100
    opaque = hist[255] / total * 100
    semi = 100 - fully - opaque
    corners = [
        _region_mean(alpha, (0, 0, CORNER, CORNER)),
        _region_mean(alpha, (w - CORNER, 0, w, CORNER)),
        _region_mean(alpha, (0, h - CORNER, CORNER, h)),
        _region_mean(alpha, (w - CORNER, h - CORNER, w, h)),
    ]
    bottom_mid = _region_mean(alpha, (int(w * 0.3), int(h * 0.7), int(w * 0.7), h))
    verdict = ("真透明" if max(corners) < ALPHA_THRESHOLD and bottom_mid < ALPHA_THRESHOLD
               else "不是真透明（没通道 / 白底 / 棋盘格画进图里，都归这档）")
    return [
        f"{path.name}: {w}x{h} mode={im.mode}",
        f"  全透明 {fully:.1f}% / 半透明 {semi:.1f}% / 不透明 {opaque:.1f}%",
        f"  四角 alpha {[int(c) for c in corners]}，留白区（下中）alpha 均值 {bottom_mid:.0f}",
        f"  结论：{verdict}",
    ]


def preview(path, bg_hex):
    """按 alpha 把图叠到底色上，存 <原名>_on_<rrggbb>.jpg（JPEG 质量 90），返回预览路径。"""
    rgba = Image.open(path).convert("RGBA")
    bg = tuple(int(bg_hex[i:i + 2], 16) for i in (0, 2, 4))
    canvas = Image.new("RGB", rgba.size, bg)
    canvas.paste(rgba, mask=rgba.getchannel("A"))
    out = Path(path).with_name(f"{Path(path).stem}_on_{bg_hex}.jpg")
    canvas.save(out, quality=90)
    return out


def _parse_bg(value):
    v = value.strip().lstrip("#").lower()
    if len(v) != 6 or any(c not in "0123456789abcdef" for c in v):
        raise ValueError(f"--bg 需要 6 位 hex 色值（如 1b1d3c），收到：{value}")
    return v


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="alpha_check",
        description="透明通道体检：判 AI 出图的透明底是真是假。",
    )
    parser.add_argument("images", nargs="+", help="要体检的图片路径")
    parser.add_argument("--bg", default=None,
                        help="叠到 6 位 hex 底色上生成预览图（如 1b1d3c）")
    args = parser.parse_args(argv)

    if not HAS_PIL:
        print("错误: 需要 Pillow：pip install Pillow", file=sys.stderr)
        return 2
    bg = None
    if args.bg:
        try:
            bg = _parse_bg(args.bg)
        except ValueError as e:
            print(f"错误: {e}", file=sys.stderr)
            return 2

    rc = 0
    for img in args.images:
        p = Path(img)
        if not p.is_file():
            print(f"错误: 找不到图片 {img}", file=sys.stderr)
            rc = 2
            continue
        try:
            lines = analyze(p)
        except Exception as e:
            print(f"错误: 打不开 {img}（{e}）", file=sys.stderr)
            rc = 2
            continue
        for line in lines:
            print(line)
        if bg:
            print(f"  预览: {preview(p, bg)}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
