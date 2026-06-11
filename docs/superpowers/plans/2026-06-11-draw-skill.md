# draw-skill 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把异步生图接口（提交→轮询→取图）封装成零依赖 Python 脚本 `draw.py`，再配 `SKILL.md` 让它能在 Claude Code 里以 `/draw` 调用。

**Architecture:** 单文件 Python 脚本，只用标准库。核心拆成纯逻辑函数 `submit` / `poll` / `download`，HTTP 收口到 `_http_post` / `_http_get_bytes`；纯逻辑函数把 HTTP 作为参数注入（`post=` / `fetch=`），便于用桩做单元测试、不打真网络。`main()` 读环境变量、解析参数、串起流程。

**Tech Stack:** Python 3（stdlib：`urllib`、`json`、`argparse`、`time`、`os`、`sys`、`unittest`）。无第三方依赖。

约定：所有命令在项目根 `/Users/sunchongsheng/code/draw-skill` 下运行。测试命令统一：

```
python3 -m unittest discover -s tests -t . -v
```

公共函数签名（全程保持一致）：

- `class DrawError(Exception)`
- `submit(prompt, *, base, api_key, model, aspect, refs, post) -> str`
- `poll(task_id, *, base, api_key, interval, timeout, post, sleep, now, log) -> list[str]`
- `download(url, out_dir, task_id, index, *, fetch) -> str`
- `_http_post(url, api_key, body) -> dict`
- `_http_get_bytes(url) -> bytes`
- `main(argv=None) -> int`

---

## File Structure

- Create: `draw.py` — 主脚本（所有函数 + CLI 入口）
- Create: `tests/__init__.py` — 让 tests 成为包
- Create: `tests/test_draw.py` — 单元测试（unittest）
- Create: `SKILL.md` — 技能清单
- Create: `README.md` — 使用说明
- 软链：`~/.claude/skills/draw` → `~/code/draw-skill`

---

## Task 1: 脚手架 + `submit()`

**Files:**
- Create: `draw.py`
- Create: `tests/__init__.py`
- Create: `tests/test_draw.py`

- [ ] **Step 1: 写失败测试**

`tests/__init__.py` 写空文件（占位即可）。

`tests/test_draw.py`：

```python
import unittest
import draw


class TestSubmit(unittest.TestCase):
    def test_returns_id_and_builds_text_body(self):
        captured = {}

        def fake_post(url, api_key, body):
            captured["url"] = url
            captured["api_key"] = api_key
            captured["body"] = body
            return {"code": 0, "msg": "success", "data": {"id": "task-123"}}

        task_id = draw.submit(
            "a cat",
            base="https://api.example.com",
            api_key="sk-xxx",
            model="gpt-image-2",
            aspect="1024x1024",
            refs=[],
            post=fake_post,
        )

        self.assertEqual(task_id, "task-123")
        self.assertEqual(captured["url"], "https://api.example.com/v1/draw/completions")
        self.assertEqual(captured["api_key"], "sk-xxx")
        self.assertEqual(
            captured["body"],
            {"model": "gpt-image-2", "prompt": "a cat", "aspectRatio": "1024x1024"},
        )
        self.assertNotIn("urls", captured["body"])

    def test_includes_refs_when_present(self):
        captured = {}

        def fake_post(url, api_key, body):
            captured["body"] = body
            return {"code": 0, "data": {"id": "x"}}

        draw.submit(
            "a cat",
            base="https://api.example.com/",
            api_key="sk",
            model="m",
            aspect="1024x1024",
            refs=["https://img/1.png"],
            post=fake_post,
        )
        self.assertEqual(captured["body"]["urls"], ["https://img/1.png"])

    def test_raises_on_nonzero_code(self):
        def fake_post(url, api_key, body):
            return {"code": 1, "msg": "bad key", "data": {}}

        with self.assertRaises(draw.DrawError):
            draw.submit("x", base="b", api_key="k", model="m", aspect="a", refs=[], post=fake_post)

    def test_raises_when_no_id(self):
        def fake_post(url, api_key, body):
            return {"code": 0, "data": {}}

        with self.assertRaises(draw.DrawError):
            draw.submit("x", base="b", api_key="k", model="m", aspect="a", refs=[], post=fake_post)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: FAIL（`AttributeError: module 'draw' has no attribute 'submit'` 或 `ModuleNotFoundError: No module named 'draw'`）

- [ ] **Step 3: 写最小实现**

`draw.py`：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: PASS（4 个 test 全绿）

- [ ] **Step 5: 提交**

```bash
git add draw.py tests/__init__.py tests/test_draw.py
git commit -m "feat: submit() 提交生图任务并解析 task id"
```

---

## Task 2: `poll()` — 成功与进行中

**Files:**
- Modify: `draw.py`
- Test: `tests/test_draw.py`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_draw.py`，在 `if __name__` 之前）

