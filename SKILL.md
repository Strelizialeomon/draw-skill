---
name: draw
description: 调用生图接口（grsai / GPT Image，流式）生成图片。当用户说"画/生成/帮我画一张/生图/文生图/draw image/生成图片"等需求时使用。负责发起、等待、把成图下载到本地并返回路径；支持本地图垫图、透明体检、模型目录。
---

# draw —— 调接口生成图片

用户想生成图片时，运行本技能目录下的 `draw.py`。它会流式发起生图（进度打在 stderr）、把成图下载到本地，并在 stdout 打印图片的本地绝对路径。

## 前置（基本不用配）

- key（按顺序找）：`IMAGE_API_KEY` 环境变量 → `GRSAI_KEY` 环境变量 → `~/.config/grsai/key` 文件
- 域名：默认 `https://grsai.dakka.com.cn`（可用 `IMAGE_API_BASE` 覆盖；海外节点 `https://grsaiapi.com`）
- 模型：默认 `gpt-image-2`（可用 `IMAGE_MODEL` 覆盖，或 `--model` 单次指定）

## 怎么调用

```bash
python3 <技能目录>/draw.py "<提示词>" [--model gpt-image-2] [--aspect 1024x1024] \
  [--ref <公网URL 或 本地图片路径>]... [--quality <档>] [--background <值>] \
  [--inspect] [--out <目录>] [--url-only]

python3 <技能目录>/draw.py --list-models              # 离线看内置模型目录
python3 <技能目录>/alpha_check.py <图片> [--bg 1b1d3c] # 透明体检（另见下节）
```

- `--ref`：参考图（垫图），公网 URL 和本地图片路径都行（本地图自动转 base64），可重复多张。
- `--quality` / `--background`：原样透传给接口；不传就不发这两个字段。
- `--inspect`：下载后对每张成图跑透明体检，报告打 stderr（stdout 仍只有路径）。
- `--list-models`：打印内置模型目录（离线快照，不联网）。
- `--url-only`：只打印图片 URL、不下载（与 `--inspect` 同给时只打 URL）。
- `--out` 默认当前目录，文件名 `draw-<任务id>-<序号><扩展名>`。

## 模型怎么选

1. 用户点名了模型 → 原样传 `--model`（**不限于内置目录、不校验**，如 `gpt-image-2-vip`、`nano-banana-pro`）。
2. 没点名 → 默认 `gpt-image-2`；要高清多分辨率（1K/2K/4K）可推荐 `gpt-image-2-vip`。
3. 不确定有哪些 → 跑 `--list-models` 看快照；完整在架列表以官方模型页为准：https://grsai.com/zh/dashboard/models

## 透明体检（alpha_check.py）

需要 Pillow（只有这个工具需要，`draw.py` 依旧零依赖）。判「真透明 / 不是真透明（没通道 / 白底 / 棋盘格画进图里）」，`--bg 1b1d3c` 还可把图叠到指定底色上出预览 jpg。

## 坑（实测）

- `background: transparent` 在 grsai `gpt-image-2-vip` 渠道**不生效**（2026-10-05 实测：纯文字出图带它连续 3 次失败；带参考图时被忽略、返回白底）。透明底素材做法：出不透明图 → `alpha_check.py` 体检 → 后处理。
- 接口是流式的：发起后连接挂住、边等边收进度（约 1 分钟），stderr 的「进度 xx%」是正常现象，不是卡死。

## 流程要点

1. 跑脚本，传用户的提示词（必要时把中文需求整理成清晰的提示词）。
2. 成功后从 stdout 读到本地路径，用 Read 工具把图片展示给用户。
3. 失败时把 stderr 的错误原因转达给用户。退出码：2 = 参数 / 配置问题（缺 key、参考图不存在等），1 = 运行期错误（网络、生成失败等）。
