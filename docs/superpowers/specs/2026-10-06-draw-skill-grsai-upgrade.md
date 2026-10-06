# draw-skill 升级设计：grsai 开箱可用 + 本地垫图 + 透明体检

- 日期：2026-10-06
- 状态：草案（待过闸）
- 作者：用户 + Claude（spec-flow 方案期）
- 修订：2026-10-06 追加「模型目录」机制（用户新需求：模型不写死；实测无模型列表 API，改为预制目录）
- 取代点：本文取代 2026-06-11 设计文档的两条：①「不做：本地图片垫图」——接口实际接受 base64 data URL（见「接口契约补充」）；②「配置：`IMAGE_API_KEY` / `IMAGE_API_BASE` 必填」——改为环境变量 → key 文件 → 内置默认域名的回退链。旧文其余部分（SSE 流式契约、帧格式、错误码框架、文件名规则）继续有效；旧文正文不修改，作为当时快照。

## 背景与现状（2026-10-06 核实）

- draw 技能（`~/code/draw-skill`）当前只认环境变量；而 shell 配置与 Claude 设置里都没有这些变量，代码也不读仓库内的 `.env`。**结论：现在装好即跑会退出码 2，必须先手动 export 才可用。**
- 2026-10-05 在 jasmine-lottery 出素材时写的 `assets/papercut-garden/scripts/gen_image.py` 沉淀了三条经验：
  1. 接口接受**本地图转 base64 data URL** 当参考图（该脚本以本地文件路径为入参，实战出图走通过）；
  2. key 可以放 `~/.config/grsai/key` 文件（该文件现已存在）；
  3. AI 出图的「透明底」经常是假的（白底 / 棋盘格画进图里 / 干脆没通道），出图后需要**透明通道体检**才能下结论。
- SKILL.md 有两处旧描述：frontmatter 描述与正文首段写成「轮询等待」，实际实现是 SSE 流式。
- grsai **没有模型列表 API**（2026-10-06 实测）：境内、境外节点的 `/v1/models`、`/v1/draw/models` 等候选端点全部 404；官方文档只定义 `/v1/draw/completions` 与 `/v1/draw/result` 两个接口。官方模型页 `grsai.com/zh/dashboard/models` 免登录、服务端直出——本次已从其页面数据抓出在架**图片模型 15 个**（GPT 系 5 个 + nano-banana 系 10 个）。

## 目标

让 draw 技能「装好即用、垫图不受公网限制、出图后能验透明、模型选型不写死」：

1. 不配任何环境变量也能跑（key 走文件、域名走默认）。
2. `--ref` 直接收本地图片路径（自动转 data URL）。
3. 新增透明体检工具；`--inspect` 可在出图后顺手跑。
4. `--quality` / `--background` 透传。
5. 顺手修 SKILL.md 的「轮询」旧描述。
6. 模型不写死：内置「预制模型目录」（GPT 系 5 个），`--list-models` 随时可查；`--model` 保持自由传。

## 机制清单

| 机制 | 解决什么 | 没有它会怎样 |
|---|---|---|
| 本地垫图直传：`--ref` 收本地路径，自动转 base64 `data:` URL | 手里有垫图、但没有公网 URL 也能垫图 | 垫图只能先传到图床换 URL |
| 配置回退链：key 依次找 `IMAGE_API_KEY` → `GRSAI_KEY` → `~/.config/grsai/key` 文件；域名缺省 `https://grsai.dakka.com.cn` | 新机器 / 新会话开箱即用 | 维持现状：每次手 export，忘了就退出码 2 |
| 透明体检：新增 `alpha_check.py` 工具；`draw.py --inspect` 生成后顺手跑 | 判定成图是不是真透明（假透明肉眼难辨） | 拿 PS 逐个开来看 |
| 参数透传：`--quality` / `--background` | 调清晰度、指定底色不用改脚本 | 要用这些参数就得改代码 |
| 模型目录：内置预制模型清单（GPT 系 5 个，快照 2026-10-06）+ `--list-models` 打印 | 模型选型可见、不写死；新模型仍可直接 `--model` 传 | 模型名只能靠记忆或临时翻官方网页 |

