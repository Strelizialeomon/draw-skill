# draw-skill 设计文档

- 日期：2026-06-11
- 状态：已确认，待写实现计划
- 作者：用户 + Claude（brainstorming）

## 目标

把一个"OpenAI 风格、异步生图"的接口，封装成一个能在 Claude Code 里调用的技能。
用户说"帮我画一张 X"，技能负责：提交生图任务 → 轮询等待 → 把成图下载到本地 → 返回本地路径。

## 不做（YAGNI，需要时再加）

- 本地图片垫图（接口只接受公网 URL，本地图要先上传到可公开访问处，先不做）
- webHook 回调（技能跑在本地，没有公网地址接收回调，只走主动轮询）
- 并发批量生图

## 接口契约（来自用户提供的真实接口）

域名记为 `{BASE}`，由环境变量提供。鉴权统一带：

```
Content-Type: application/json
Authorization: Bearer <IMAGE_API_KEY>
```

### ① 提交生图

`POST {BASE}/v1/draw/completions`

请求体：

```json
{
  "model": "gpt-image-2",
  "prompt": "描述您想要生成的图像内容的提示词",
  "aspectRatio": "1024x1024",
  "urls": ["https://example.com/example1.png"],
  "webHook": "https://example.com/callback",
  "shutProgress": false
}
```

- `urls`：可选，参考图（垫图）的公网 URL 数组；不传即纯文生图。
- `webHook` / `shutProgress`：本技能不使用（不传或传默认值）。

返回体：

```json
{ "code": 0, "msg": "success", "data": { "id": "id" } }
```

- 任务 id 在 `data.id`。

### ② 查询/轮询结果

`POST {BASE}/v1/draw/result`

请求体：

```json
{ "id": "xxxxx" }
```

返回体：

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "id": "xxx",
    "progress": 100,
    "status": "succeeded",
    "failure_reason": "",
    "error": "",
    "results": [ { "url": "https://example.com/example.png" } ]
  }
}
```

- `data.status`：`succeeded` 完成；`failed` 失败；其它（如 `pending`/`running`）视为进行中，继续轮询。
- `data.progress`：0~100，进度，轮询时打印给用户看。
- 成图是 `data.results[].url`（**URL 链接，不是 base64**），可能多张。

## 架构

单个零依赖 Python 脚本 + 一个 SKILL.md。

- `draw.py`：只用 Python 标准库（`urllib`、`json`、`argparse`、`time`、`os`），**不需要 pip install**。
- `SKILL.md`：技能说明，告诉 Claude Code 何时、如何调用 `draw.py`，以及环境变量怎么配。

脚本内部可按职责分成几个小函数（提交 / 轮询 / 下载 / CLI 入口），单文件即可；若将来变大再拆模块。

### 调用形态

```
python3 draw.py "<prompt>" \
  [--model gpt-image-2] \
  [--aspect 1024x1024] \
  [--ref <公网图URL>]...   # 可重复，对应接口的 urls[]
  [--out <目录>]           # 默认当前目录
  [--url-only]             # 只打印图片 URL，不下载
```

- 默认行为：阻塞等待，成功后把每张图下载到 `--out`，打印**绝对路径**（方便 Claude 直接 Read/展示）。
- 文件名规则：`draw-<id>-<序号>.png`（多张时序号递增）。

## 数据流

1. 读取环境变量 `IMAGE_API_KEY`、`IMAGE_API_BASE`；缺任一则报错退出。
2. 组装请求体，`POST /v1/draw/completions`，从 `data.id` 取任务 id。
3. 循环：每 **3 秒** `POST /v1/draw/result`，按 `status` 分支：
   - `succeeded` → 进入下载步骤。
   - `failed` → 打印 `failure_reason` / `error`，非零退出。
   - 其它 → 打印 `progress`，继续等。
   - 累计超 **5 分钟（300 秒）** → 超时报错，非零退出。
4. 逐个下载 `data.results[].url` 到 `--out`，打印本地绝对路径。
5. `--url-only` 时跳过下载，直接打印 URL 列表。

## 配置

| 环境变量 | 必填 | 说明 |
|---|---|---|
| `IMAGE_API_KEY` | 是 | Bearer token |
| `IMAGE_API_BASE` | 是 | 接口域名，如 `https://api.example.com`（不含路径） |

- **绝不在脚本里硬编码 key**。
- 缺失时给出清楚的中文报错，提示用户先 `export`。

可选默认值（写死在脚本里，可被参数覆盖）：
- `model = gpt-image-2`
- `aspect = 1024x1024`
- 轮询间隔 `3` 秒，超时 `300` 秒。

## 错误处理

| 情况 | 行为 |
|---|---|
| 缺 `IMAGE_API_KEY` / `IMAGE_API_BASE` | 打印提示，退出码 2 |
| HTTP 非 2xx / 网络异常 | 打印状态码与响应片段，退出码 1 |
| 返回 `code != 0` | 打印 `msg`，退出码 1 |
| `status == failed` | 打印 `failure_reason` / `error`，退出码 1 |
| 轮询超时 | 打印"已等待 N 秒仍未完成"+ 最后进度，退出码 1 |
| 图片下载失败 | 打印 URL 与原因，退出码 1（仍打印已成功的图） |

所有报错走 stderr；正常的本地路径走 stdout，方便 Claude/脚本解析。

## 测试

- 把 HTTP 调用收敛到一个可替换的函数（如 `_http_post(url, body)`），便于用桩（stub）做单元测试，覆盖：提交解析 id、轮询的三种 status 分支、超时、`code != 0`、缺环境变量。
- 一个真机冒烟测试：配好环境变量后用一句便宜 prompt 实际跑通"提交→轮询→下载"。
- 不依赖外网的测试用桩数据，不打真接口。

## 技能落地

- 项目放 `~/code/draw-skill/`，内含 `draw.py`、`SKILL.md`、`README.md`。
- 软链到 `~/.claude/skills/draw`（与 `hi-backend`、`google-design-skill` 一致的玩法）。
- 命令名 / 技能名：`draw`（将来 `/draw 帮我画只猫`）。
- SKILL.md 的 frontmatter `description` 要覆盖触发词：生图、画图、生成图片、文生图、draw image 等。

## 待确认项

无（接口三步、id 字段、默认参数、目录名、出图位置均已与用户确认）。
