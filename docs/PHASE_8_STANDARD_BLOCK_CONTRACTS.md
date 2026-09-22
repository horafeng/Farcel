# Phase 8.1 — 标准模块 Contracts

## 范围

Phase 8.1 将 Phase 8.0 已冻结的标准模块 descriptor 概念实现为 Farcel-owned
public contracts。新增的 `BlockCategoryDescriptor`、`BlockDescriptor`、
`BlockParameterDescriptor`、`BlockPortDescriptor`、`BlockValueType` 和
`BlockPortDirection` 位于 `farcel.contracts`，并由该包的 public surface 导出。

它们是纯不可变数据模型：前端、CLI 和 application 可以读取它们，未来
infrastructure 可以实现它们所描述的 FMU，但没有任一方向依赖具体运行时。这样
继续保持：

```text
GUI / CLI -> application -> contracts <- infrastructure
```

因此 contracts 不导入 FMPy、PySide6、pathlib resource loader、Worker RPC、socket
或 native FMU object。

## 与 Phase 8.0 schema 的关系

`BlockDescriptor` 的字段直接对应 Phase 8.0 冻结的 future `block.json` 根字段：
`schema_version`、`block_id`、`block_version`、用户可见名称和描述、分类、FMU
asset/SHA-256、`execution_interface`、parameters、input_ports、output_ports。
`schema_version` 默认 `"1.0"`；`execution_interface` 复用现有 Farcel-owned
`InterfaceType`，不暴露 FMI/FMPy 类型。

未来 catalog loader 只需将 JSON 的 parameter 条目映射为
`BlockParameterDescriptor`、端口条目映射为 `BlockPortDescriptor`、分类条目映射为
`BlockCategoryDescriptor`，再组装 `BlockDescriptor`。本阶段不加载 JSON，不解析
资源，也不验证 asset 或真实 FMU metadata。

`variable_name` 是 FMI-facing machine identifier；`display_name` 与
`description` 保存简体中文用户 metadata。`BlockValueType` v1 仅定义 scalar
`REAL`，且不绑定 FMI 数据类型；未来可按需要增加 Integer、Boolean 或 String。

## 已实现的基础校验

- `schema_version`、`block_id`、`block_version` 与所有 parameter/port
  `variable_name` 必须非空；
- parameter 的 `minimum` 与 `maximum` 同时存在时满足 `minimum <= maximum`；
- 一个 block 内 parameter `variable_name`、parameter `display_order` 与所有端口的
  `variable_name` 均不能重复；
- `input_ports` 必须是 `INPUT`，`output_ports` 必须是 `OUTPUT`；以及
- descriptor 的 parameter 与 port collections 必须是 tuple，保持外层 immutable
  contract 形态。

这些是 descriptor 自身可知的纯数据约束。descriptor ↔ FMU metadata compare、
block.json/catalog loading、package asset resolution、asset SHA 检查、Graph runtime
integration 和任何模块数值代码均不属于 Phase 8.1。

## 刻意不包含的内容

contracts 不包含 GUI 坐标、canvas/port position、widget type、PySide6 object、
runtime callback、FMU handle、资源路径解析器、Worker RPC 或 socket。它们也没有
改变 `ModelNode`、`SimulationGraph`、Worker protocol、Project schema 或
`pyproject.toml`。这些边界确保未来标准模块仍转换为既有 `ModelNode` 和 FMU
execution path，而不是生成第二套运行时。
