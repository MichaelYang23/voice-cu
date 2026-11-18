# voice_cu — 语音控制 Mac 的 v0 原型（Kev 本地决策）

```
按住右 Option 说话 → 本地 Whisper (MLX) 转写
   → 规则快速通道（打开 App / 音量 / 输入 / 常用快捷键 / 滚动，0 次模型调用）
   → 否则：读当前窗口 Accessibility 树 → 候选元素列表
   → Kev（本地 /v1/systemone）一次请求回答 5 个问题：action / target / key / scroll / app
   → 执行：AXPress 优先，失败才模拟鼠标；键盘用 CGEvent（支持中文）
   → 每条指令记录 JSONL + 分阶段耗时
```

**关键设计**：屏幕快照作为 `state`，语音指令放进问题文本里。屏幕不变时 Kev 复用 state 的缓存，
只为问题付费（Kev README：Kev-4B 在 Apple M5 上新文本 ~721 ms，重复 ~136 ms）。

> ⚠️ Kev 基于 Qwen —— 只在你自己的 Mac 上跑，不要放到 Ganymede2 / UTD 系统上。

## 1. 起 Kev 服务（终端 A）

```bash
git clone https://github.com/jaredpalmer/kev.git && cd kev
uv sync --extra serve
uv run --extra serve python -m kev.serve --run jaredpalmer/kev-0.8b --port 8009
```

- Kev 需要 Python 3.12/3.13；首次运行会下载 adapter 和 Qwen 底座。
- README 说 Apple Silicon 走 MLX。如果 serve 在 Mac 上报依赖错误，看 repo README 里 Apple Silicon 那一节（可能需要额外的 extra）。
- **先用 0.8B** 跑通（M5 上 ~149 ms 新 / ~28 ms 重复），准确率不够再换 `jaredpalmer/kev-4b`。
- 换后端只改环境变量：`KEV_BASE_URL`、`KEV_MODEL`、`KEV_API_KEY`（同一个 System One 协议，也能指向托管的 Jev）。

## 2. 装原型（终端 B）

```bash
cd voice_cu
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**权限**（系统设置 > 隐私与安全性），给你运行脚本的终端（Terminal / iTerm / Ghostty…）勾上，然后**重启终端**：
- 辅助功能（读 AX 树、点击、打字）
- 输入监控（全局热键）
- 麦克风

## 3. 运行

```bash
# 先用打字模式测管线（不需要麦克风）：输入指令后它会作用在终端后面那个窗口上
python -m voice_cu.main --text --dry-run     # 只决策不执行，看 Kev 选了什么
python -m voice_cu.main --text

# 语音模式：按住右 Option 说话，松开执行
python -m voice_cu.main
python -m voice_cu.main --lang zh            # 固定中文，省掉语言检测时间

# 收集训练数据：每个模型决策都问你对不对（输入 e12 可以纠正成点 e12）
python -m voice_cu.main --confirm
```

可以试的指令：`打开备忘录`、`新建`、`输入 明天下午三点开会`、`点一下发送`、`click the share button`、
`往下翻`、`音量调到 30`。

每条指令打印：
```
[model] click e7
  ⏱  stt=420ms  route=0ms  ax=85ms  kev=160ms  exec=12ms  total=680ms
```

## 4. 日志 = 第 1 步的训练数据

`logs/decisions-YYYYMMDD.jsonl`，每行一条：`command`、`state`（屏幕文本）、`candidates`、`answers`（全部概率）、
`plan`、`label`（`--confirm` 模式下你的确认或纠正）、`timing`。

## 已知限制（v0）

- **非流式 STT**：松开键才开始转写。要做到「话没说完就执行」，下一步换成 macOS 的 SFSpeechRecognizer 流式部分结果，
  在部分转写稳定时提前触发规则通道。
- 不读菜单栏（用快捷键代替）；网页内容在 Chrome 里 AX 树很深，网页任务更适合 browser-use 的 Jev Ultrafast 路线。
- 「写一封回复说…」这类需要生成文本的指令：设置 `ANTHROPIC_API_KEY` 并 `pip install anthropic` 后由 LLM 生成，
  否则只支持「输入 xxx」逐字输入。
- Kev 主要是英文训练的，中文指令准确率可能偏低 —— 正好用 `--confirm` 收集中文数据。

## 离线测试

```bash
pip install pytest && python -m pytest tests -q    # 路由规则 + 用 mock 服务器测 Kev 客户端
```
