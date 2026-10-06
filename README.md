# draw-skill

把生图接口（grsai / GPT Image）封装成零依赖 Python 脚本 + Claude Code 技能：本地垫图、流式出图、透明体检、模型目录。

## 安装

```bash
ln -sfn ~/code/draw-skill ~/.claude/skills/draw
```

key 三种给法（按顺序找，配一个就行）：

1. `IMAGE_API_KEY` 环境变量
2. `GRSAI_KEY` 环境变量
3. `~/.config/grsai/key` 文件（内容就是 key，一行）

域名默认 `https://grsai.dakka.com.cn`（海外节点 `https://grsaiapi.com`，用 `IMAGE_API_BASE` 覆盖）。
也可以照 `.env.example` 自己 export。

## 直接用脚本

```bash
python3 draw.py "一只戴墨镜的柴犬" --out ./out
python3 draw.py "改成油画风" --ref ./cat.png --inspect
python3 draw.py --list-models
python3 alpha_check.py ./out/draw-xxx-1.png --bg 1b1d3c
```

- `--ref <公网URL 或 本地路径>`：参考图/垫图，可重复多张（本地图自动转 base64 data URL）
- `--model`：优先级 `--model` > `IMAGE_MODEL` > `gpt-image-2`；**不限于内置目录**，任何模型名直接传
- `--quality` / `--background`：透传给接口；不传就不发这两个字段
- `--inspect`：出图后跑透明体检（报告走 stderr）
- `--aspect`（默认 `1024x1024`）、`--out`、`--url-only`、`--timeout`：同旧版
- `--list-models`：离线打印 GPT 系模型目录（快照 2026-10-06，价格与在架状态以官方模型页为准）

> 接口是流式的：发起请求后挂住等待，进度打到 stderr，生成完（约 1 分钟）直接拿到图。

## 透明体检（alpha_check.py）

需要 Pillow（**只有这个工具需要**，`draw.py` 依旧零依赖）。判「真透明 / 不是真透明（没通道 / 白底 / 棋盘格画进图里）」；`--bg 1b1d3c` 把图叠到指定底色上生成预览 jpg。

## 已知坑

- `background: transparent` 在 grsai `gpt-image-2-vip` 渠道不生效（2026-10-05 实测）：纯文字出图带它连续 3 次失败；带参考图时被忽略、返回白底。透明底素材请出不透明图后用 `alpha_check.py` 体检。

## 测试

```bash
python3 -m unittest discover -s tests -t . -v
```

设计与实现细节见 `docs/superpowers/specs/2026-10-06-draw-skill-grsai-upgrade.md`（升级设计）与 `docs/superpowers/specs/2026-06-11-draw-skill-design.md`（初版）。