```python
class TestPollSuccess(unittest.TestCase):
    def test_returns_urls_on_succeeded(self):
        responses = iter([
            {"code": 0, "data": {"status": "running", "progress": 30, "results": []}},
            {"code": 0, "data": {"status": "succeeded", "progress": 100,
                                 "results": [{"url": "https://img/a.png"},
                                             {"url": "https://img/b.png"}]}},
        ])
        calls = {"n": 0}

        def fake_post(url, api_key, body):
            self.assertEqual(url, "https://api.example.com/v1/draw/result")
            self.assertEqual(body, {"id": "task-123"})
            calls["n"] += 1
            return next(responses)

        urls = draw.poll(
            "task-123",
            base="https://api.example.com",
            api_key="sk",
            interval=3,
            timeout=300,
            post=fake_post,
            sleep=lambda s: None,
            now=lambda: 0.0,
            log=lambda m: None,
        )
        self.assertEqual(urls, ["https://img/a.png", "https://img/b.png"])
        self.assertEqual(calls["n"], 2)

    def test_raises_on_nonzero_code(self):
        def fake_post(url, api_key, body):
            return {"code": 1, "msg": "boom"}

        with self.assertRaises(draw.DrawError):
            draw.poll("x", base="b", api_key="k", interval=0, timeout=10,
                      post=fake_post, sleep=lambda s: None, now=lambda: 0.0, log=lambda m: None)

    def test_raises_when_succeeded_but_no_urls(self):
        def fake_post(url, api_key, body):
            return {"code": 0, "data": {"status": "succeeded", "results": []}}

        with self.assertRaises(draw.DrawError):
            draw.poll("x", base="b", api_key="k", interval=0, timeout=10,
                      post=fake_post, sleep=lambda s: None, now=lambda: 0.0, log=lambda m: None)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: FAIL（`module 'draw' has no attribute 'poll'`）

- [ ] **Step 3: 写最小实现**（追加到 `draw.py`，`submit` 之后）

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: PASS（7 个 test 全绿）

- [ ] **Step 5: 提交**

```bash
git add draw.py tests/test_draw.py
git commit -m "feat: poll() 轮询结果，成功取 URL、code!=0 与空结果报错"
```

---

## Task 3: `poll()` — 失败与超时分支

**Files:**
- Test: `tests/test_draw.py`（`poll` 实现已在 Task 2 覆盖这两个分支，本任务只补测试锁定行为）

- [ ] **Step 1: 写测试**（追加到 `tests/test_draw.py`，`if __name__` 之前）

```python
class TestPollFailAndTimeout(unittest.TestCase):
    def test_raises_on_failed_status_with_reason(self):
        def fake_post(url, api_key, body):
            return {"code": 0, "data": {"status": "failed",
                                        "failure_reason": "nsfw", "error": ""}}

        with self.assertRaises(draw.DrawError) as ctx:
            draw.poll("x", base="b", api_key="k", interval=0, timeout=10,
                      post=fake_post, sleep=lambda s: None, now=lambda: 0.0, log=lambda m: None)
        self.assertIn("nsfw", str(ctx.exception))

    def test_raises_on_timeout(self):
        clock = {"t": 0.0}

        def fake_now():
            return clock["t"]

        def fake_sleep(s):
            clock["t"] += 5.0  # 每次 sleep 推进 5 秒，迅速越过 deadline

        def fake_post(url, api_key, body):
            return {"code": 0, "data": {"status": "running", "progress": 10}}

        with self.assertRaises(draw.DrawError) as ctx:
            draw.poll("x", base="b", api_key="k", interval=3, timeout=10,
                      post=fake_post, sleep=fake_sleep, now=fake_now, log=lambda m: None)
        self.assertIn("仍未完成", str(ctx.exception))
