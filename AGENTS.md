# AGENTS.md —— draw-skill 项目指令

## 流程规矩：来自 spec-flow

**本仓的复杂活走 spec-flow 的两段流程。**
规则的**唯一真相源**是 spec-flow，**本文件不复述它的步骤**——要看细则就去读真身。

| 项 | 值 |
|---|---|
| 远端真身 | `https://github.com/Strelizialeomon/spec-flow`（`SKILL.md` + `references/`） |
| 本机 skill | `~/.claude/skills/spec-flow`（本机为 spec-flow 仓库的 git 检出） |
| 怎么用 | 开工前调 skill `spec-flow`；**认领 issue 做实施也算开工**（从段二进）；**没装 skill 的机器**直接在会话里读上面那个远端的 `SKILL.md` |

### ⚠️ 本仓不放副本、不做任何同步机制

spec-flow 的 `references/adoption-model.md` 记了四种采纳动作：**只指向式**（2026-09-22 起新仓默认）、同步 + 指向（存量副本仓）、不引用、当场引用。
本仓选**只指向式——只指向、不复制**。

理由（也是 spec-flow 自己的历史教训）：**静止副本 = 分叉，会变旧**——它建仓之前活在三处静止副本里、零历史，
追一条规矩的来历追到 2026-08-24 就断档了。本仓不复制，**压根不存在第二份**。

**要改流程规矩 → 去改真身并提交**，本仓自动跟着变，**不用改本文件**。
反过来，哪天发现两处对不上，那就是有人绕过了 git——**别在这边就地修正**。
