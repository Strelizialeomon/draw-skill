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