```

- [ ] **Step 2: 跑测试确认通过**（实现已存在，这两个测试应直接绿）

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: PASS（9 个 test 全绿）。若 timeout 测试不绿，检查 `poll` 中 `now() >= deadline` 的判断位置（应在取到非终态、log 之后、sleep 之前）。

- [ ] **Step 3: 提交**

```bash
git add tests/test_draw.py
git commit -m "test: 锁定 poll() 的 failed 与 timeout 分支"
```

---

## Task 4: `download()`

**Files:**
- Modify: `draw.py`
- Test: `tests/test_draw.py`

- [ ] **Step 1: 写失败测试**（追加，`if __name__` 之前）

```python
import os
import tempfile


class TestDownload(unittest.TestCase):
    def test_writes_file_and_returns_abspath(self):
        with tempfile.TemporaryDirectory() as d:
            def fake_fetch(url):
                self.assertEqual(url, "https://img/a.png")
                return b"PNGDATA"

            path = draw.download("https://img/a.png", d, "task-123", 1, fetch=fake_fetch)

            self.assertTrue(os.path.isabs(path))
            self.assertEqual(os.path.basename(path), "draw-task-123-1.png")
            with open(path, "rb") as f:
                self.assertEqual(f.read(), b"PNGDATA")

    def test_creates_missing_out_dir(self):
        with tempfile.TemporaryDirectory() as d:
            nested = os.path.join(d, "imgs")
            path = draw.download("https://img/a.png", nested, "t", 2, fetch=lambda u: b"x")
            self.assertTrue(os.path.exists(path))
            self.assertEqual(os.path.basename(path), "draw-t-2.png")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: FAIL（`module 'draw' has no attribute 'download'`）

- [ ] **Step 3: 写最小实现**（追加到 `draw.py`，`poll` 之后）

```python
def download(url, out_dir, task_id, index, *, fetch=None):
    fetch = fetch or _http_get_bytes
    os.makedirs(out_dir, exist_ok=True)
    filename = f"draw-{task_id}-{index}.png"
    path = os.path.abspath(os.path.join(out_dir, filename))
    with open(path, "wb") as f:
        f.write(fetch(url))
    return path
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: PASS（11 个 test 全绿）

- [ ] **Step 5: 提交**

```bash
git add draw.py tests/test_draw.py
git commit -m "feat: download() 把图片 URL 下载到本地并返回绝对路径"
```

---

## Task 5: HTTP 层 `_http_post` / `_http_get_bytes`

**Files:**
- Modify: `draw.py`
- Test: `tests/test_draw.py`

- [ ] **Step 1: 写失败测试**（追加，`if __name__` 之前）。用 monkeypatch 替换 `urllib.request.urlopen`，验证请求头/请求体与错误映射。

```python
import io
import urllib.error


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestHttpPost(unittest.TestCase):
    def test_sends_json_with_bearer_and_parses_response(self):
        captured = {}

        def fake_urlopen(req):
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            captured["auth"] = req.get_header("Authorization")
            captured["ctype"] = req.get_header("Content-type")
            captured["data"] = req.data
            return _FakeResp(b'{"code":0,"data":{"id":"abc"}}')

        orig = draw.urllib.request.urlopen
        draw.urllib.request.urlopen = fake_urlopen
        try:
            out = draw._http_post("https://api.example.com/v1/draw/completions",
                                  "sk-key", {"prompt": "x"})
        finally:
            draw.urllib.request.urlopen = orig

        self.assertEqual(out, {"code": 0, "data": {"id": "abc"}})
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["auth"], "Bearer sk-key")
        self.assertEqual(captured["ctype"], "application/json")
        self.assertEqual(json.loads(captured["data"].decode()), {"prompt": "x"})

    def test_maps_httperror_to_drawerror(self):
        def fake_urlopen(req):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {},
                                         io.BytesIO(b"no access"))

        orig = draw.urllib.request.urlopen
        draw.urllib.request.urlopen = fake_urlopen
        try:
            with self.assertRaises(draw.DrawError) as ctx:
                draw._http_post("https://api.example.com/x", "k", {})
        finally:
            draw.urllib.request.urlopen = orig
        self.assertIn("401", str(ctx.exception))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: FAIL（`module 'draw' has no attribute '_http_post'`）

