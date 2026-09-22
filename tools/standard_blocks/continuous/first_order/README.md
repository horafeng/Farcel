# First Order FMI 2.0 Co-Simulation FMU

此目录包含 Farcel 自有的一阶连续系统标准模块源码。它只实现 scalar Real 参数
`gain`、`time_constant`、`initial_value`、输入 `u` 和输出 `y`，满足：

```text
time_constant * dy/dt + y = gain * u
```

FMI 2 使用进入与退出初始化模式代替 `fmi2Initialize`；模块在
`fmi2ExitInitializationMode` 将状态 `y` 设置为已配置的 `initial_value`。
随后每个 `fmi2DoStep` 以常输入的精确一阶响应推进状态。

在 Windows 上运行以下命令构建 package asset：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File tools/standard_blocks/continuous/first_order/build.ps1
```

脚本需要 GCC（PATH 中的 `gcc` 或 `FARCEL_GCC`），生成
`src/farcel/standard_library/assets/continuous/first_order/FirstOrder.fmu`，并将
真实 SHA-256 写入同目录 `block.json`。源码由 Farcel 维护，不依赖或复制第三方
FMU。
