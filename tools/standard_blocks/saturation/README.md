# Saturation FMI 2.0 Co-Simulation FMU

此目录包含 Farcel 自有的 Saturation 标准模块源码。它只实现 scalar Real 参数
`lower_limit`、`upper_limit`、输入 `u` 和输出
`y = min(max(u, lower_limit), upper_limit)`，并且只导出 FMI 2.0 Co-Simulation
接口。

在 Windows 上运行以下命令构建 package asset：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File tools/standard_blocks/saturation/build.ps1
```

脚本需要 GCC（PATH 中的 `gcc` 或 `FARCEL_GCC`），生成
`src/farcel/standard_library/assets/math/saturation/Saturation.fmu`，并将真实
SHA-256 写入对应 `block.json`。源码由 Farcel 维护，不依赖或复制第三方 FMU。
