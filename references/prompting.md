# 提示词怎么按落地场景写

出图前先想清楚「这张图最后怎么用」，再把下面六条写进提示词。每条附一个 jasmine-lottery 2026-10-05 实战的例子。

1. **写清用途和落地位置**：别只写画面内容，把「用在哪」写进去。例：「used as the background plate of a mobile Halloween night page」。
2. **布局留位**：用百分比说清哪些区域要留空（之后要叠标题、按钮、盒子）。例：「Keep the top 15% of the image plain dark sky … (a title will sit there)」「no objects in the middle 60% of the width between 20% and 72% of the height」。
3. **色值对齐落地页面**：直接把页面用的 hex 写进提示词，出图就不会跑色调。例：「deep midnight indigo (#11132A …)」。
4. **用途约束写死**，让之后好处理：
   - 剪影类（之后要二值化、描矢量）：「Pure black and pure white only: no gray, no shading, no texture」。
   - 拼图类（之后要切块）：「arranged in two rows … generous even spacing … none touch each other」。
   - 纯色底类（之后要色键抠图）：「plain flat … background」。
5. **写明不要什么**：AI 爱加文字、边框、多余元素。例：「No text, no frame, no border」。
6. **风格要否掉时，用用户原话改方向**：例：徽章第一版被用户说「像是一个邪恶的女巫」，第二版提示词改写成「Cozy and kind … Not spooky, not dark … no witchy or magical mood」，过了。

## 原文位置

jasmine-lottery 仓里那四份提示词（只给路径，不复制全文——它们是那个项目的资产，复制过来就成了会变旧的副本）：

- `assets/papercut-garden/prompts/plate.txt` —— 全屏背景：用途、布局留位、色值、写明不要什么
- `assets/papercut-garden/prompts/branch-corner.txt` —— 单色剪影
- `assets/papercut-garden/prompts/lanterns.txt` —— 单色剪影、多个元素一张图
- `assets/prize-placeholder/prompt.txt` —— 拼图 + 纯色底 + 风格改方向
