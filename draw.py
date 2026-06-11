#!/usr/bin/env python3
"""生图接口封装：流式发起 -> 读进度 -> 下载成图。零第三方依赖。

接口 /v1/draw/completions 是 SSE 流式：连接挂住，逐条推 `data: {事件}`，
每个事件是扁平 JSON（含 status/progress/results），直到 succeeded 或 failed。
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

_STREAM_TIMEOUT = 300  # 流读取默认超时秒


class DrawError(Exception):
    """业务/接口错误，main() 捕获后转为非零退出码。"""


def _parse_sse_event(line):
    """把一行 SSE 文本解析成事件 dict；非 data 行或空 payload 返回 None。"""
    line = line.strip()
    if not line.startswith("data:"):
        return None
    payload = line[len("data:"):].strip()
    if not payload:
        return None
    return json.loads(payload)


def generate(prompt, *, base, api_key, model, aspect, refs, open_stream, log=lambda m: None):
    """发起生图并消费事件流，返回 (task_id, [图片URL...])。"""
    body = {"model": model, "prompt": prompt, "aspectRatio": aspect, "shutProgress": False}
    if refs:
        body["urls"] = refs
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


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="draw",
        description="发起生图、读进度并下载结果。",
    )
    parser.add_argument("prompt", help="生图提示词")
    parser.add_argument("--model", default=None,
                        help="模型名；不传则用 IMAGE_MODEL 环境变量，仍无则 gpt-image-2")
    parser.add_argument("--aspect", default="1024x1024", help="尺寸，默认 1024x1024")
    parser.add_argument("--ref", action="append", default=[], dest="refs",
                        help="参考图公网 URL，可重复传多张")
    parser.add_argument("--out", default=".", help="图片保存目录，默认当前目录")
    parser.add_argument("--url-only", action="store_true", help="只打印图片 URL，不下载")
    parser.add_argument("--timeout", type=float, default=_STREAM_TIMEOUT,
                        help=f"流读取超时秒，默认 {_STREAM_TIMEOUT}")
    args = parser.parse_args(argv)

    api_key = os.environ.get("IMAGE_API_KEY")
    base = os.environ.get("IMAGE_API_BASE")
    missing = [n for n, v in (("IMAGE_API_KEY", api_key), ("IMAGE_API_BASE", base)) if not v]
    if missing:
        print(f"错误: 缺少环境变量 {', '.join(missing)}，请先 export 后再运行。", file=sys.stderr)
        return 2

    model = args.model or os.environ.get("IMAGE_MODEL") or "gpt-image-2"

    def open_stream(url, key, body):
        return _http_post_stream(url, key, body, timeout=args.timeout)

    try:
        print(f"生成中（模型 {model}）…", file=sys.stderr)
        task_id, urls = generate(args.prompt, base=base, api_key=api_key, model=model,
                                 aspect=args.aspect, refs=args.refs,
                                 open_stream=open_stream,
                                 log=lambda m: print(m, file=sys.stderr))
        if args.url_only:
            for u in urls:
                print(u)
            return 0
        for i, u in enumerate(urls, 1):
            path = download(u, args.out, task_id, i, fetch=_http_get_bytes)
            print(path)
        return 0
    except DrawError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
