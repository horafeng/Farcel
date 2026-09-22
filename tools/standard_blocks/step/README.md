# Step FMI 2.0 Co-Simulation FMU

此目录包含 Farcel 自有的 Step 标准模块。它只支持 scalar Real 参数
`initial_value`、`final_value`、`step_time` 和输出 `y`；当 `t < step_time` 时输出
初始值，否则输出终值。

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File tools/standard_blocks/step/build.ps1
```

脚本使用 PATH 中的 GCC 或 `FARCEL_GCC`，生成 package asset 并把真实 SHA-256
写入对应 `block.json`。此源代码由 Farcel 维护，不复制第三方 FMU。