- [ ] **Step 3: 写最小实现**（追加到 `draw.py`，`download` 之后）

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: PASS（13 个 test 全绿）

- [ ] **Step 5: 提交**

```bash
git add draw.py tests/test_draw.py
git commit -m "feat: HTTP 层 _http_post/_http_get_bytes，含 Bearer 与错误映射"
```

---

## Task 6: `main()` — 环境变量校验 + CLI 串联

**Files:**
- Modify: `draw.py`
- Test: `tests/test_draw.py`

- [ ] **Step 1: 写失败测试**（追加，`if __name__` 之前）。测缺环境变量返回 2，以及整体编排（通过 monkeypatch 替换 `submit`/`poll`/`download` 验证 stdout 打印路径、返回 0）。

```python
import contextlib


class TestMain(unittest.TestCase):
    def test_missing_env_returns_2(self):
        orig = dict(os.environ)
        os.environ.pop("IMAGE_API_KEY", None)
        os.environ.pop("IMAGE_API_BASE", None)
        try:
            rc = draw.main(["a cat"])
        finally:
            os.environ.clear()
            os.environ.update(orig)
        self.assertEqual(rc, 2)

    def test_happy_path_prints_paths_and_returns_0(self):
        orig = dict(os.environ)
        os.environ["IMAGE_API_KEY"] = "sk"
        os.environ["IMAGE_API_BASE"] = "https://api.example.com"
        orig_submit, orig_poll, orig_download = draw.submit, draw.poll, draw.download
        draw.submit = lambda prompt, **kw: "task-9"
        draw.poll = lambda task_id, **kw: ["https://img/a.png"]
        draw.download = lambda url, out, tid, idx, **kw: f"/abs/draw-{tid}-{idx}.png"
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = draw.main(["a cat", "--out", "."])
        finally:
            draw.submit, draw.poll, draw.download = orig_submit, orig_poll, orig_download
            os.environ.clear()
            os.environ.update(orig)
        self.assertEqual(rc, 0)
        self.assertIn("/abs/draw-task-9-1.png", buf.getvalue())

    def test_url_only_prints_urls(self):
        orig = dict(os.environ)
        os.environ["IMAGE_API_KEY"] = "sk"
        os.environ["IMAGE_API_BASE"] = "https://api.example.com"
        orig_submit, orig_poll = draw.submit, draw.poll
        draw.submit = lambda prompt, **kw: "t"
        draw.poll = lambda task_id, **kw: ["https://img/x.png"]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = draw.main(["a cat", "--url-only"])
        finally:
            draw.submit, draw.poll = orig_submit, orig_poll
            os.environ.clear()
            os.environ.update(orig)
        self.assertEqual(rc, 0)
        self.assertIn("https://img/x.png", buf.getvalue())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: FAIL（`module 'draw' has no attribute 'main'`）

- [ ] **Step 3: 写最小实现**（追加到 `draw.py` 末尾）

```python
def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="draw",
        description="提交生图任务、轮询等待并下载结果。",
    )
    parser.add_argument("prompt", help="生图提示词")
    parser.add_argument("--model", default="gpt-image-2", help="模型名，默认 gpt-image-2")
    parser.add_argument("--aspect", default="1024x1024", help="尺寸，默认 1024x1024")
    parser.add_argument("--ref", action="append", default=[], dest="refs",
                        help="参考图公网 URL，可重复传多张")
    parser.add_argument("--out", default=".", help="图片保存目录，默认当前目录")
    parser.add_argument("--url-only", action="store_true", help="只打印图片 URL，不下载")
    parser.add_argument("--interval", type=float, default=3.0, help="轮询间隔秒，默认 3")
    parser.add_argument("--timeout", type=float, default=300.0, help="超时秒，默认 300")
    args = parser.parse_args(argv)

    api_key = os.environ.get("IMAGE_API_KEY")
    base = os.environ.get("IMAGE_API_BASE")
    missing = [n for n, v in (("IMAGE_API_KEY", api_key), ("IMAGE_API_BASE", base)) if not v]
    if missing:
        print(f"错误: 缺少环境变量 {', '.join(missing)}，请先 export 后再运行。", file=sys.stderr)
        return 2

    try:
        task_id = submit(args.prompt, base=base, api_key=api_key, model=args.model,
                         aspect=args.aspect, refs=args.refs, post=_http_post)
        print(f"已提交，任务 id={task_id}，开始轮询…", file=sys.stderr)
        urls = poll(task_id, base=base, api_key=api_key, interval=args.interval,
                    timeout=args.timeout, post=_http_post,
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: PASS（16 个 test 全绿）

- [ ] **Step 5: 提交**

```bash
git add draw.py tests/test_draw.py
git commit -m "feat: main() 校验环境变量并串起 submit/poll/download"
```

---

## Task 7: SKILL.md + README.md + 软链安装

**Files:**
- Create: `SKILL.md`
- Create: `README.md`

- [ ] **Step 1: 写 `SKILL.md`**

```markdown
---
name: draw
description: 调用异步生图接口生成图片。当用户说"画/生成/帮我画一张/生图/文生图/draw image/生成图片"等需求时使用。负责提交任务、轮询等待、把成图下载到本地并返回路径。
---

# draw —— 调接口生成图片

用户想生成图片时，运行本技能目录下的 `draw.py`。它会提交生图任务、轮询直到完成、把图下载到本地，并在 stdout 打印图片的本地绝对路径。

## 前置：环境变量（必须已配置）

- `IMAGE_API_KEY`：接口的 Bearer token
- `IMAGE_API_BASE`：接口域名，如 `https://api.example.com`（不带路径）

若未配置，脚本会报错退出码 2。这时提醒用户先 export，例如：

\`\`\`bash
export IMAGE_API_KEY="sk-xxx"
export IMAGE_API_BASE="https://api.example.com"
\`\`\`

## 怎么调用

\`\`\`bash
python3 <技能目录>/draw.py "<提示词>" [--model gpt-image-2] [--aspect 1024x1024] \
  [--ref <公网图URL>]... [--out <目录>] [--url-only]
\`\`\`

- 默认把图下载到当前目录，文件名 `draw-<任务id>-<序号>.png`，stdout 打印绝对路径。
- `--ref` 传公网图片 URL 做参考图（垫图），可重复多张；本地图片暂不支持。
- `--url-only` 只打印图片 URL、不下载。

## 流程要点

1. 跑脚本，传用户的提示词（必要时把中文需求整理成清晰的提示词）。
2. 脚本阻塞轮询（默认每 3 秒、最多 5 分钟），进度打印在 stderr。
3. 成功后从 stdout 读到本地路径，用 Read 工具把图片展示给用户。
4. 失败（退出码非 0）时把 stderr 的错误原因转达给用户。
```

> 注意：上面 SKILL.md 内容里的三处 ` ``` ` 代码块在真实文件中是普通三反引号；本计划为了嵌套展示用了转义，落地时写成正常代码块即可。

- [ ] **Step 2: 写 `README.md`**

```markdown
# draw-skill

把异步生图接口（提交 → 轮询 → 取图）封装成零依赖 Python 脚本，并作为 Claude Code 技能使用。

## 安装

\`\`\`bash
ln -s ~/code/draw-skill ~/.claude/skills/draw
export IMAGE_API_KEY="sk-xxx"
export IMAGE_API_BASE="https://api.example.com"
\`\`\`

## 直接用脚本

\`\`\`bash
python3 draw.py "一只戴墨镜的柴犬" --out ./out
\`\`\`

## 测试

\`\`\`bash
python3 -m unittest discover -s tests -t . -v
\`\`\`

详见 `docs/superpowers/specs/2026-06-11-draw-skill-design.md`。
```

- [ ] **Step 3: 建软链并验证**

```bash
ln -sfn /Users/sunchongsheng/code/draw-skill /Users/sunchongsheng/.claude/skills/draw
ls -l /Users/sunchongsheng/.claude/skills/draw
```

Expected: 软链指向 `/Users/sunchongsheng/code/draw-skill`

- [ ] **Step 4: 校验脚本能跑（不打真网络）**

```bash
python3 draw.py "test" ; echo "exit=$?"
```

Expected: 因未配置环境变量（或已配置则真实提交）打印中文报错、`exit=2`（缺环境变量场景）。确认脚本本身可执行、参数解析正常。

- [ ] **Step 5: 提交**

```bash
git add SKILL.md README.md
git commit -m "feat: SKILL.md 技能清单 + README + 软链安装说明"
```

---

## Task 8: 真机冒烟测试（需要真实凭据，手动）

**Files:** 无（手动验证步骤，不写入仓库）

- [ ] **Step 1: 配置真实环境变量**

```bash
export IMAGE_API_KEY="<真实 key>"
export IMAGE_API_BASE="<真实域名>"
```

- [ ] **Step 2: 跑一次真实生图**

```bash
python3 draw.py "一只在沙发上睡觉的橘猫，写实风格" --out ./smoke
```

Expected: stderr 打印 `已提交…` 和进度；stdout 打印形如 `/Users/.../smoke/draw-<id>-1.png` 的绝对路径；该文件存在且能打开。

- [ ] **Step 3: 验证 `--url-only` 与 `--ref`（可选）**

```bash
python3 draw.py "把这张图变成水彩风" --ref "https://example.com/a.png" --url-only
```

Expected: stdout 打印图片 URL，返回码 0。

- [ ] **Step 4: 记录结果**

把真机表现（耗时、是否多图、失败信息样式）回填到 README 或 spec 的「已知行为」备注。不提交真实 key。

---

## Self-Review（计划对照 spec 自查）

- **接口三步**：submit（Task 1）/ poll（Task 2-3）/ download（Task 4）+ HTTP 层（Task 5）全覆盖。✅
- **环境变量校验**：Task 6 覆盖缺失返回 2。✅
- **错误处理表**：缺 env(2)、HTTP 错误→DrawError、code!=0、failed、timeout、下载失败，分散在 Task 1/2/3/5/6 的测试中锁定。✅
- **默认参数**（model/aspect/3 秒/300 秒/当前目录）：Task 6 的 argparse 默认值。✅
- **--url-only / --ref / --out**：Task 6。✅
- **不硬编码 key**：全程从环境变量读，无写死。✅
- **技能落地 + 软链 + 触发词**：Task 7。✅
- **冒烟测试**：Task 8。✅
- **占位符扫描**：无 TBD/TODO；每个代码步骤含完整代码。✅
- **签名一致性**：submit/poll/download/_http_post/_http_get_bytes/main 在各 Task 中签名一致。✅
