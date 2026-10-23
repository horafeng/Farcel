# Phase 9 — Modelica 后端最终验收

## 验收范围

Phase 9 建立了单个 Modelica `.mo` 文件经 OpenModelica 编译为 FMI 2.0 Co-Simulation FMU，
再由既有 Farcel 运行时加载、验证、仿真和导出的完整后端链路。本文件描述已实现能力，
不表示 main 已合并或 PySide6 GUI 已实现。

## 分阶段成果

| 阶段 | 已完成成果 |
| --- | --- |
| 9.0 | 真实 OMC → FMU → Farcel 可行性、数值和 CSV 验证。 |
| 9.1A–D | 环境检测、编译合同、受控 OMC adapter 和独立编译公共 API。 |
| 9.2A–D | 来源合同、源快照/构建包、schema 1.1、显式迁移、持久化和 Project 登记。 |
| 9.3A–B | 独立和 Project Modelica CLI。 |
| 9.3C | CLI 退出码、未登记构建包诊断、全链路回归和交付审计。 |

## 支持范围和公共 API

支持单个 `.mo`、一个普通或点分限定类名、FMI `2.0`、Co-Simulation `cs`、本地同步编译。
不会自动运行 FMU。公开调用为：

```python
backend = create_backend()
environment = backend.get_modelica_environment(compiler_path=None)
compiled = backend.compile_modelica(ModelicaCompileRequest(...))
project = backend.open_project(project_root)
migration = backend.migrate_project_schema(project_root)  # 仅显式调用
registration = backend.compile_project_modelica(project, ModelicaAssetStagingRequest(...))
builds = backend.list_project_modelica_builds(registration.project)
```

独立编译使用 `ModelicaCompileRequest`；Project 编译使用
`ModelicaAssetStagingRequest`。contracts、结果和错误不会泄露 OMC、subprocess、FMPy 或 Qt。
创建 backend 不启动 OMC；只有明确编译才会启动它。

环境检测优先接受显式 OMC 路径；否则依次检查 `OPENMODELICAHOME`、PATH 和常见 Windows
目录，并以真实 `omc --version` 判定可用性。显式无效路径不回退。

## Project 与数据安全

Project 1.0 可读、可保存、可运行，普通打开/保存绝不自动升级。用户必须明确调用迁移才能得到
1.1 和由迁移服务命名的备份。schema 1.1 可持久化 `modelica_build_provenance`。
`ModelAsset.sha256` 是 FMU 散列唯一权威；`ModelicaSourceSnapshot.sha256` 标识保存的源快照。

发布构建包只含归属标记、`source/model.mo` 与 `model/model.fmu`；临时
`compiler-output/` 不发布。路径均相对 Project，因此复制/移动整个 Project 后仍可验证。

Project 登记是乐观条件保存：编译前记录 project.json 散列，保存前再次检查。陈旧对象或并发
修改返回 `PROJECT_CONFLICT`，不默默覆盖。最终散列检查与 `os.replace()` 之间仍有有限的
跨进程 TOCTOU 窗口；它不是数据库事务或全局文件锁。

若构建包已发布但 `pre_persist` 验证或 `project_persistence` 保存失败，原始 ErrorCode、
issues/details 均保留，并追加：

```text
details.stage
details.unregistered_build.build_id
details.unregistered_build.fmu_relative_path
```

不会自动删除、恢复、扫描或重试未登记构建包。

## CLI

```text
farcel modelica env [--omc PATH] [--json]
farcel modelica compile SOURCE_MO --model NAME --output-dir PATH [--omc PATH] [--timeout SECONDS] [--json]
farcel modelica project migrate --project-root PATH [--json]
farcel modelica project compile --project-root PATH --source PATH --model NAME [--omc PATH] [--timeout SECONDS] [--json]
farcel modelica project list --project-root PATH [--json]
```

成功时 stdout 只输出一个 JSON；`EngineError` 失败时 stderr 只输出一个错误 JSON。运行时错误
退出码为 1；argparse 参数错误或缺失子命令为 2。`env` 的 `NOT_FOUND` / `UNUSABLE` 是完成的
环境查询摘要，仍返回 1。

## 最终真实验证

本机 OpenModelica 1.26.3 已验证：安装后的 CLI 检测 OMC、编译 `FarcelFirstOrder.mo`、检查和
运行 FMU；第二次同名编译由 `MODELICA_OUTPUT_CONFLICT` 拒绝且原 SHA-256 不变。临时 Project
1.0 显式迁移到 1.1、检查备份、CLI 编译登记、重开和查询 provenance、复制 Project 后的相对
路径和散列复验均通过。T=0.5、u=1、0–2 s、步长 0.01 的 `y` 对解析解
`1 - exp(-t / 0.5)` 最大绝对误差不超过 0.03。

GitHub Windows CI 不安装 OMC，真实 OMC 测试明确跳过；普通 CLI、Project、架构边界和全量
后端测试继续在 Python 3.10/3.13 执行。

本机最终验收使用 Python 3.13、显式开启真实 OMC 集成：855 项测试通过、6 项条件跳过、0 失败。
安装后的 CLI 也实际验证了根/嵌套帮助退出码为 0，`farcel modelica` 与
`farcel modelica project` 缺失子命令退出码为 2。

## 边界

**已完成：** 上述编译、来源追溯、资产登记、CLI 和 FMI 2 CS 运行链路。

**部分完成：** 仅单文件/单模型/FMI 2 CS；可追溯不等同于跨工具链逐字节复现 FMU。

**未完成：** 多文件 Package 依赖、FMI 1/3 或 Model Exchange 编译、GUI 源码编辑器、异步编译、
构建包 show/delete/recover/rebuild、自动迁移和自动垃圾回收。

**不在 Phase 9：** 新 FMU runtime、FMPy/CVode 替换、Graph/Worker 协议改造、数据库/云服务、
PySide6 GUI、PR 或 main 合并。
