# Farcel 项目路线图

Farcel 的定位是**面向异构数字模型集成仿真的本地优先仿真平台原型**。本文件区分已实现、进行中和远期方向；未标为 Completed 的能力均不是当前产品承诺。

## 已完成基础

| 阶段 | 状态 | 范围 |
|---|---|---|
| Phase 1 | Completed | 单 FMU 基础：contracts、application/infrastructure 边界、导入、inspect、CLI 与稳定错误。 |
| Phase 2 | Completed | 单 FMU 工程化：FMI 2/3 Co-Simulation、inputs、selected outputs、采样间隔、FMI3 Event Mode/Early Return、arrays、structural parameters、RunControl/Progress/ResultChunk/CSV。 |

## 已完成：单模型 Model Exchange

**Phase 3 — Completed。** FMI2 `ModelExchangeSession`、CVode SolverAdapter、event coordinator 与 public `ModelExchangeRunner` 已完成 Reference FMU release hardening：continuous/state/time/input event、零状态、repeat、temp cleanup 与 Issue #882 均有回归保护。`run_fmu`、validation、metadata 与 CLI 都支持可执行的 FMI 2 Model Exchange；默认仍优先 Co-Simulation。FMI 3 Model Exchange 与 Scheduled Execution 仍未实现。

## 规划：本地多模型集成

**Phase 4 — Planned。** 在单模型 ME 完成后，建立 `ModelNode`、`Port`、`Connection`、`SimulationGraph`，加入 graph validation、单机 multi-FMU scheduler 与 data routing。现有单 FMU engine 将复用为 node runtime，不会被废弃。

**Phase 5 — Planned。** 仿真项目与图形化装配：项目定义、图编辑/连接、运行配置与可视化工作流。此阶段不意味着已经支持任意第三方工具的直接连接。

## 远期方向

**Phase 6 — Long-term。** 异构模型首先通过 FMU 接入；在有明确验证与维护能力时，才考虑 Simulink、AMESim、ANSYS 等 direct adapter。

**Phase 7 — Long-term。** 在本地 `SimulationGraph` 和单机调度可靠后，再评估分布式执行；当前没有 worker、RPC、云服务或分布式仿真。

**Phase 8 — Long-term optional。** 实时/HIL、ROM 和性能导向的 native worker/C++ 加速仅在相应需求与本地基线成熟后单独设计。它们目前不存在，也不由当前 Python orchestration 伪装实现。

## 目标架构（Planned）

```text
Simulation Project → Simulation Graph → Simulation Orchestrator
    → Model Node Adapters → FMU / Simulink / AMESim / ANSYS / ...
```

当前架构仍为：

```text
GUI / CLI → application → contracts ← infrastructure
```

GUI 技术路线为 Python + PySide6，结果展示可使用 PySide6 + matplotlib；application 由 Python 编排，FMPy 与 native FMU 细节隔离在 infrastructure。未来 solver adapter、native worker 或 C++ 实现只能替换/补充 infrastructure，不能使 GUI 或 public contracts 依赖 FMPy/native handle。
