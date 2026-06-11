---
name: draw
description: 调用异步生图接口生成图片。当用户说"画/生成/帮我画一张/生图/文生图/draw image/生成图片"等需求时使用。负责提交任务、轮询等待、把成图下载到本地并返回路径。
---

# draw —— 调接口生成图片

用户想生成图片时，运行本技能目录下的 `draw.py`。它会提交生图任务、轮询直到完成、把图下载到本地，并在 stdout 打印图片的本地绝对路径。

## 前置：环境变量（必须已配置）

- `IMAGE_API_KEY`：接口的 Bearer token
- `IMAGE_API_BASE`：接口域名，如 `https://api.example.com`（不带路径）
- `IMAGE_MODEL`（可选）：默认模型名；不设则用 `gpt-image-2`

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

- 默认把图下载到当前目录，文件名 `draw-<任务id>-<序号><扩展名>`（扩展名取自图片 URL），stdout 打印绝对路径。
- `--model` 选模型：不传则用环境变量 `IMAGE_MODEL`，仍无则 `gpt-image-2`。用户用大白话点名某模型时，把它当 `--model` 传进去。
- `--ref` 传公网图片 URL 做参考图（垫图），可重复多张；本地图片暂不支持。
- `--url-only` 只打印图片 URL、不下载。
- `--timeout` 流读取超时秒，默认 300。

## 流程要点

接口是流式的：脚本发起一个请求后会挂住，边等边收进度，生成完直接拿到图。生成通常约 1 分钟。

1. 跑脚本，传用户的提示词（必要时把中文需求整理成清晰的提示词）。
2. 等待期间脚本把 `进度 xx%` 打印在 stderr；这是正常的，不是卡死。
3. 成功后从 stdout 读到本地路径，用 Read 工具把图片展示给用户。
4. 失败（退出码非 0）时把 stderr 的错误原因转达给用户。
