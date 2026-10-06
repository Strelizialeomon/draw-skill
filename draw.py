#!/usr/bin/env python3
"""生图接口封装：流式发起 -> 读进度 -> 下载成图。零第三方依赖。

用法示例：
  python3 draw.py "一只戴墨镜的柴犬"
  python3 draw.py "把这只猫画成油画" --ref ./cat.png --inspect
  python3 draw.py "三盏南瓜灯剪影" --use cutout
  python3 draw.py "只把中间那块改成蓝色" --ref ./p.png --mask ./m.png
  python3 draw.py --list-models

配置（全都有回退，可以一样都不设）：
  key：IMAGE_API_KEY 环境变量 → GRSAI_KEY 环境变量 → ~/.config/grsai/key 文件
  域名：IMAGE_API_BASE 环境变量 → 默认 https://grsai.dakka.com.cn（海外节点 https://grsaiapi.com）
  模型：--model 参数 → IMAGE_MODEL 环境变量 → 默认 gpt-image-2

护栏（都只帮你少犯错，不拦路）：本地图按文件头判格式（png / jpg / webp）；
--aspect 不合模型尺寸规则只警告照发；failure_reason=error 自动重试 1 次（官方说失败退积分）。

出图记录：每张下载的图旁边写一份同名 .json（提示词 / 模型 / 尺寸 / 参数 / 用途 / 任务 id / 时间）。
--use 只写进记录、不改变任何出图行为；--url-only 不写；写失败只警告一行，出图照常。

已知坑（2026-10-05 实测）：grsai 的 gpt-image-2-vip 渠道 background="transparent" 不生效。
透明素材怎么出：先按 SKILL.md「判用途」归类；透明位图只在官方称支持透明的模型上试 + 用 alpha_check.py 体检，
不成退回纯色平底 + 本地抠图。

接口 /v1/draw/completions 是 SSE 流式：连接挂住，逐条推 `data: {事件}`，
每个事件是扁平 JSON（含 status/progress/results），直到 succeeded 或 failed。
"""
import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

DEFAULT_BASE = "https://grsai.dakka.com.cn"
DEFAULT_MODEL = "gpt-image-2"
USE_VALUES = ("full", "cutout", "sheet", "alpha")  # --use 四个用途（只进记录，不影响行为）
KEY_FILE = Path.home() / ".config" / "grsai" / "key"
_STREAM_TIMEOUT = 300  # 流读取默认超时秒

# 官网上传组件口径的软限制（接口文档没写，只警告不拦）
_MAX_REF_BYTES = 10 * 1024 * 1024
_MAX_REFS = 6

# 模型目录：GPT 系快照（2026-10-06 抓自官方模型页 + 官方分辨率文档；价格与上下架状态以官方页为准）
MODELS_SNAPSHOT = "2026-10-06"
MODELS_PAGE = "https://grsai.com/zh/dashboard/models"
KNOWN_MODELS = [
    {"name": "gpt-image-2", "tier": "1K", "quality": "auto",
     "transparent": False, "size_rule": "preset13"},
    {"name": "gpt-image-2-vip", "tier": "1K~4K", "quality": "medium",
     "transparent": True, "size_rule": "pixels"},
    {"name": "gpt-image-2.5", "tier": "1K", "quality": "auto",
     "transparent": False, "size_rule": "preset13"},
    {"name": "gpt-image-2.5-flare", "tier": "1K~4K", "quality": "low/medium/high",
     "transparent": True, "size_rule": "pixels"},
    {"name": "gpt-image-2.5-sunburst", "tier": "1K~4K",
     "quality": "low/medium/high/xhigh/max", "transparent": True, "size_rule": "pixels"},
]

