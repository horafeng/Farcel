# StateSpace v0.0.41 补丁说明

本目录只保存 Farcel 回归 fixture 所需的最小上游修复，不包含完整的
Reference-FMUs 源码树。

- 上游项目：Modelica Association Reference-FMUs
- 上游标签：`v0.0.41`
- 上游源码提交：`1258711a41e28b1c25e058abd04f6214beefa3cb`
- 许可证：BSD-2-Clause
- 原始源码：`StateSpace/model.c`
- 补丁：`StateSpace-v0.0.41-setUInt64.patch`
- 构建方式：官方 CMake 流程，配置 `-DFMI_VERSION=3` 后执行 `--target StateSpace --config Release`
- 生成文件 SHA-256：`26818B9F3386DE3EF63436BF3C122264E32A4806A605216F6A269EAD3BCD2F18`

官方 v0.0.41 的 `setUInt64()` 在消耗 `values[(*index)++]` 后才执行
`ASSERT_NVALUES(1)`。该顺序使单个 scalar Structural Parameter 的标准 FMI 3
setter 调用同时无法满足内部的 `nValues` 和最终 index 校验。补丁仅将该检查
移至消耗 value 之前，并删除三个重复的错误位置检查；不改变 `m`、`n`、`r`
的上限、StateSpace 方程、模型描述、FMI capability 或其他模型逻辑。

基于此补丁构建的 `StateSpace-fmi3-patched.fmu` 仅用于 Farcel 的 FMI 3
Configuration Mode、Structural Parameter 与 Dynamic Shape 正向回归。它不是
Modelica Association 发布的官方原始二进制。
