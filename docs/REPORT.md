# 实测报告：Minimal 与 Standard 的五条轴，逐条测下来

2026-08-16 ｜ dsh 0.1.0-rc.6 ｜ deepseek-v4-pro，`reasoningEffort: max`

这份报告既是结果，也是这套工具的使用示范。三轮实验一共 56 次模型运行，**推翻了三个假设——三个都是本文作者提出的**。

---

## 0. 起点

DeepSeek 官方称 v4-pro 的能力在极简模式下才能正常体现，社区据此出现了一类「效果提升插件」。代表作 [dsh-anchored-standard](https://github.com/xiaobright/dsh-anchored-standard) 的做法是：让会话的第一次请求只暴露 Minimal 的两把工具，等会话产生第一个durable 事件后再晋升到完整的 25 把。

它的核心主张是：**首次请求的 tool schema 是决定性变量**，模型的轨迹在第一次请求就被定型。

这个主张可能对。但 Minimal 与 Standard 之间不止工具目录一个差异，而没有人把这些差异拆开单独测过。本项目就是去做这件事。

---

## 1. 五条轴是怎么读出来的

不是从文档，是从预设定义本身：`apps/cli/config/agent-presets/{minimal,standard}/agent.cordis.yml`。

| 轴 | Minimal | Standard |
|---|---|---|
| ① 工具目录 | 2 把 | 25 把 |
| ② system prompt | 一句话 + `complete: true` + `includeRuntimeContext: false` | 模板 persona + AGENTS.md 摘要（上限 64KB）+ plan-mode 段 + skill 目录 + 每把工具的 description |
| ③ 上下文完整性 | 无 compaction | `compaction-basic` + `tool-result-pruner` |
| ④ shell 形态 | 持久 PTY bash | 沙箱一次性 bash + 审批往返 |
| ⑤ fs 层 | 裸 `fs-local` | host 沙箱 policy |

anchored-standard 只攻 ①，且只作用于第一次请求。

---

## 2. 仪表：不用改 harness 一行代码

`request/header` 事件把**渲染后的完整 system prompt** 与**装配后的完整 tool schema 数组**原样写进持久化 session log，并在头部变化时追加新事件（`reason: initial | resume | change`）。

加上 `reasoning-chunks`、带 `usage` 的 `assistant/message`、`tool/call` / `tool/result`、以及记录被销毁 token 数的 `compaction/prune`——**所有代理指标都能从落盘日志离线算出**。不需要 patch `llm.stream`，不需要 fork harness。

日志在 `$DSH_HOME/sessions/<cwd 编码>/session-<id>/session.jsonl.zstd`，`zstd -dc` 即得纯 JSONL。

---

## 3. 准入门槛：先证明格子是它自称的东西

这一步是整套方法里最容易被跳过、也最不该跳过的一步。

B0 声称等于官方 Minimal，B1 声称等于官方 Standard。**这是需要被证明的，不是假设。** 证明方式：拿一个真实的、由预设组合出来的会话，与格子的 `request/header` 逐字段比对（`bin/check_header_equivalence.py`）。

参照必须来自**未被污染的** profile。这一点在本机被实测证实是必要的：作者日常的 web profile 装了第三方插件，`ssh_*`(6) + `vision_*`(3) 把 Minimal 从 2 把变成 11 把、Standard 从 25 变成 34——**两个预设上的污染量完全一致，交叉印证**。任何以它为基线的对照都是脏的。

### 门槛抓到的第一个错误

裸 `base + headless` 组合出 25 把工具，官方 `standard` 预设也是 25 把。作者据此写下「干净的 headless profile 已经就是 Standard」——**数目对，成员错**：

- base 有 `str_replace_editor`，无 `ask_user_question`
- standard 正好相反
- 另有 `subagent_fork` 的 `backgroundMode` 差异（`one-shot` vs `continuable`），它还会改变一段系统提示词

**25 = 25 的巧合掩盖了一次两把工具的对调。** 如果门槛只比数目，这个错误会进入全部 8 个格子；而且因为所有格子错得一样，跑出来的差分看起来还会完全自洽。

修正在 `overlays/_base-standard.yml`，每个格子开跑前必叠。

---

## 4. 三轮实验

### 第一轮：八格全轴（incident-triage，8 格 × 3 次）

**能力分饱和**：24 次里 23 次全对。唯一未过的一次，模型花 38k reasoning token 论证一个目标不算凭据泄露——那是站得住的判读，说明任务的基准答案本身有歧义。去掉该项后八格全部 100%。

**假设一被推翻：③ 不是「每轮都在扣分」。**

24 次运行的 `prune_shadowed_tokens` **全为 0**。查源码发现 `tool-result-pruner` 不是常开的 8KB 铡刀，而是 compaction 的子步骤，而 compaction 的触发阈值是：

```
compaction-basic/src/config.ts:20
const DEFAULT_THRESHOLD_RATIO = 0.8
```

256k 窗口下要约 20 万 token 才触发。本任务约 6k。作者此前「③ 每一轮都在扣分」的判断**是错的**——它只在超长会话里存在。

### 第二轮：prompt 剂量（policy-audit，4 格 × 3 次）

四个格子**工具目录完全相同（25 把）**，只有提示词内容不同。Standard 的 4441 字符分成两个体量相当的块：工具用法指导 1977 字符、委派编排指导 2214 字符。

**假设二被推翻：不存在「prompt 轴的成本优势」。**

第一轮曾报告「A2 比 B1 省 2.4–3.2 倍 token」。那个结论建立在每格 N=1 上。N=3 之后 out_tok `19915±8429` vs `23872±6612`、秒 `231.7±100.1` vs `299.4±79.2`——**区间完全重叠，A2 名义上还更慢**。

但这一轮出现了一个看起来更干净的效应：

| 格子 | sysChars | 通过 |
|---|---|---|
| b1-standard（全量） | 4461 | **0/3** |
| a2a-toolguide（去工具用法块） | 2470 | 2/3 |
| a2b-delegation（去委派编排块） | 2240 | **3/3** |
| a2-prompt（全去） | 46 | 3/3 |

作者当时列出三条排除性证据：非长度效应（a2a 更长却更差）、非委派行为（`subagent`/`workflow`/`ralph`/`goal` 调用 0 次）、非行为差异（b1 与 a2b 的工具调用分布几乎一致）。**这三条后来被证明全都成立，也全都无关。**

### 第三轮：预注册确证（b1 vs a2b，N=10）

预注册写于开跑之前（`results/PREREGISTRATION-b1-vs-a2b.md`），锁死主结局指标、检验方式、阈值与作废条件。

```
cell            n   pass       sysC
b1-standard    10   6/10  4461 字符
a2b-delegation 10   6/10  2240 字符
```

**Fisher 双尾 p = 1.0000，通过率差 = 0 个百分点。假设三被推翻。**

对称得刺眼：两格各 6/10 通过、**各 0 次误报**、**各漏同一个目标恰好 4 次**。失败模式完全一致。

---

## 5. 那个「0/3 vs 3/3」是怎么来的

在两格真实通过率都是 60% 的前提下：

- b1 三战三败 = 0.4³ = 0.064
- a2b 三战三胜 = 0.6³ = 0.216
- 同时发生 ≈ 0.014，约 1/72

看起来很不可能。**但第二轮是从 4 个格子里事后挑出这一对**——可挑的两两组合有 6 种，任意一对出现这种极端对比的概率约 **0.155**。

也就是说：**在纯噪声下，那一轮有约六分之一的机会造出一个「漂亮」的结果。作者确实看到了，并且差点据此去写插件。**

这就是 garden of forking paths。它不需要任何人作弊，只需要在小样本上多看几个格子。

---

## 6. 任务集：三次尝试的教训

| 任务 | 规模 | 结果 |
|---|---|---|
| incident-triage | 20KB 检索 | 23/24 全对，饱和 |
| incident-triage-v2 | 80KB，六目标，反向 grep 陷阱 | 6/6，58 秒，饱和 |
| policy-audit | 58KB，三条件合取 + 两条例外 | 通过率约 60%，可用 |

v2 那次把敏感词全部挪进干扰项，让 `grep key|token|password` 只返回错误答案。模型确实咬钩（首行就是 *"Let me grep for security-related terms"*），**但它同时又把全文读了一遍**，陷阱零代价。

**根本原因**：23k token 只占 256k 窗口的 9%，模型没有出错的理由。要靠「读不完」制造失败，文件需要约 18 万 token/次——那也才会跨过 ③ 的 0.8 阈值。

唯一能产生失败的是**推理深度**：多条件合取 + 例外。

---

## 7. 结论

**在本项目的任务分布上，Minimal 与 Standard 的五条轴，没有一条产生了可复现的效果差异。**

这不等于说 anchored-standard 的结论是错的——它测的是 Project2，本项目没有复现那个任务。可以说的是关于**证据强度**：

- 它公开的证据是 Project2 上的两次运行（98/99）加首行风格计数
- 本台子在自己的任务集上测得单格通过率约 60%，**N=3 有约 1/6 概率凭空产出极端对比**
- 按这个方差，两次运行能支撑的结论比通常被理解的要弱

**这类主张需要多大样本量才能与噪声区分，是可以事先算出来的。** 这也是本项目希望留下的东西：不是又一个未经验证的效果插件，而是一把尺子。

---

## 附：三个假设的死法

| 假设 | 提出者 | 怎么死的 |
|---|---|---|
| ③ 上下文完整性「每一轮都在扣分」 | 本文作者 | pruner 是 compaction 子步骤，阈值 0.8；24 次运行 pruned 全 0 |
| ② prompt 轴带来 2.4–3.2× 成本优势 | 本文作者 | 每格 N=1 的噪声；N=3 下区间完全重叠 |
| 委派提示词块损害正确率 | 本文作者 | 预注册 N=10：6/10 vs 6/10，p=1.0 |