# 官方 1K 预设 13 档（gpt-image-2 / gpt-image-2.5 共用；比例写法或像素值都行）
SIZES_1K = [
    ("1:1", "1024x1024"), ("16:9", "1672x941"), ("9:16", "941x1672"),
    ("4:3", "1443x1090"), ("3:4", "1090x1443"), ("3:2", "1536x1024"),
    ("2:3", "1024x1536"), ("5:4", "1408x1120"), ("4:5", "1120x1408"),
    ("21:9", "1920x832"), ("9:21", "832x1920"), ("2:1", "1792x896"),
    ("1:2", "896x1792"),
]
# 官方 1K~4K 预设 43 档（vip / flare / sunburst 共用；只收像素值，按下表挑最稳）
SIZES_HD = [
    ("1:1", ["1024x1024", "2048x2048", "2880x2880"]),
    ("16:9", ["1280x720", "2048x1152", "3840x2160"]),
    ("9:16", ["720x1280", "1152x2048", "2160x3840"]),
    ("4:3", ["1152x864", "2304x1728", "3264x2448"]),
    ("3:4", ["864x1152", "1728x2304", "2448x3264"]),
    ("3:2", ["1536x1024", "2048x1360", "3504x2336"]),
    ("2:3", ["1024x1536", "1360x2048", "2336x3504"]),
    ("5:4", ["1120x896", "2240x1792", "3200x2560"]),
    ("4:5", ["896x1120", "1792x2240", "2560x3200"]),
    ("21:9", ["1456x624", "2912x1248", "3840x1648"]),
    ("9:21", ["624x1456", "1248x2912", "1648x3840"]),
    ("1:3", ["688x2048", "1280x3840"]),
    ("3:1", ["2048x688", "3840x1280"]),
    ("2:1", ["1536x768", "3072x1536", "3840x1920"]),
    ("1:2", ["768x1536", "1536x3072", "1920x3840"]),
]

# failure_reason 的中文说法（官方枚举：output_moderation / input_moderation / error）
_REASON_MESSAGES = {
    "error": "其它错误",
    "input_moderation": "提示词或参考图被判违规，改写后再试",
    "output_moderation": "生成结果被判违规，换个说法再试",
}


class DrawError(Exception):
    """业务/接口错误（运行期），main() 捕获后退出码 1。"""

    def __init__(self, message, reason=None):
        super().__init__(message)
        self.reason = reason  # failure_reason（error / input_moderation / output_moderation）


class UsageError(Exception):
    """参数/前置配置错误，main() 捕获后退出码 2。"""


def resolve_key():
    """key 回退链：IMAGE_API_KEY → GRSAI_KEY → ~/.config/grsai/key 文件。"""
    key = os.environ.get("IMAGE_API_KEY") or os.environ.get("GRSAI_KEY") or ""
    if not key and KEY_FILE.exists():
        try:
            key = KEY_FILE.read_text().strip()
        except (OSError, UnicodeDecodeError):
            raise UsageError(f"key 文件读不了（权限或编码问题）：{KEY_FILE}")
    if not key:
        raise UsageError(
            "没找到 key：设 IMAGE_API_KEY（或 GRSAI_KEY）环境变量，"
            "或把 key 写入 ~/.config/grsai/key")
    return key


def resolve_base():
    """域名回退链：IMAGE_API_BASE → 默认国内直连节点。"""
    return os.environ.get("IMAGE_API_BASE") or DEFAULT_BASE


