# 原始运行数据

`docs/REPORT.md` 里的每个数字都出自这里，可回溯到具体某一次运行。

```
incident-triage-1786868857/   第一轮：八格全轴，8 格 × 3 次
policy-audit-1786879027/      第二轮：prompt 剂量，4 格 × 3 次
policy-audit-1786882893/      第三轮：预注册确证 b1 vs a2b，2 格 × 10 次
```

每个目录含：

- `runs.jsonl` —— 每次运行一行：判分结果、完整代理指标、stdout 尾部
- `runs.jsonl.bak` —— `bin/reattribute.py` 修复会话归属**之前**的版本。保留它是为了让那次修复本身可审计：第一轮有一格的会话被机器上另一个并发 dsh 实例抢走了，事后按工作区路径重新归属。diff 两个文件即可看到改了什么
- `summary.txt` —— 汇总表（均值±标准差）

## 脱敏说明

工作区路径中的家目录前缀已由 `/Users/<本机用户名>` 替换为 `/Users/REDACTED`。路径的相对结构完整保留，不影响任何结论的核验。

## 注意

这些数据来自单一模型（deepseek-v4-pro）、单一 `reasoningEffort: max`、单一机器。跨机器直接比较绝对耗时没有意义。
