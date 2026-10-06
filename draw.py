#!/usr/bin/env python3
"""生图接口封装：流式发起 -> 读进度 -> 下载成图。零第三方依赖。

用法示例：
  python3 draw.py "一只戴墨镜的柴犬"
  python3 draw.py "改成油画风" --ref ./cat.png --inspect
  python3 draw.py --list-models

配置（全都有回退，可以一样都不设）：
  key：IMAGE_API_KEY 环境变量 → GRSAI_KEY 环境变量 → ~/.config/grsai/key 文件
  域名：IMAGE_API_BASE 环境变量 → 默认 https://grsai.dakka.com.cn（海外节点 https://grsaiapi.com）
  模型：--model 参数 → IMAGE_MODEL 环境变量 → 默认 gpt-image-2

已知坑（2026-10-05 实测）：grsai 的 gpt-image-2-vip 渠道 background="transparent" 不生效
（纯文字出图带它连续 3 次失败；带参考图时被忽略、返回白底）。透明底素材做法：
出不透明图 → 用 alpha_check.py 体检 → 后处理。

接口 /v1/draw/completions 是 SSE 流式：连接挂住，逐条推 `data: {事件}`，
每个事件是扁平 JSON（含 status/progress/results），直到 succeeded 或 failed。
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_BASE = "https://grsai.dakka.com.cn"
DEFAULT_MODEL = "gpt-image-2"
KEY_FILE = Path.home() / ".config" / "grsai" / "key"
_STREAM_TIMEOUT = 300  # 流读取默认超时秒

# 模型目录：GPT 系快照（2026-10-06 抓自官方模型页；价格与在架状态以官方页为准）
MODELS_SNAPSHOT = "2026-10-06"
MODELS_PAGE = "https://grsai.com/zh/dashboard/models"
KNOWN_MODELS = [
    {"name": "gpt-image-2", "status": "可用", "size": "1K", "quality": "auto",
     "price": "600 积分（≈¥0.03）"},
    {"name": "gpt-image-2-vip", "status": "可用", "size": "1K/2K/4K", "quality": "medium",
     "price": "2000 积分（≈¥0.1）"},
    {"name": "gpt-image-2.5", "status": "可用", "size": "1K", "quality": "auto",
     "price": "600 积分（≈¥0.03）"},
    {"name": "gpt-image-2.5-flare", "status": "维护中", "size": "1K/2K/4K",
     "quality": "low/medium/high", "price": "2000 积分（≈¥0.1）"},
    {"name": "gpt-image-2.5-sunburst", "status": "维护中", "size": "1K/2K/4K",
     "quality": "low/medium/high/xhigh/max", "price": "2400 积分（≈¥0.12）"},
]


class DrawError(Exception):
    """业务/接口错误（运行期），main() 捕获后退出码 1。"""


class UsageError(Exception):
    """参数/前置配置错误，main() 捕获后退出码 2。"""


def resolve_key():
    """key 回退链：IMAGE_API_KEY → GRSAI_KEY → ~/.config/grsai/key 文件。"""
    key = os.environ.get("IMAGE_API_KEY") or os.environ.get("GRSAI_KEY") or ""
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text().strip()
    if not key:
        raise UsageError(
            "没找到 key：设 IMAGE_API_KEY（或 GRSAI_KEY）环境变量，"
            "或把 key 写入 ~/.config/grsai/key")
    return key


def resolve_base():
    """域名回退链：IMAGE_API_BASE → 默认国内直连节点。"""
    return os.environ.get("IMAGE_API_BASE") or DEFAULT_BASE


def normalize_refs(refs):
    """--ref 收公网 URL / data URL / 本地图片路径；本地路径读文件转 base64 data URL。"""
    mime_by_ext = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                   ".webp": "image/webp", ".gif": "image/gif"}
    out = []
    for ref in refs:
        if ref.startswith(("http://", "https://", "data:")):
            out.append(ref)
            continue
        p = Path(ref)
        if not p.is_file():
            raise UsageError(f"参考图不存在：{ref}")
        mime = mime_by_ext.get(p.suffix.lower(), "image/png")
        b64 = base64.b64encode(p.read_bytes()).decode()
        out.append(f"data:{mime};base64,{b64}")
    return out


def build_body(*, model, prompt, aspect, refs, quality=None, background=None):
    """组装请求体；quality / background 只在给了非空值时才带上。"""
    body = {"model": model, "prompt": prompt, "aspectRatio": aspect, "shutProgress": False}
    if refs:
        body["urls"] = refs
    if quality:
        body["quality"] = quality
    if background:
        body["background"] = background
    return body


def format_models():
    """--list-models 的输出文本（离线渲染内置目录）。"""
    lines = [f"grsai 生图模型目录（快照 {MODELS_SNAPSHOT}，来源：官方模型页）", ""]
    for m in KNOWN_MODELS:
        lines.append(f"  {m['name']} | {m['status']} | {m['size']} | "
                     f"质量 {m['quality']} | {m['price']}")
    lines += [
        "",
        "说明：目录只是参考，不是白名单——--model 可直接传任意模型名（不校验）。",
        f"价格与在架状态以官方页为准：{MODELS_PAGE}",
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
    return json.loads(payload)


def generate(prompt, *, base, api_key, model, aspect, refs, quality=None, background=None,
             open_stream, log=lambda m: None):
    """发起生图并消费事件流，返回 (task_id, [图片URL...])。"""
    body = build_body(model=model, prompt=prompt, aspect=aspect, refs=refs,
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
            reason = event.get("failure_reason") or event.get("error") or "未知原因"
            raise DrawError(f"生成失败: {reason}")
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
                        help="参考图：公网 URL 或本地图片路径（自动转 base64），可重复传多张")
    parser.add_argument("--quality", default=None,
                        help="质量档（随模型不同，如 medium）；不传则不发该字段")
    parser.add_argument("--background", default=None,
                        help="底色（如 transparent；空串=不发该字段）")
    parser.add_argument("--inspect", action="store_true",
                        help="下载后对每张成图跑透明体检（报告走 stderr）")
    parser.add_argument("--list-models", action="store_true",
                        help="打印内置模型目录（离线，不触网）")
    parser.add_argument("--out", default=".", help="图片保存目录，默认当前目录")
    parser.add_argument("--url-only", action="store_true", help="只打印图片 URL，不下载")
    parser.add_argument("--timeout", type=float, default=_STREAM_TIMEOUT,
                        help=f"流读取超时秒，默认 {_STREAM_TIMEOUT}")
    args = parser.parse_args(argv)

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
    except UsageError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2

    model = args.model or os.environ.get("IMAGE_MODEL") or DEFAULT_MODEL

    def open_stream(url, k, body):
        return _http_post_stream(url, k, body, timeout=args.timeout)

    try:
        print(f"生成中（模型 {model}）…", file=sys.stderr)
        task_id, urls = generate(args.prompt, base=base, api_key=key, model=model,
                                 aspect=args.aspect, refs=refs,
                                 quality=args.quality, background=args.background,
                                 open_stream=open_stream,
                                 log=lambda m: print(m, file=sys.stderr))
        if args.url_only:
            for u in urls:
                print(u)
            return 0
        paths = []
        for i, u in enumerate(urls, 1):
            paths.append(download(u, args.out, task_id, i, fetch=_http_get_bytes))
            print(paths[-1])
        if args.inspect:
            _inspect_files(paths)
        return 0
    except DrawError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