def _sniff_image(data):
    """按文件头判图片格式，返回 mime；不认识返回 None。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _png_has_alpha(data):
    """零依赖判 PNG 有没有透明通道：IHDR 颜色类型（第 26 字节）4/6，或存在 tRNS 块。"""
    if len(data) > 25 and data[25] in (4, 6):
        return True
    return b"tRNS" in data


def normalize_refs(refs):
    """--ref 收公网 URL / data URL / 本地图片路径；本地路径读文件、按文件头判格式转 data URL。"""
    out = []
    for ref in refs:
        if ref.startswith(("http://", "https://", "data:")):
            out.append(ref)
            continue
        p = Path(ref)
        if not p.is_file():
            raise UsageError(f"参考图不存在：{ref}")
        try:
            data = p.read_bytes()
        except OSError as e:
            raise UsageError(f"参考图读不了：{ref}（{e.strerror or e}）")
        mime = _sniff_image(data)
        if mime is None:
            raise UsageError(f"参考图只支持 png / jpg / webp，先转换：{ref}")
        if len(data) > _MAX_REF_BYTES:
            print(f"警告: 参考图超过 10MB（官网上传组件口径，仅供参考）：{ref}", file=sys.stderr)
        out.append(f"data:{mime};base64,{base64.b64encode(data).decode()}")
    if len(out) > _MAX_REFS:
        print(f"警告: 参考图共 {len(out)} 张、超过 6 张（官网上传组件口径，仅供参考）",
              file=sys.stderr)
    return out


def normalize_mask(mask, *, has_refs):
    """--mask 收公网 URL / data URL / 本地 PNG；透明区 = 要重绘的区域。"""
    if not mask:
        return None
    if not has_refs:
        raise UsageError("--mask 要配参考图（--ref）一起用")
    if mask.startswith(("http://", "https://", "data:")):
        return mask
    p = Path(mask)
    if not p.is_file():
        raise UsageError(f"遮罩文件不存在：{mask}")
    try:
        data = p.read_bytes()
    except OSError as e:
        raise UsageError(f"遮罩读不了：{mask}（{e.strerror or e}）")
    if _sniff_image(data) != "image/png":
        raise UsageError(f"遮罩只收 PNG（透明区 = 要重绘的区域）：{mask}")
    if not _png_has_alpha(data):
        print(f"警告: 遮罩 {mask} 没有透明区，接口会当「全部保留」原样返回", file=sys.stderr)
    return f"data:image/png;base64,{base64.b64encode(data).decode()}"


def build_body(*, model, prompt, aspect, refs, mask=None, quality=None, background=None):
    """组装请求体；mask / quality / background 只在给了非空值时才带上。"""
    body = {"model": model, "prompt": prompt, "aspectRatio": aspect, "shutProgress": False}
    if refs:
        body["urls"] = refs
    if mask:
        body["mask"] = mask
    if quality:
        body["quality"] = quality
    if background:
        body["background"] = background
    return body


def check_aspect(model, aspect):
    """按模型目录的尺寸规则做软检查；返回警告文案列表（空 = 没问题）。只警告不拦。"""
    aspect = (aspect or "").strip()
    if aspect.lower() == "auto":
        return []
    entry = next((m for m in KNOWN_MODELS if m["name"] == model), None)
    if entry is None:
        return []  # 目录外模型不检查

    if entry["size_rule"] == "preset13":
        ratios = {r for r, _ in SIZES_1K}
        pixels = {p for _, p in SIZES_1K}
        if aspect in ratios or aspect in pixels:
            return []
        return [f"--aspect {aspect} 不在 {model} 的官方 1K 预设里"
                f"（--list-models 看 13 档预设与比例写法），官方可能拒收"]

    # 1K~4K 档模型：只收像素值，自定义尺寸要过官方四条约束
    m = re.fullmatch(r"(\d+)[xX](\d+)", aspect)
    if not m:
        return [f"{model} 只收像素值（如 2048x2048），不支持「{aspect}」这种写法；"
                f"--list-models 看 43 档预设"]
    w, h = int(m.group(1)), int(m.group(2))
    if w == 0 or h == 0:
        return [f"尺寸 {aspect} 不合法（边长不能为 0）"]
    warnings = []
    if max(w, h) > 3840:
        warnings.append(f"尺寸 {aspect} 最长边超过 3840")
    if w % 16 or h % 16:
        warnings.append(f"尺寸 {aspect} 两边都要是 16 的倍数")
    if max(w, h) / min(w, h) > 3:
        warnings.append(f"尺寸 {aspect} 长短边之比超过 3:1")
    total = w * h
    if total < 655_360 or total > 8_294_400:
        warnings.append(f"尺寸 {aspect} 总像素 {total} 超出 655,360 ~ 8,294,400")
    return warnings


def format_models():
    """--list-models 的输出文本（离线渲染内置目录 + 两张预设尺寸表）。"""
    lines = [f"grsai 生图模型目录（快照 {MODELS_SNAPSHOT}；来源：官方模型页 + 官方分辨率文档）", ""]
    for m in KNOWN_MODELS:
        t = "官方称支持透明" if m["transparent"] else "官方未称支持透明"
        rule = "1K 预设 13 档 / 比例 / auto" if m["size_rule"] == "preset13" \
            else "只收像素值（四条约束）"
        lines.append(f"  {m['name']} | 清晰度 {m['tier']} | 质量 {m['quality']} | {t} | {rule}")
    lines += ["", "预设尺寸 · 1K 档（gpt-image-2 / gpt-image-2.5：比例写法或像素值都行）:"]
    lines += [f"  {r}  {p}" for r, p in SIZES_1K]
    lines += ["", "预设尺寸 · 1K~4K 档（vip / flare / sunburst：只收像素值）:"]
    lines += [f"  {r}  " + " / ".join(ps) for r, ps in SIZES_HD]
    lines += [
        "",
        "说明：目录只是参考，不是白名单——--model 可直接传任意模型名（不校验）。",
        f"价格与上下架状态以官方模型页为准：{MODELS_PAGE}",
    ]
    return "\n".join(lines)


def _parse_sse_event(line):
    """把一行 SSE 文本解析成事件 dict；非 data 行或空 payload 返回 None。"""
    line = line.strip()
    if not line.startswith("data:"):
        return None
    payload = line[len("data:"):].strip()
    if not payload:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None  # 非 JSON 帧（心跳等）跳过，别打断整条流


def generate(prompt, *, base, api_key, model, aspect, refs, mask=None, quality=None,
             background=None, open_stream, log=lambda m: None):
    """发起生图并消费事件流，返回 (task_id, [图片URL...])。"""
    body = build_body(model=model, prompt=prompt, aspect=aspect, refs=refs, mask=mask,
                      quality=quality, background=background)
    url = base.rstrip("/") + "/v1/draw/completions"

    last_status = None
    for line in open_stream(url, api_key, body):
        event = _parse_sse_event(line)
        if event is None:
            continue
        last_status = event.get("status")
        if last_status == "succeeded":
            task_id = event.get("id") or "img"
            urls = [r["url"] for r in (event.get("results") or []) if r.get("url")]
            if not urls:
                raise DrawError("生成完成但没有返回图片 URL")
            return task_id, urls
        if last_status == "failed":
            reason = event.get("failure_reason") or ""
            detail = event.get("error") or ""
            msg = f"生成失败: {reason or '未知原因'}"
            if detail:
                msg += f"（{detail}）"
            raise DrawError(msg, reason=reason or None)
        log(f"进度 {event.get('progress', 0)}% ({last_status})")
    raise DrawError(f"连接结束但未生成完成，最后状态 {last_status}")


def download(url, out_dir, task_id, index, *, fetch=None):
    fetch = fetch or _http_get_bytes
    os.makedirs(out_dir, exist_ok=True)
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1] or ".png"
    filename = f"draw-{task_id}-{index}{ext}"
    path = os.path.abspath(os.path.join(out_dir, filename))
    with open(path, "wb") as f:
        f.write(fetch(url))
    return path


def record_path(image_path):
    """记录文件路径 = 图片文件名去扩展名 + .json，与图片同目录。"""
    stem, _ = os.path.splitext(str(image_path))
    return stem + ".json"


def record_ref(ref):
    """记录里怎么存参考图 / 遮罩：URL 原样；本地路径转绝对路径；data URL 不落 base64。"""
    if ref.startswith(("http://", "https://")):
        return ref
    if ref.startswith("data:"):
        return "<data-url>"
    return os.path.abspath(ref)


def write_record(image_path, record):
    """在图片旁写同名 .json 记录（UTF-8、ensure_ascii=False、缩进 2）；写失败由调用方兜底。"""
    path = record_path(image_path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return path


def _http_post_stream(url, api_key, body, *, timeout=_STREAM_TIMEOUT):
    """真实流式 POST：逐行 yield 解码后的文本。"""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        raise DrawError(f"HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise DrawError(f"网络错误: {e.reason}")
    with resp:
        for raw in resp:
            yield raw.decode("utf-8", "replace")


def _http_get_bytes(url):
    try:
        with urllib.request.urlopen(url) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise DrawError(f"下载失败 HTTP {e.code}: {url}")
    except urllib.error.URLError as e:
        raise DrawError(f"下载失败 {e.reason}: {url}")


def _inspect_files(paths):
    """--inspect：对每张成图跑透明体检，报告走 stderr；没装 Pillow 则提示跳过。"""
    try:
        import alpha_check
    except ImportError:
        print("体检跳过：加载不了 alpha_check.py（需要 Pillow：pip install Pillow）",
              file=sys.stderr)
        return
    if not getattr(alpha_check, "HAS_PIL", False):
        print("体检跳过：没装 Pillow（pip install Pillow）", file=sys.stderr)
        return
    for p in paths:
        try:
            lines = alpha_check.analyze(p)
        except Exception as e:
            print(f"体检跳过：{p}（{e}）", file=sys.stderr)
            continue
        for line in lines:
            print(line, file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="draw",
        description="发起生图、读进度并下载结果。",
    )
    parser.add_argument("prompt", nargs="?", help="生图提示词")
    parser.add_argument("--model", default=None,
                        help=f"模型名；不传则用 IMAGE_MODEL 环境变量，仍无则 {DEFAULT_MODEL}；"
                             f"--list-models 看内置目录")
    parser.add_argument("--aspect", default="1024x1024", help="尺寸，默认 1024x1024")
    parser.add_argument("--ref", action="append", default=[], dest="refs",
                        help="参考图：公网 URL 或本地图片（按文件头判格式，收 png/jpg/webp），"
                             "可重复传多张")
    parser.add_argument("--mask", default=None,
                        help="遮罩（局部重绘）：公网 URL 或本地 PNG；透明区 = 要重绘的区域；"
                             "须同时给 --ref")
    parser.add_argument("--quality", default=None,
                        help="质量档（随模型不同，如 medium）；不传则不发该字段")
    parser.add_argument("--background", default=None,
                        help="底色（如 transparent；空串=不发该字段）")
    parser.add_argument("--use", default=None, metavar="full|cutout|sheet|alpha",
                        help="出图用途（只写进同名 .json 记录，不影响请求与任何出图行为）")
    parser.add_argument("--inspect", action="store_true",
                        help="下载后对每张成图跑透明体检（报告走 stderr）")
    parser.add_argument("--list-models", action="store_true",
                        help="打印内置模型目录与预设尺寸表（离线，不触网）")
    parser.add_argument("--out", default=".", help="图片保存目录，默认当前目录")
    parser.add_argument("--url-only", action="store_true",
                        help="只打印图片 URL，不下载（链接 2 小时后失效）")
    parser.add_argument("--timeout", type=float, default=_STREAM_TIMEOUT,
                        help=f"流读取超时秒，默认 {_STREAM_TIMEOUT}")
    args = parser.parse_args(argv)

    if args.use is not None and args.use not in USE_VALUES:
        print(f"错误: --use 只收 {' / '.join(USE_VALUES)}（收到「{args.use}」）", file=sys.stderr)
        return 2

    if args.list_models:
        print(format_models())
        return 0

    if not args.prompt:
        print("错误: 缺少提示词（只想看模型目录用 --list-models）", file=sys.stderr)
        return 2

    try:
        key = resolve_key()
        base = resolve_base()
        refs = normalize_refs(args.refs)
        mask = normalize_mask(args.mask, has_refs=bool(refs))
    except UsageError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2

    model = args.model or os.environ.get("IMAGE_MODEL") or DEFAULT_MODEL
    for warn in check_aspect(model, args.aspect):
        print(f"警告: {warn}", file=sys.stderr)

    def open_stream(url, k, body):
        return _http_post_stream(url, k, body, timeout=args.timeout)

    attempt = 0
    started = time.monotonic()  # 记录里的 seconds 从第一次发起请求起算（含重试等待）
    while True:
        attempt += 1
        try:
            print(f"生成中（模型 {model}）…", file=sys.stderr)
            task_id, urls = generate(args.prompt, base=base, api_key=key, model=model,
                                     aspect=args.aspect, refs=refs, mask=mask,
                                     quality=args.quality, background=args.background,
                                     open_stream=open_stream,
                                     log=lambda m: print(m, file=sys.stderr))
            break
        except DrawError as e:
            reason = getattr(e, "reason", None)
            if reason == "error" and attempt == 1:
                print("其它错误，自动重试 1 次（失败已退积分）", file=sys.stderr)
                continue
            cn = _REASON_MESSAGES.get(reason or "")
            print(f"错误: {cn}（原始错误：{e}）" if cn else f"错误: {e}", file=sys.stderr)
            return 1

    try:
        if args.url_only:
            for u in urls:
                print(u)
            return 0
        paths = []
        for i, u in enumerate(urls, 1):
            paths.append(download(u, args.out, task_id, i, fetch=_http_get_bytes))
            print(paths[-1])
            record = {
                "prompt": args.prompt,
                "model": model,
                "aspect": args.aspect,
                "quality": args.quality or None,
                "background": args.background or None,
                "refs": [record_ref(r) for r in args.refs],
                "mask": record_ref(args.mask) if args.mask else None,
                "use": args.use,
                "base": base,
                "task_id": task_id,
                "index": i,
                "image_url": u,
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "seconds": round(time.monotonic() - started),
            }
            try:
                write_record(paths[-1], record)
            except OSError as e:
                print(f"警告: 出图记录写不了（不影响出图）：{record_path(paths[-1])}"
                      f"（{e.strerror or e}）", file=sys.stderr)
        if args.inspect:
            _inspect_files(paths)
        return 0
    except DrawError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