## 接口契约补充（2026-10-05 实测，叠在 2026-06-11 契约之上）

- `urls[]` 元素除公网 URL 外，**接受 base64 data URL**（`data:image/png;base64,...`）。gen_image.py 即按此实现，并以本地文件当参考图出图走通。
- `background: "transparent"` 在 grsai `gpt-image-2-vip` 渠道**不生效**：纯文字出图带它连续 3 次失败（不扣费）；带参考图时被忽略、返回白底 RGB。→ 技能只做透传、不承诺效果；文档必须标注此坑。
- `quality`（如 `medium`）实测可用；**支持值随模型不同**，官方文档对照（2026-10-05 抓取）：`gpt-image-2`: auto；`gpt-image-2.5`: auto；`gpt-image-2-vip`: medium；`gpt-image-2.5-flare`: low、medium、high；`gpt-image-2.5-sunburst`: low、medium、high、xhigh、max。
- **没有模型列表接口**（见「背景」，境内外节点均 404）；在架模型以官方模型页为准，本次抓取快照见「模型目录」。
- SSE 流式契约（`shutProgress:false` + `data:` 帧）不变，继续沿用；不引入 `/v1/draw/result` 轮询。

## 设计

### draw.py（改造，保持零第三方依赖）

调用形态（★ = 本次新增）：

```bash
python3 draw.py "<提示词>" \
  [--model gpt-image-2] [--aspect 1024x1024] \
  [--ref <公网URL 或 本地图片路径>]...   # ★ 本地路径自动转 data URL
  [--quality <档位>]                    # ★ 透传，不传则不发该字段
  [--background <值>]                   # ★ 透传，空串 = 不发该字段
  [--list-models]                       # ★ 打印内置模型目录（离线，不触网）
  [--inspect]                           # ★ 下载后对每张成图跑体检（报告走 stderr）
  [--out <目录>] [--url-only] [--timeout 300]
```

抽出可单测的纯函数（现有 `generate` / `download` / SSE 解析不动）：

- `normalize_refs(refs) -> list[str]`：`http://` / `https://` / `data:` 开头原样透传；其余当本地文件读字节 → 按扩展名定 mime → `data:<mime>;base64,...`。文件不存在 → `DrawError`（退出码 2）。
- `resolve_key() -> str`：`IMAGE_API_KEY` 环境变量 → `GRSAI_KEY` 环境变量 → `~/.config/grsai/key` 文件（取 strip）；全无 → 报错退出码 2，文案写清三个来源。
- `resolve_base() -> str`：`IMAGE_API_BASE` 环境变量 → 缺省 `https://grsai.dakka.com.cn`。
- `build_body(...) -> dict`：把现在内联的请求体组装抽成函数；`quality` / `background` 仅在给了值且非空串时带上。

`--inspect`：下载完成后对每张图调用体检函数，报告打 **stderr**（stdout 只保留「本地路径」契约）；Pillow 未装时 stderr 提示跳过体检、主流程照常、退出码 0；`--url-only` 时无本地文件、不体检。体检函数从 `alpha_check.py` 惰性 import——只有 `--inspect` 才需要 Pillow，主路径仍零依赖。

### 模型目录（draw.py 内置常量）

内置常量 `KNOWN_MODELS`：GPT 系 5 个（用户偏好），随代码快照，字段 = 名称 / 状态 / 清晰度 / 质量档 / 价格：

| 模型 | 状态 | 清晰度 | 质量档 | 价格（快照 2026-10-06） |
|---|---|---|---|---|
| `gpt-image-2` | 可用 | 1K | auto | 600 积分（≈¥0.03） |
| `gpt-image-2-vip` | 可用 | 1K / 2K / 4K | medium | 2,000 积分（≈¥0.1） |
| `gpt-image-2.5` | 可用 | 1K | auto | 600 积分（≈¥0.03） |
| `gpt-image-2.5-flare` | 维护中 | 1K / 2K / 4K | low / medium / high | 2,000 积分（≈¥0.1） |
| `gpt-image-2.5-sunburst` | 维护中 | 1K / 2K / 4K | low / medium / high / xhigh / max | 2,400 积分（≈¥0.12） |

