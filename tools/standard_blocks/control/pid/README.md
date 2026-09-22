# PID FMI 2.0 Co-Simulation FMU

此目录包含 Farcel 自有的 PID 控制器标准模块源码。它只实现 scalar Real 参数
`Kp`、`Ki`、`Kd`、`initial_integral`、`initial_error`、输入 `u` 和输出 `y`。

FMI 2 使用进入与退出初始化模式代替 `fmi2Initialize`；模块在
`fmi2ExitInitializationMode` 初始化积分状态和上一误差。每个正的
`fmi2DoStep` 依次更新积分项、差分项、输出以及上一误差：

```text
integral += error * step
derivative = (error - previous_error) / step
y = Kp * error + Ki * integral + Kd * derivative
```

在 Windows 上运行以下命令构建 package asset：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File tools/standard_blocks/control/pid/build.ps1
```

脚本需要 GCC（PATH 中的 `gcc` 或 `FARCEL_GCC`），生成
`src/farcel/standard_library/assets/control/pid/PID.fmu`，并将真实 SHA-256 写入
同目录 `block.json`。源码由 Farcel 维护，不依赖或复制第三方 FMU。
