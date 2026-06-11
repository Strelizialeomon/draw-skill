# draw-skill

把异步生图接口（提交 → 轮询 → 取图）封装成零依赖 Python 脚本，并作为 Claude Code 技能使用。

## 安装

```bash
ln -sfn ~/code/draw-skill ~/.claude/skills/draw
export IMAGE_API_KEY="sk-xxx"
export IMAGE_API_BASE="https://api.example.com"
```

把上面两个 `export` 写进 `~/.zshrc` 可长期生效。

## 直接用脚本

```bash
python3 draw.py "一只戴墨镜的柴犬" --out ./out
```

成功后 stdout 打印图片本地绝对路径，stderr 打印进度。常用参数：

- `--model`：模型名。优先级 `--model` > 环境变量 `IMAGE_MODEL` > `gpt-image-2`
- `--aspect`（默认 `1024x1024`）
- `--ref <公网图URL>`：参考图/垫图，可重复多张（本地图暂不支持）
- `--out <目录>`：保存目录，默认当前目录
- `--url-only`：只打印图片 URL、不下载
- `--timeout`：流读取超时秒，默认 300

> 接口是流式的：发起一个请求后挂住等待，期间把进度打印到 stderr，生成完（约 1 分钟）直接拿到图。

## 测试

```bash
python3 -m unittest discover -s tests -t . -v
```

设计与实现细节见 `docs/superpowers/specs/2026-06-11-draw-skill-design.md`
与 `docs/superpowers/plans/2026-06-11-draw-skill.md`。
