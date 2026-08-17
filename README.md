# dsh-ablation

**一把用来验证 dsh 效果主张的尺子。**

DeepSeek Harness 生态里出现了一类「效果提升插件」，它们都建立在同一类因果假设上：Standard 预设比 Minimal 差，是因为某个具体机制。这些假设可能对，但很少有人把 Minimal 与 Standard 的差异拆成独立变量逐条测过，也很少有人报告样本量。

这个仓库提供拆分与测量的工具，以及一份用它做出来的实测报告。

> **报告结论提前说**：三轮实验 56 次运行，推翻了三个假设——**三个都是本仓库作者自己提出的**。
>
> 但请连同它的边界一起读：56 次运行**并非平均分配**，**只有一条轴做过 N=10 的预注册确证**（移除提示词里的委派编排块，对通过率无可检出影响）。其余四条轴没有被证明无效，只是**没有被有效测量**——③ 从未跨过触发阈值，①④⑤ 只在能力分饱和的任务上跑过 N=3。详见 [`docs/REPORT.md`](docs/REPORT.md) 第 7 节。

## 动机

本项目源于想验证 [dsh-anchored-standard](https://github.com/xiaobright/dsh-anchored-standard) 的核心假设：**首次请求的 tool schema 是决定模型轨迹的变量**。

该项目公开的证据是 Project2 上的两次运行（能力分 98/99）与推理首行的风格计数（"We need…" vs "Let me…"）。本台子在**自己的**任务集上测得单格通过率约 60%，此时 **N=3 的实验有约 1/6 概率凭空产生极端对比**（详见报告第 5 节的计算）。

这**不能说明它的结论是错的**——它测的是 Project2，本项目没有复现那个任务。它说明的是：**这类主张需要多大样本量才能与噪声区分，是可以事先算出来的**，而这件事值得在写插件之前先做。

## 它测什么

Minimal 与 Standard 的五条轴，从预设定义本身读出（不是从文档）：

| 轴 | Minimal | Standard |
|---|---|---|
| ① 工具目录 | 2 把 | 25 把 |
| ② system prompt | 一句话 + `complete: true` | ~4.4KB，来自十余个插件 |
| ③ 上下文完整性 | 无 compaction | `compaction-basic` + `tool-result-pruner` |
| ④ shell 形态 | 持久 PTY bash | 沙箱一次性 bash |
| ⑤ fs 层 | 裸 `fs-local` | host 沙箱 policy |

每条轴一个 overlay，外加两个基线和一个 anchored-standard 复刻对照组。

## 三个设计要点

**1. 不用改 harness。** `request/header` 事件把渲染后的完整 system prompt 与装配后的完整 tool schema 写进持久化 session log，所有代理指标离线可算。

**2. 先证明格子是它自称的东西。** `bin/capture_references.py` 拿真实的预设组合会话与格子逐字段比对，不一致就不许开跑。这个门槛抓到过一次真错误：裸组合与官方 Standard 都是 25 把工具，但成员差一次对调（`str_replace_editor` ↔ `ask_user_question`）——只比数目会把错误基线带进全部格子。

**3. 用 `restrict` 而不是 `disabled` 改工具目录。** dsh 里工具插件同时拥有自己的提示词段，禁用工具行会连带抽走提示词：实测 system prompt 从 4063 掉到 315 字符，名义上动①、实际同时动了②的 92%。

## 环境要求

- dsh **0.1.0-rc.6**（overlay 按 row id 定位，版本不符时 `doctor` 会拒绝——见下）
- Node（dsh 支持的版本）、pnpm、`zstd`
- 已配置好可用的模型路由（`$DSH_HOME/settings.yaml`）

## 使用

```bash
./bootstrap.sh                      # 建 profile，幂等
python3 bin/doctor.py               # 自检，全绿才算能跑
python3 bin/run_matrix.py --task tasks/policy-audit --repeats 3 --dry-run
python3 bin/run_matrix.py --task tasks/policy-audit --repeats 3
```

`doctor` 检查的每一项都对应一个开发期真实发生过的错误，其中**版本不符是致命而非警告**：row id 一旦移动，patch 会静默地匹配不到任何东西，格子于是在测量基线却自称在测量某条轴。

## 目录

```
overlays/     实验单元，每格一个 --patch overlay
              _base-standard  把裸 base+headless 修正成官方 standard；每格必叠
              b0/b1           两个基线
              a1..a5          五条单轴
              a1p-anchored    anchored-standard 复刻（对照组）
              a2a/a2b         prompt 轴的剂量拆分
plugins/      两个零构建 ESM 辅助插件
              dsh-ablation-prompt   complete 段 + 按名遮蔽提示词段
              dsh-ablation-anchor   工具目录白名单，permanent / anchored 两种模式
bin/          doctor / bootstrap 校验 · 指标提取 · 准入门槛 · 批量执行 · 重归属 · 重计分
tasks/        任务集，每个含 task.md / setup.sh / grade.sh
docs/         实测报告
```

## 加自己的轴

一条轴 = 一个 overlay 文件。写完用 `bin/doctor.py` 确认能合入，跑一次确认 `request/header` 里变的**只有你想变的东西**——工具数或 prompt 字符数意外移动，就说明这条轴不干净。

## 方法上的三条纪律

它们都是从踩坑里来的，写进工具而不只是写进文档：

- **单次运行不叫复现。** 本仓库因此撤回过一个「2.4× 成本优势」的结论。
- **事后从多个格子里挑对比 = garden of forking paths。** 定案实验必须预注册（模板见 `results/PREREGISTRATION-*.md`），锁死主结局指标、检验方式、阈值与作废条件。
- **成本指标（token / 秒）与正确率分开报，不混为一谈。** 前者方差极大。

## 它不是什么

**不是 benchmark，也不是 benchmark 的替代品。**

benchmark 回答"系统 X 有多强"，本项目回答"只改一个变量，结果会不会变"。关键差别是 benchmark **不验证被测配置是否名副其实**——它会老实地给一个标签错误的配置打分。本项目的准入门槛抓到过一次这种错误（两个配置都是 25 把工具，但成员差一次对调），**那种错误在任何 benchmark 上都会静默通过**。

**为什么没直接用 SWE-bench 之类：主要是成本，不是原则。** 按实测量级，SWE-bench Verified 跑完一个格子一次约需 1300 万 output token，8 格 ×3 次约 3.1 亿——本项目全部 56 次运行合计约 150 万。差三个数量级。

**因此自制任务集是本项目最大的弱点**：没有已知的难度分布与区分度（代价是可见的——三个任务设计里前两个都在正确率上饱和），而且全是"读文档 + 判断"，没有写代码、没有多轮交互。**任务分布很可能恰好落在效应不存在的区域。**

合理的用法是把它放在 benchmark **之前**：先确认变量真的被隔离了、先估出方差算出需要多大 N，**然后**再去成熟 benchmark 上花那笔钱。详见报告第 8 节。

## 已知边界

- **只有一条轴做过 N=10 确证**；其余四条是 N=3 的探索性观测，且多数任务上能力分饱和（见报告第 7 节的逐格证据级别表）
- N=10 的零结果只能排除大效应，**未排除 10–20 个百分点量级的差异**
- 单一模型、单一 `reasoningEffort`（v4-pro / max）。换 effort 重跑是最便宜的一次反驳尝试，尚未做
- overlay 绑定 dsh 0.1.0-rc.6 的 row id
- ① 与 ⑤ 不是完美单轴（残留约 8.6% 的提示词耦合；⑤ 必然改变目录），overlay 内已逐条标注
- ③ 从未被触发：`tool-result-pruner` 是 compaction 的子步骤，阈值 0.8 ⇒ 256k 窗口下需约 20 万 token。该轴状态是**未测**，不是**无效**
- 准入门槛只覆盖 B0/B1；A 系列各格从未与任何外部参照比对过
- B1 与真实 Web Standard 差 3 段 web-app 专属提示词，headless 下无法复现，只能声明

## License

MIT
