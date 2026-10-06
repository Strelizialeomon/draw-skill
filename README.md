# draw-skill

把生图接口（grsai / GPT Image，SSE 流式）封装成零依赖 Python 脚本 + Claude Code 技能：本地垫图、遮罩局部重绘、流式出图、透明体检、模型目录与尺寸检查。

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
python3 draw.py "只把中间那块改成蓝色" --ref ./p.png --mask ./m.png
python3 draw.py --list-models
python3 alpha_check.py ./out/draw-xxx-1.png --bg 1b1d3c
```

- `--ref <公网URL 或 本地图片>`：参考图/垫图，可重复多张；本地图按**文件头**判格式（只收 png / jpg / webp），单个超 10MB、总数超 6 张只警告
- `--mask <公网URL 或 本地PNG>`：遮罩（局部重绘），必须配 `--ref`；透明区 = 要重绘的区域，尺寸与第一张参考图一致；白底黑形遮罩无效
- `--model`：优先级 `--model` > `IMAGE_MODEL` > `gpt-image-2`；**不限于内置目录**，任何模型名直接传
- `--aspect`：默认 `1024x1024`；不合模型尺寸规则时 stderr 警告、照发（1K 档模型用 13 档预设/比例，1K~4K 档只收像素值）
- `--use`：出图用途（`full` / `cutout` / `sheet` / `alpha`，见 SKILL.md「① 判用途」）；只写进同名 `.json` 记录，不改变任何出图行为
- 出图后每张图旁边会写一份同名 `.json` 记录（提示词 / 模型 / 尺寸 / 参数 / 用途 / 任务 id / 时间）；写失败只警告，不影响出图
- `--quality` / `--background`：透传给接口；不传就不发这两个字段
- `--inspect`：出图后跑透明体检（报告走 stderr）
- `--out`、`--url-only`（链接 2 小时后失效）、`--timeout`：同旧版
- `--list-models`：离线打印 GPT 系模型目录 + 两张预设尺寸表（快照 2026-10-06；价格与上下架状态以官方模型页为准）

> 接口是流式的：发起请求后挂住等待，进度打到 stderr，生成完（约 1 分钟）直接拿到图。
> 失败会返还积分；`failure_reason=error`（其它错误）会自动重试 1 次，违规类给中文原因、不重试。

## 透明体检（alpha_check.py）

需要 Pillow（**只有这个工具需要**，`draw.py` 依旧零依赖）。
看**边框一圈**里全透明像素占比：≥25% 判「真透明」、1%~25% 判「存疑」、<1%（或没通道）判「不是真透明」并说清原因（没通道 / 边框不透明 / 疑似棋盘格 / 白或浅色底 / 其它底）；`--bg 1b1d3c` 生成底色预览 jpg。

## 已知坑

- `background: transparent` 在本渠道不稳：官方只列 vip / flare / sunburst 支持，vip 实测不生效（2026-10-05：纯文字出图带它连续 3 次失败；带参考图时被忽略、返回白底），另两个在维护。透明素材怎么出不默认一条路——先按 SKILL.md「① 判用途」归类（透明位图 / 抠形状）再定路线；纯色平底出图 + 本地抠图仍是多数情况下的落地路线。

## 测试

```bash
python3 -m unittest discover -s tests -t . -v
```

设计与实现细节见 `docs/superpowers/specs/2026-10-06-draw-skill-grsai-upgrade.md`（升级设计，含修订二）与 `docs/superpowers/specs/2026-06-11-draw-skill-design.md`（初版）。
