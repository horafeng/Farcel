# Phase 8.2 — 标准模块 Catalog

## 职责

`StandardBlockCatalog` 是 application 层的内存查询能力。它注册并查询已有的
Farcel-owned `BlockCategoryDescriptor` 与 `BlockDescriptor`，使后续 application
use case 可以获得当前标准模块、模块分类和完整 descriptor metadata。它不定义新
contract，也不修改标准模块的执行路径。

catalog 提供 `register_category()`、`register_block()`、`list_categories()`、
`list_blocks()` 和 `get_block()`。category ID 与 block ID 只能注册一次；block 的
`category_id` 必须已经注册。列表 API 返回 immutable tuple；查询不存在的 block
会报告明确的 `ValueError`。

## 与 contracts 的关系

descriptor 是稳定、implementation-independent 的 contracts；catalog 是持有并
查询那些 contracts 的 application capability。因此 catalog 不放入
`farcel.contracts`，且 contracts 不依赖 catalog。它也不包含 GUI layout、canvas
position、widget、PySide6 object 或 GUI state：前端未来只能消费 descriptor 的
中文 display metadata，并自行决定如何呈现。

## 不是 FMU loader

本阶段的 catalog 是纯内存 registry。它不创建或检查 FMU、不读取 package
resource、不进行 SHA-256 验证、不比较 FMU metadata，也不触碰 `ModelNode`、
`SimulationGraph`、Graph runtime、Worker 或 Project schema。`fmu_asset` 在
descriptor 中只是被保存的 metadata，catalog 不解析它。

## 后续接入

Phase 8.0 冻结的未来 `catalog.json` 将定义分类和模块目录；每个未来
`block.json` 将映射为一个 `BlockDescriptor`。后续 loader 可从 package resource
读取 JSON，构造已有 contracts，再以 `register_category()` 和 `register_block()`
填充此 catalog。真实 FMU asset 的 package resolution 和 metadata consistency
validation 分别属于后续资产与集成阶段，而非 Phase 8.2。
