# Phase 8.3 — 标准模块 Asset 与 Resource Layer

**历史阅读说明。** 本文记录 Phase 8.3 当时的最小 metadata resource 基线；其中
“未来阶段”及“尚未创建 FMU”的表述只描述该阶段，不代表当前标准模块库状态。当前
asset 库存以 package 内 `standard_library/assets/catalog.json` 及其声明的
`block.json` 为准。

## 资源布局

Phase 8.3 将少量标准模块 metadata 放入 Farcel package：

```text
src/farcel/standard_library/
    __init__.py
    assets/
        catalog.json
        categories/math/gain/block.json
```

`catalog.json` 声明分类与 block metadata resource 路径；`block.json` 只描述一个
测试用的“增益”模块。它们是 package data，wheel 明确包含
`src/farcel/standard_library/assets/**/*.json`，并由 application regression test
通过 Python package resource mechanism 读取。资源定位不依赖当前工作目录，也不
硬编码开发机的绝对路径。

## JSON 与 contracts 映射

catalog 的 category object 映射为 `BlockCategoryDescriptor`；`block.json` 的
schema v1 字段映射为 `BlockDescriptor`，并将 parameters 和 ports 分别映射为
`BlockParameterDescriptor` 与 `BlockPortDescriptor`。`execution_interface`、
`value_type` 和 `direction` 被转换为既有 Farcel enum。loader 对 schema version、
必填字段、JSON field 类型和未知 metadata resource path 提供明确校验错误。

`fmu_asset` 与 `fmu_sha256` 是与 Phase 8.1 contract 对齐的 descriptor metadata。
它们在本阶段既不被解析，也不被验证；`Gain.fmu` 并不存在，且没有创建任何 FMU。

## application 层职责

`StandardBlockCatalogLoader` 位于 application，因为它将 package metadata 转换为
Farcel contracts，再使用 `StandardBlockCatalog` 组装查询能力。它不是 GUI 数据：
没有 canvas 坐标、widget、PySide6 object 或呈现状态。它也不是 infrastructure：
不创建 FMU runtime、不调用 FMPy、不读取 native FMU metadata，且不改变 Graph
runtime、Worker 或 Project。

未来阶段可在不改变 loader 到 catalog 的方向下扩展 `catalog.json` 和新增的
`block.json`。随后才分别增加真实 FMU package asset、FMU metadata consistency
validation，以及 Block → ModelNode integration。
