<!--
请使用中文撰写本 PR。标题建议使用 Conventional Commits 前缀，例如：
  feat(scope): 简短描述
  fix(scope): 简短描述
  refactor(scope): 简短描述
  docs(scope): 简短描述
  test(scope): 简短描述
  chore(scope): 简短描述
-->

## 概述 / Summary

<!--
用 1～3 条要点说明这个 PR 做了什么、为什么要做，重点讲清楚「动机」与「影响范围」。
避免简单复述 diff，要让 reviewer 看完就能判断是否需要深入。
-->

-
-

## 改动详情 / What changed

<!--
按模块（后端 / 前端 / 测试 / 脚本 等）分块列出关键改动。
引用文件时使用相对路径 + 行号，方便 reviewer 跳转，例如：
  - [job_lifecycle.py](backend/app/domain/job_lifecycle.py:120) — 在终态广播后触发 batch 钩子
-->

### 后端 / Backend

-

### 前端 / Frontend

-

### 测试与脚本 / Tests & scripts

-

## 设计动机 / Why

<!--
可选。如果改动涉及非显而易见的取舍、踩过的坑、或对架构有影响，请在这里说清楚：
- 为什么选这条路而不是另一条？
- 有哪些已知限制 / 后续待办？
-->

## 兼容性与风险 / Compatibility & risk

<!--
可选。列出可能影响线上 / 其他模块的点：
- 数据库迁移、配额、SSE 协议、外部依赖、环境变量等
- 回滚策略
如果没有这类影响，可以删掉本节。
-->

## 测试计划 / Test plan

<!--
用 checklist 列出验证步骤，覆盖「正向路径」与「边界 / 回归」。
对人工验证的步骤，写清楚预期结果，方便 reviewer 复现。
-->

- [ ] `.venv/bin/python -m pytest backend/tests` 通过
- [ ] `cd frontend && pnpm run build` 通过
- [ ] `cd frontend && pnpm run test:e2e`（如涉及前端交互）通过
- [ ] 手动验证：

## 截图 / 录屏（可选）

<!--
UI / 交互类改动建议附上截图或短录屏，前后对比更佳。
-->

## 关联 Issue / PR

<!--
例如：Closes #123、Refs #456
-->
