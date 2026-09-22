# 闭环控制标准模块示例

本示例验证 Farcel 已有 Standard Block Library 可以不新增 API、不修改 runtime 地
构造并运行一个负反馈闭环。入口为：

```powershell
python examples/closed_loop_control_demo.py
```

## 拓扑

```text
        +----------------+
        |                |
        v                |
Step → Sum → PID → FirstOrder
          ^                 |
          |                 |
          +── Gain(-1) ─────+
```

模块参数如下：

- Step：初始值 `0`、终值 `1`、阶跃时间 `0`。
- PID：`Kp=2`、`Ki=1`、`Kd=0`。
- FirstOrder：增益 `1`、时间常数 `1`、初始值 `0`。
- Gain：增益 `-1`，用于负反馈。

示例通过现有 `StandardBlockCatalog`、`StandardBlockFactory`、`SimulationGraph`
和 `run_graph()` 创建和运行。这是一个目标值为 `1` 的负反馈 PI 控制示例：
FirstOrder 的 `y` 是记录的输出信号，运行后应逐渐接近目标值。命令行会显示最终输出
和最终跟踪误差；其他需要连接路由的输出仍由既有 Graph runtime 自动读取。

## Jacobi 语义

当前 Graph runtime 使用 explicit-Jacobi checkpoint routing：一个连接在当前
checkpoint 使用上一个 checkpoint 的 source snapshot。因此闭环传播存在一个或多
个 checkpoint 延迟。这是既有 Graph 语义，不是 Standard Block 或此示例的缺陷；
结果验证检查有限数值、对目标值的响应与误差收敛，但不假定精确控制曲线，也不应把
该传播延迟误认为模块错误。
