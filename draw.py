#!/usr/bin/env python3
"""异步生图接口封装：提交 -> 轮询 -> 下载。零第三方依赖。"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


class DrawError(Exception):
    """业务/接口错误，main() 捕获后转为非零退出码。"""


def submit(prompt, *, base, api_key, model, aspect, refs, post):
    body = {"model": model, "prompt": prompt, "aspectRatio": aspect}
    if refs:
        body["urls"] = refs
    url = base.rstrip("/") + "/v1/draw/completions"
    resp = post(url, api_key, body)
    if resp.get("code") != 0:
        raise DrawError(f"提交失败: {resp.get('msg')}")
    task_id = resp.get("data", {}).get("id")
    if not task_id:
        raise DrawError("提交成功但返回里没有 data.id")
    return task_id


def poll(task_id, *, base, api_key, interval, timeout, post,
         sleep=time.sleep, now=time.monotonic, log=lambda m: None):
    url = base.rstrip("/") + "/v1/draw/result"
    deadline = now() + timeout
    while True:
        resp = post(url, api_key, {"id": task_id})
        if resp.get("code") != 0:
            raise DrawError(f"查询失败: {resp.get('msg')}")
        data = resp.get("data", {})
        status = data.get("status")
        if status == "succeeded":
            urls = [r["url"] for r in data.get("results", []) if r.get("url")]
            if not urls:
                raise DrawError("任务已完成但没有返回图片 URL")
            return urls
        if status == "failed":
            reason = data.get("failure_reason") or data.get("error") or "未知原因"
            raise DrawError(f"生成失败: {reason}")
        log(f"进度 {data.get('progress', 0)}% ({status})")
        if now() >= deadline:
            raise DrawError(f"已等待 {timeout} 秒仍未完成，最后进度 {data.get('progress', 0)}%")
        sleep(interval)


def download(url, out_dir, task_id, index, *, fetch=None):
    fetch = fetch or _http_get_bytes
    os.makedirs(out_dir, exist_ok=True)
    filename = f"draw-{task_id}-{index}.png"
    path = os.path.abspath(os.path.join(out_dir, filename))
    with open(path, "wb") as f:
        f.write(fetch(url))
    return path


def _http_post(url, api_key, body):
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
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        raise DrawError(f"HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise DrawError(f"网络错误: {e.reason}")


def _http_get_bytes(url):
    try:
        with urllib.request.urlopen(url) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise DrawError(f"下载失败 HTTP {e.code}: {url}")
    except urllib.error.URLError as e:
        raise DrawError(f"下载失败 {e.reason}: {url}")
