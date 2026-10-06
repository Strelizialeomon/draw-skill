#!/usr/bin/env python3
"""透明通道体检：判 AI 出图的"透明底"是真的还是假的，并说清假在哪。需要 Pillow（draw.py 依旧零依赖）。

判法（2026-10-06 修订二；合成图 8 类实测过）：看**边框一圈**里「全透明」像素的占比——
≥ 25% 判「真透明」；1%~25% 判「存疑」；< 1%（或压根没有透明通道）判「不是真透明」，并给出原因与底色判断。
（旧版「四角 + 下半留白区」探针只适合主体偏上的素材，居中、贴底的真透明图会被误判，已换掉。）

用法：
  python3 alpha_check.py <图片> [<图片>...] [--bg RRGGBB]
  --bg 1b1d3c   # 把图按 alpha 叠到该底色上，生成预览 <原名>_on_1b1d3c.jpg
"""
import argparse
import sys
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageDraw, ImageStat
    HAS_PIL = True
except ImportError:  # 允许 draw.py 在没装 Pillow 的机器上安全 import 本模块
    HAS_PIL = False

ALPHA_TRANSPARENT_MAX = 5      # alpha ≤ 5 算「全透明」
ALPHA_OPAQUE_MIN = 250         # alpha ≥ 250 算「不透明」（上游透明图的实心区只到 252~254）
TRUE_TRANSPARENT_RATIO = 25.0  # 边框全透明占比 ≥ 25% 判真透明
SUSPECT_RATIO = 1.0            # 1%~25% 判存疑；< 1% 判不是
QUANTIZE_STEP = 16             # 底色统计前的量化步长（每通道 //16*16）
LIGHT_CHANNEL_MIN = 176        # 「浅色」：各通道 ≥ 176
LIGHT_SPREAD_MAX = 16          # 「浅灰」：通道间差 ≤ 16
CHECKER_SECOND_MIN = 20.0      # 棋盘格：第二色占比 ≥ 20%
CHECKER_SUM_MIN = 80.0         # 棋盘格：两色合计 ≥ 80%
LIGHT_DOMINANT_MIN = 80.0      # 浅色底：最多那色 ≥ 80%


def _border_width(w, h):
    return max(4, round(min(w, h) * 0.02))


def _border_mask(w, h):
    """边框一圈的掩膜（L 模式，边框内 255）。"""
    border = Image.new("L", (w, h), 0)
    ImageDraw.Draw(border).rectangle((0, 0, w - 1, h - 1), outline=255, width=_border_width(w, h))
    return border


def _lut(pred):
    """按条件预生成 256 项查找表（走 PIL 的 C 实现，别用逐像素 lambda）。"""
    return [255 if pred(v) else 0 for v in range(256)]


def _border_transparent_ratio(alpha, w, h):
    """边框里「全透明」像素的占比（%）。"""
    full_tr = alpha.point(_lut(lambda a: a <= ALPHA_TRANSPARENT_MAX))
    border = _border_mask(w, h)
    border_px = ImageStat.Stat(border).sum[0]
    hit_px = ImageStat.Stat(ImageChops.multiply(border, full_tr)).sum[0]
    return (hit_px / border_px * 100) if border_px else 0.0


def _is_light(c):
    return min(c) >= LIGHT_CHANNEL_MIN and (max(c) - min(c)) <= LIGHT_SPREAD_MAX


def _border_base(im, alpha, w, h):
    """只看边框里的不透明像素，判底色：疑似棋盘格 / 白或浅色底 / 其它底 / None（判不出）。"""
    border = _border_mask(w, h)
    opaque = alpha.point(_lut(lambda a: a >= ALPHA_OPAQUE_MIN))
    mask = ImageChops.multiply(border, opaque)
    total = ImageStat.Stat(mask).sum[0] / 255
    if total == 0:
        return None
    lut = [v // QUANTIZE_STEP * QUANTIZE_STEP for v in range(256)]
    quant = Image.merge("RGB", [band.point(lut) for band in im.convert("RGB").split()])
    canvas = Image.new("RGB", (w, h), (1, 1, 1))  # 哨兵色（量化后不会出现 1）
    canvas.paste(quant, mask=mask)
    counts = canvas.getcolors(maxcolors=1 << 24) or []
    top = sorted(((n, c) for n, c in counts if c != (1, 1, 1)), reverse=True)
    if not top:
        return None
    top_n, top_c = top[0]
    second_n, second_c = top[1] if len(top) > 1 else (0, (0, 0, 0))
    p_top = top_n / total * 100
    p_second = second_n / total * 100
    if (top_c != second_c and _is_light(top_c) and _is_light(second_c)
            and p_second >= CHECKER_SECOND_MIN and p_top + p_second >= CHECKER_SUM_MIN):
        return "疑似棋盘格画进图里"
    if _is_light(top_c) and p_top >= LIGHT_DOMINANT_MIN:
        return "白或浅色底"
    return "其它底"


def analyze(path):
    """返回逐行报告（list[str]），末行为结论。"""
    if not HAS_PIL:
        raise RuntimeError("需要 Pillow：pip install Pillow")
    path = Path(path)
    im = Image.open(path)
    w, h = im.size
    has_channel = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
    alpha = im.convert("RGBA").getchannel("A")
    hist = alpha.histogram()
    total = w * h
    fully = sum(hist[:ALPHA_TRANSPARENT_MAX + 1]) / total * 100
    opaque = sum(hist[ALPHA_OPAQUE_MIN:]) / total * 100
    semi = 100.0 - fully - opaque
    ratio = _border_transparent_ratio(alpha, w, h)
    lines = [
        f"{path.name}: {w}x{h} mode={im.mode}",
        f"  全透明 {fully:.1f}% / 半透明 {semi:.1f}% / 不透明 {opaque:.1f}%",
        f"  边框全透明占比 {ratio:.1f}%（带宽 {_border_width(w, h)}px）",
    ]
    if has_channel and ratio >= TRUE_TRANSPARENT_RATIO:
        verdict = "真透明"
    elif has_channel and ratio >= SUSPECT_RATIO:
        verdict = "存疑（主体贴边，或背景没抠干净，用 --bg 预览确认）"
    else:
        reason = "没有透明通道" if not has_channel else "有通道但边框不透明"
        base = _border_base(im, alpha, w, h)
        verdict = f"不是真透明（{reason}" + (f"；底色：{base}" if base else "") + "）"
    lines.append(f"  结论：{verdict}")
    return lines


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
