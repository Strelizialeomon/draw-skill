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

```bash
export IMAGE_API_KEY="sk-xxx"
export IMAGE_API_BASE="https://api.example.com"
```

## 怎么调用

```bash
python3 <技能目录>/draw.py "<提示词>" [--model gpt-image-2] [--aspect 1024x1024] \
  [--ref <公网图URL>]... [--out <目录>] [--url-only]
```

- 默认把图下载到当前目录，文件名 `draw-<任务id>-<序号>.png`，stdout 打印绝对路径。
- `--ref` 传公网图片 URL 做参考图（垫图），可重复多张；本地图片暂不支持。
- `--url-only` 只打印图片 URL、不下载。
- `--interval` / `--timeout` 调整轮询间隔（默认 3 秒）与超时（默认 300 秒）。

## 流程要点

1. 跑脚本，传用户的提示词（必要时把中文需求整理成清晰的提示词）。
2. 脚本阻塞轮询（默认每 3 秒、最多 5 分钟），进度打印在 stderr。
3. 成功后从 stdout 读到本地路径，用 Read 工具把图片展示给用户。
4. 失败（退出码非 0）时把 stderr 的错误原因转达给用户。