- `--list-models`：打印该目录 + 快照日期 + 官方模型页 URL + 「以官方页为准」；走 stdout、不触网。
- `--model` **不做白名单校验**：目录是参考不是闸门，新模型 / 其它系列（nano-banana 等）直接传即可。
- 数据来源：官方模型页快照（2026-10-06 抓取）+ 官方文档 quality 对照（2026-10-05 抓取）。

### alpha_check.py（新增）

```bash
python3 alpha_check.py <图片> [<图片>...] [--bg RRGGBB]
```

- 判定逻辑沿用 gen_image.py 实测版：读成 RGBA 后，
  - 四角各取 20×20 像素区域算 alpha 均值，取四角最大者；
  - 底部中间区域（高度 70% 以下、宽度 30%~70%）算 alpha 均值；
  - 两者都 < 10 → 判「真透明」；否则 → 「不是真透明」（没通道 / 白底 / 棋盘格画进图里 都归这档）。
- 报告输出（stdout）：文件名、尺寸、mode、全透明 / 半透明 / 不透明占比、四角 alpha、留白区均值、结论。
- `--bg RRGGBB`：把图按 alpha 叠到指定底色上，生成 `<原名>_on_<rrggbb>.jpg`（JPEG 质量 90），打印预览路径；不给 `--bg` 不生成预览。
- 依赖：Pillow（仅此工具需要，`draw.py` 不依赖）。未装时给「pip install Pillow」提示，退出码 2。
- 暴露可 import 的 `analyze(path) -> list[str]`（逐行报告文本、末行为结论），`draw.py --inspect` 复用同一函数。

### SKILL.md / README

- 修「轮询」两处旧描述 → 流式。
- 补：key / base 回退链与默认域名、`--ref` 支持本地路径、`--quality` / `--background`、`--inspect` 与 `alpha_check.py` 用法、`background=transparent` 渠道坑（2026-10-05 实测）。
- 补模型目录用法：`--list-models`、默认 gpt-image-2、用户点名模型直接 `--model` 传（不限于目录）、完整在架列表看官方模型页。
- README 的「设计与实现细节见…」指向本文 + 旧文。

## 错误处理（增补；其余沿用 2026-06-11 表）

| 情况 | 行为 |
|---|---|
| `--ref` 指向的文件不存在 | 报错（写清路径），退出码 2 |
| key 三个来源皆无 | 报错（写清三个来源），退出码 2 |
| `--bg` 色值非法 | 报错，退出码 2 |
| `--inspect` 但 Pillow 未装 | stderr 提示跳过，主流程照常，退出码 0 |
| `alpha_check.py` 单独跑但 Pillow 未装 | 报错提示安装，退出码 2 |

## 测试

- `normalize_refs`：URL 透传 / 本地文件转 data URL（含 mime 按扩展名判定）/ 文件不存在报错。
- `resolve_key`：四态（`IMAGE_API_KEY` 优先、`GRSAI_KEY`、key 文件、全无报错）——用临时 HOME 隔离文件态。
- `resolve_base`：环境变量优先 / 缺省默认域名。
- `build_body`：`quality`、`background` 的「传 / 不传 / 空串」三态。
- `main`：本地 `--ref` 端到端（桩掉 `generate`）；`--inspect` 报告走 stderr、stdout 仍只有路径。
- `alpha_check` 判定：用 Pillow 合成三类小图（真透明 RGBA / 不透明白底 / 半透明），断言结论；`--bg` 生成预览文件。
- `--list-models`：打印 5 个 GPT 系模型（含维护中标注、快照日期、官方页 URL）、退出 0、**不触网**（桩掉 `urlopen` 断言未被调用）。
- 真机冒烟：只用 `~/.config/grsai/key`（不设环境变量）实跑一次「本地垫图 + `--inspect`」。

## 不做（YAGNI）

