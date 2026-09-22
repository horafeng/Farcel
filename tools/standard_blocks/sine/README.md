# Sine FMI 2.0 Co-Simulation FMU

此目录包含 Farcel 自有的 Sine 标准模块。它只支持 scalar Real 参数 `amplitude`、
`frequency_hz`、`phase_rad`、`offset` 和输出 `y`，并计算正弦信号。

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File tools/standard_blocks/sine/build.ps1
```

脚本使用 PATH 中的 GCC 或 `FARCEL_GCC`，生成 package asset 并把真实 SHA-256
写入对应 `block.json`。此源代码由 Farcel 维护，不复制第三方 FMU。
