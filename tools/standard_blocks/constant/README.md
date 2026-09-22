# Constant FMI 2.0 Co-Simulation FMU

此目录包含 Farcel 自有的 Constant 标准模块源代码。它只实现 scalar Real 参数
`value` 与输出 `y = value`，并且只导出 FMI 2.0 Co-Simulation 接口。

在 Windows 上运行以下命令构建 package asset：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File tools/standard_blocks/constant/build.ps1
```

脚本需要 GCC（PATH 中的 `gcc` 或 `FARCEL_GCC`），生成
`src/farcel/standard_library/assets/sources/constant/Constant.fmu`，并把真实
SHA-256 写入对应 `block.json`。源代码由 Farcel 维护，不依赖或复制第三方 FMU。