- webHook / `/v1/draw/result` 轮询（流式已够，维持旧裁决）。
- 并发批量、`--n` 多张（维持旧裁决）。
- 把公网 URL 参考图先下载再转 data URL（接口直接收 URL，没必要）。
- 自动判断该不该体检（`--inspect` 显式开关，不做魔法）。
- 全局安装 Pillow、或把 Pillow 并成 `draw.py` 的依赖。
- 自动抓取官方模型页做「实时模型列表」：页面无接口、结构易变、抓取脆；改为「快照 + 官方页指引」，需要时人工更新常量。

## 验收标准

- [ ] 不设任何环境变量、仅 `~/.config/grsai/key` 在位：`python3 draw.py "一只猫"` 出图成功，stdout 为本地路径。
- [ ] `--ref 本地图.png` 垫图：请求体 `urls` 为 `data:image/png;base64,...`；`--ref https://…` 仍原样透传。
- [ ] `--quality medium` 时 body 多出 `quality`；`--background ""` 时不带 `background`；`--background transparent` 时原样带上。
- [ ] `--inspect`：stderr 打体检结论、stdout 只多出路径；Pillow 缺失时提示跳过、退出码 0。
- [ ] `python3 alpha_check.py`：真透明图判「真透明」、白底图判「不是真透明」；`--bg 1b1d3c` 生成预览 jpg。
- [ ] 单测全绿；`draw.py` 的 import 仍全为标准库（零第三方依赖）。
- [ ] `--list-models` 打印 GPT 系 5 个模型（含维护中标注、价格快照、官方页 URL），且不触网。
- [ ] SKILL.md 无「轮询」字样残留，五项新能力都有说明。

## 自定细节

1. ref 判定规则：`http://` / `https://` / `data:` 开头 = 直接透传；其余一律当本地路径。
2. mime 推断：按扩展名 `.png` / `.jpg` / `.jpeg` / `.webp` / `.gif`；认不出 → `image/png`。
3. 新增 `GRSAI_KEY` 为第二 key 来源：兼容 gen_image.py 的既有用法。
4. 默认域名取国内直连 `https://grsai.dakka.com.cn`；海外节点 `https://grsaiapi.com` 只写进文档、不做默认。
5. 默认模型维持 `gpt-image-2` 不变（同价的 `gpt-image-2.5`、高配 `gpt-image-2-vip` 都由 `--model` 传，不改成默认）。
6. `--background` 传空串 = 不发该字段（对齐 gen_image.py 的 `--background ""` 用法）。
7. `--inspect` 的报告与进度类输出全部走 stderr；stdout 继续只保留「本地路径」这一个契约。
8. 体检阈值沿用 gen_image.py 实测值：四角最大均值 < 10 且留白区均值 < 10 判真透；探针为 20×20 角块与「高 70% 以下、宽 30%~70%」区域。
9. 体检工具文件名 `alpha_check.py`——不叫 `inspect.py`，避免与 Python 标准库 `inspect` 撞名（`tests/` 与 unittest 依赖标准库 inspect）。
10. `--url-only` 与 `--inspect` 同给时：只打 URL、跳体检（没有本地文件可检）。
11. 预览图命名 `<原名>_on_<rrggbb>.jpg`，落在原图旁边；`--bg` 色值接受 6 位 hex，`#` 可带可不带。
12. 不读仓库 `.env`：不新增隐式配置源；`.env` 保持「模板」用途，`.env.example` 注释同步更新。
13. 旧 spec（2026-06-11）正文不动笔；取代关系在本文头部声明。
14. 模型目录只预制 GPT 系 5 个（用户偏好）；目录是快照不是白名单——`--model` 不校验、自由传，新模型无阻。
15. `--list-models` 走 stdout、不触网，只读内置常量；输出带快照日期与官方模型页 URL，并注明「以官方页为准」。
16. 目录价格双写「积分 + ≈人民币」，明确标注为快照；官方模型页地址固定写 `https://grsai.com/zh/dashboard/models`。

## 审核修订记录

（待审）
