# Phase 9.7A：Modelica 构建资产生命周期与安全恢复设计

## 1. 范围、结论与非目标

本文是基于当前实现的设计审计，不实现缓存、自动恢复、扫描登记、删除命令或新的公开 API。
本阶段没有修改生产代码、`farcel.contracts`、Project schema、FMU Runtime、GUI 或现有构建
语义。Phase 9.6B 的 PySide6 实际集成仍由前端负责，状态仍为未验收。

推荐路线是分步推进：先在 9.7B 提供**只读的、保守的构建包检查**；只有在确认能够可信地
恢复完整 provenance 后，才在 9.7C 加入显式登记恢复；删除和自动缓存留待 9.7D 或 Phase 10。
默认策略一律是保留文件、报告诊断，绝不推断所有权后自动删除或重新编译。

当前最重要的设计结论是：现有正式构建包只有 marker、源快照和 FMU，没有不可伪造或完整的
构建 manifest。因而它可以被定位、做路径/结构/散列检查，但在进程重启后不能无猜测地恢复
`model_name`、`compiler_name`、`compiler_version` 等完整 `ModelicaBuildProvenance`。marker 是
受控目录的**归属提示**，不是密码学证明。对历史无 manifest 构建包，自动恢复登记不是安全的
默认行为。

## 2. 当前实现审计

### 2.1 实际目录和状态

当前 `LocalModelicaAssetStore` 使用固定、项目相对的布局：

```text
<project-root>/
  .farcel-modelica-staging/
    build-<32位小写十六进制ID>/       # 未发布工作区
      .farcel-modelica-staging         # 内容等于 build_id
      source/model.mo
      compiler-output/
  modelica-builds/
    build-<32位小写十六进制ID>/       # 已发布包
      .farcel-modelica-staging         # 内容等于 build_id
      source/model.mo
      model/model.fmu
  project.json
```

构建 ID 由 UUID 生成；路径只由固定目录名和 build ID 组成。正式包发布前仅允许 marker、
`source/model.mo`、`model/model.fmu`，不会携带编译临时输出、原始 OMC 日志或用户提供的文件名。
`ModelAsset.sha256` 是 FMU 内容散列的唯一权威；`ModelicaSourceSnapshot.sha256` 标识保存的
源快照。

| 生命周期状态（设计名称，非现有公开枚举） | 物理事实 | 当前行为 |
|---|---|---|
| `STAGING` | `.farcel-modelica-staging/build-<id>` 存在 | 仅当前构建工作区；失败时只尝试清理匹配 marker 的目录。 |
| `PUBLISHED_UNREGISTERED` | `modelica-builds/build-<id>` 已 rename 发布，但 `project.json` 未引用该 ID | 发生在发布后 `pre_persist` 校验或条件保存失败；保留文件并在错误 details 返回定位信息。 |
| `REGISTERED` | 已发布包且 schema 1.1 的 `ModelAsset`/provenance 关联同一 build ID | `ProjectValidator` 校验路径、源快照 SHA-256、FMU SHA-256、关联与唯一性。 |
| `CORRUPTED` | marker、固定布局、普通文件、路径边界或可验证散列失败 | 现有代码会在创建/发布时拒绝；未来检查器应报告，绝不恢复或删除。 |
| `CONFLICTED` | build ID 已登记但候选元数据不同，或 Project revision 已变 | 当前编译以 `PROJECT_CONFLICT` / `MODELICA_OUTPUT_CONFLICT` 拒绝；未来恢复应保持幂等而不覆盖。 |
| `UNKNOWN` | 目录不在受控位置、缺 marker、marker 不匹配、含未知内容或无法证明元数据 | 当前清理器保留；未来只读检查报告，绝不接管。 |

这些名字仅用于本文和未来测试设计；本阶段不新增 enum、DTO 或 ErrorCode。

### 2.2 当前真实执行流程

下列流程由 `ProjectModelicaService.compile_and_register()`、
`ModelicaAssetStagingService.stage()`、`LocalModelicaAssetStore` 和
`LocalJsonProjectRepository` 的实际代码确认：

```text
用户选择 .mo / 类名 / Project
  → load_with_revision(project.json) 取得 Project + 原始文档 SHA-256
  → schema 必须为 1.1，调用者 Project 必须等于最新 revision
  → ProjectValidator 预编译校验
  → 创建受控 STAGING 工作区和匹配 marker
  → 原文件 SHA-256 → 排他复制 → 快照 SHA-256 → 原文件再次 SHA-256
  → 校验快照，调用 OpenModelica（输入只能是该工作区 source/model.mo）
  → 再校验快照、编译结果 FMI/类型/编译器版本/sha256 格式
  → 检查编译 FMU 位于 compiler-output 内，复制后两次 SHA-256 校验
  → 删除本工作区 compiler-output，白名单校验正式布局
  → os.rename 发布至 modelica-builds/build-<id>
  → ProjectAssetValidator 复核已发布 FMU 路径、范围和 SHA-256
  → 构造 ModelAsset + ModelicaBuildProvenance，追加到内存 Project
  → ProjectValidator pre_persist 校验
  → save_if_unchanged：临时文件 fsync、最终文档 SHA-256 比对、os.replace(project.json)
  → 返回登记结果
```

发布前失败（请求/Project 预校验、快照、编译、FMU 散列、临时输出清理、布局、rename）不会
登记 Project；服务只会尽力清理当前 marker 所属的**未发布**工作区。发布后立即的资产复核失败
会尝试删除本次刚发布且 marker 匹配的包。发布成功后发生的 `pre_persist` 校验失败、
`PROJECT_CONFLICT`、`PROJECT_IO_ERROR` 或条件保存失败则不删除正式包，错误保留原 code/details
并添加：

```text
details.stage = "pre_persist" 或 "project_persistence"
details.unregistered_build.build_id
details.unregistered_build.fmu_relative_path
```

这正是 `PUBLISHED_UNREGISTERED` 的来源；它不是运行时或 FMU 编译失败。

### 2.3 已有保护与尚未保证的事项

**已实现：** 受控根目录解析；创建时拒绝符号链接目录和源文件；build ID 格式；源复制期间
前后散列一致性；FMU 必须在当前工作区输出目录；发布前后 SHA-256；固定包白名单；同名目标
绝不覆盖；Project 相对路径及资产 SHA-256；schema 1.1 provenance/asset 关联；条件保存；迁移
备份；失败后未知目录默认保留。

**未实现或不能声称：** 跨进程文件锁、跨文件系统/多文件事务、对发布后所有目录项的持续监控、
可信构建 manifest、已登记引用感知的删除、未登记包扫描/恢复、自动垃圾回收、缓存命中、
用户取消后的恢复队列，或“marker 即可证明文件从未被篡改”。`discard_published_build()` 本身
仅检查路径、build ID 和 marker；它不读取 Project 来阻止删除已登记包。当前调用点只在登记前的
发布后资产复核失败窗口使用它，未来绝不能把该内部 primitive 直接暴露为删除 API。

## 3. 未登记构建包的候选恢复设计

### 3.1 只读检查与状态判定

未来 9.7B 应从 `project_root` 和严格的 32 位 `build_id` 推导唯一候选路径
`modelica-builds/build-<id>`，而不是扫描任意目录。检查器必须不调用 OMC、不写文件、不清理，
并按顺序验证：

1. 解析 `project_root`，拒绝不存在、不可解析、越界、符号链接/junction 或非目录根。
2. 构造固定相对路径，拒绝输入路径片段、绝对路径、`..` 和不匹配的 build ID。
3. 用不跟随链接的方式检查构建目录、marker、`source`、`model`、`model.mo`、`model.fmu`；任何
   symlink/junction、额外项、缺项或 marker 内容不等于 build ID 均为 `UNKNOWN` 或 `CORRUPTED`。
4. 重新计算 `source/model.mo` 和 `model/model.fmu` SHA-256；对已登记包分别与 persisted
   provenance / `ModelAsset.sha256` 比较，并检查 `fmu_asset_id`、固定相对路径和 build ID 唯一性。
5. 对 FMU 做结构检查（通过既有公开 importer/能力校验边界，而非自行解析 zip）；检查失败即
   `CORRUPTED`，不进行恢复。
6. 读取最新 Project revision，确认该 build ID 是否在 provenance 中、同一 asset ID/路径是否已
   被占用，以及是否有同路径或同 asset ID 的歧义。

只要一项不能证明，即返回诊断状态并保留文件。`REGISTERED` 是 Project 记录和文件两者一致；
仅 marker+布局存在且未登记不能自动升级为 `PUBLISHED_UNREGISTERED` 的“可恢复”结论。

### 3.2 为什么历史包不能自动恢复完整登记

当前正式布局没有保存模型类名、编译器名称/版本、FMI/类型或发布时的可信源/FMU散列 manifest。
从 FMU 可获部分 FMI 信息，但不能可靠恢复编译器版本；从 `.mo` 文本推测类名既不等价于 OMC
输入，也不应由 GUI/恢复逻辑重新实现 Modelica 解析。因此，历史包在重启后只能安全归类为：

* 已登记且可验证；
* 未登记但只有候选 marker/布局；或
* 已损坏/未知。

它们不应无 OMC、无额外受信输入地自动写回一条臆造 provenance。唯一例外是同一调用链仍持有
已验证的 `ModelicaStagedBuild`，但当前公共错误 details 没有携带完整 provenance，且失败后的
新进程不能依赖内存对象。

若将来需要安全恢复，候选方案是引入版本化、包内的不可变 manifest（包含完整 provenance、
源/FMU SHA-256、包格式版本及必要的编译形状），在发布前写入并纳入白名单校验。它可保持
schema 1.1 不变，但会改变构建包布局，必须单独迁移/兼容设计；旧包仍只能检查或由用户选择
重新编译，不能伪装成可自动恢复。

### 3.3 9.7C 的显式恢复算法（仅设计）

对 manifest-bearing 包，未来恢复操作应当：

1. 执行上述完整只读检查，并要求状态为 `PUBLISHED_UNREGISTERED`。
2. 重新计算源/FMU SHA-256；对比 manifest；通过 importer 检查 FMU 结构；验证 marker 和固定布局。
3. `load_with_revision()` 最新 Project；若同 build ID 已正确登记，返回幂等成功且不写文件。
4. 若 build ID、asset ID 或 relative path 被不同记录占用，返回 `PROJECT_CONFLICT`，不覆盖、不删除。
5. 用 manifest 构造 `ModelAsset` / provenance，追加到最新 Project，运行既有 `ProjectValidator`。
6. 以读取 revision 的 SHA-256 调用 `save_if_unchanged()`。保存失败时保留构建包与 manifest，
   返回原错误和 build ID；再次调用仍可检查/恢复。
7. 成功后重新读取或复验 Project，使 `REGISTERED` 成为文件和 JSON 都一致的结论。

该流程不重新调用 OMC；恢复只是把已验证、未登记包显式写入 Project。恢复期间不删除任何文件。
重复请求在完全相同登记已存在时无副作用，在不同记录存在时冲突，满足幂等和不覆盖原则。

## 4. 清理策略和安全门禁

### 4.1 当前受控清理

`discard_workspace()` 和 `discard_published_build()` 是 infrastructure 内部 store 操作，不是
Engine/CLI/GUI 公共能力。它们只尝试删除当前项目根内、非链接、marker 内容匹配 build ID 的
目录；marker 不匹配、目录不存在、路径越界或疑似链接时保留。`compiler-output/` 的自动删除
也只发生在已验证工作区、固定名称、解析后仍位于 workspace 内的目录。

### 4.2 将来删除的强制前置条件

任何未来显式删除都必须同时满足下列条件；任一不满足即只报告、保留：

* 用户在界面/API 中针对确切 `build_id` 明确确认，且请求包含刚刚检查得到的不可变摘要；
* 最新 Project revision 已读取；构建包处于经完整检查确认的 `PUBLISHED_UNREGISTERED`；
* marker、固定布局、路径边界、无 symlink/junction、所有文件普通且包格式受支持；
* build ID、asset ID、FMU relative path 均未在最新 Project provenance/model assets 中出现；
* Project 的 Case、Graph、run history 及其他已知资产引用没有引用该路径；若无法证明，拒绝删除；
* 没有活动构建/恢复操作持有同一 build ID；删除前再次验证目录身份和摘要；
* Windows 占用、权限、rename 或删除失败仅返回诊断，绝不递归扩大目标或尝试删除未知父目录；
* 用户取消、并发 revision 变化或检查与删除之间的身份变化均中止并保留。

不允许清理：已登记 ModelAsset、任意 Case/Graph/run 可能引用的资产、marker 不匹配目录、链接或
越界路径、当前工作区、无法证明所有权的文件、用户放入 Project 的文件，以及历史无 manifest
的候选包。删除应是最后的独立命令，不应被恢复、打开 Project、编译或前端刷新隐式触发。

### 4.3 文件系统风险

Windows 的文件占用可使 `os.rename`、`os.replace`、`rmtree` 失败；应保持目录而不是重试删除。
目录检查到删除之间存在 TOCTOU，junction/symlink 可在期间替换；未来实现须在同一受控根内使用
最小范围操作、重复 `lstat`/解析检查，并优先采用可验证 rename 到受控隔离区后再删除的两阶段
设计。即使如此也不是防恶意本地管理员的安全边界。绝不能在用户提供路径、通配符或未经解析的
父目录上递归删除。

## 5. 编译缓存与可复现性

### 5.1 追溯不等于缓存或字节复现

现有 provenance 提供源快照 SHA-256、模型名、编译器名称/版本、FMI 版本、FMU 类型、build ID
和关联资产的 FMU SHA-256。这足以说明登记资产来源并做移动后完整性验证；不表示下一次编译会
得到逐字节相同 FMU，也不表示两个同源 FMU 可以互换。

自动缓存的严格键至少还需要：完整源及所有依赖内容、模型类名、FMI/类型、编译器产品和精确
版本、操作系统/架构、全部编译配置和环境、外部工具链/库版本、影响 OMC 的 flags、资源文件和
生成策略。当前 provenance 缺少平台/架构、显式编译器路径外的工具链身份、完整 flags/环境、
依赖闭包、外部库/资源散列、包格式版本和可复用性声明。单独用 source SHA-256 会造成错误命中。

| 方案 | 收益 | 风险与复杂度 | schema 1.1 / 可移动性 | 结论 |
|---|---|---|---|---|
| 1. 每次显式编译，无缓存 | 最小、语义清晰 | 无误命中；仍保留未登记包 | 完全兼容 | 当前推荐。 |
| 2. 历史构建查询与用户手动复用 | 用户可查看已登记来源，避免隐式复用 | 需要只读检查和明确恢复语义，但无缓存键 | 可用相对路径；无 schema 变更即可先做 | 9.7B/9.7C 候选。 |
| 3. 严格键自动缓存 | 可能减少真实 OMC 编译 | 需要完整输入指纹、完整性验证、并发/失效策略，错误命中会产生错误 FMU | 可能需要 schema/manifest 演进 | 延后，至少 Phase 10。 |

不推荐“source SHA 相同即复用”、将 `build_id` 当缓存键、或把当前公开 provenance 误写为完整可
复现构建声明。推荐先交付历史查询/显式复用，收集真实用户收益后再评估自动缓存。

## 6. Project 并发和事务边界

当前登记先 `load_with_revision()` 获得原 `project.json` 字节 SHA-256，再进行可能很久的 OMC
编译；发布后，`save_if_unchanged()` 写同目录临时文件并 fsync，重新读文档比对 SHA-256，随后
`os.replace()`。这保证陈旧调用者和通常的并发修改不会静默覆盖，并让保存失败后仍留有可诊断包。

但最后散列比对与 `os.replace()` 之间存在 TOCTOU；没有跨进程锁、文件事务、目录事务或数据库。
发布 build 目录和保存 JSON 也不是一个原子组合：前者先完成，后者失败正是未登记包的来源。多
进程同时恢复同一 build ID 或同时删除同一包，在没有额外协调时也不能安全保证唯一结果。

| 方案 | 评价 |
|---|---|
| 保持乐观条件保存并保留未登记包 | 当前已实现，最小且数据保守；推荐作为恢复的基础。 |
| 恢复/删除增加项目内受控锁 | 可降低同一 build 的竞争，但要处理 Windows 锁遗留、崩溃与网络文件系统；仅在 9.7C/D 有明确操作时评估。 |
| 操作日志/事务记录 | 有助恢复复杂多文件操作，但需要新格式和故障模型；当前收益不足。 |
| 数据库/全局事务 | 超出范围，不推荐。 |

未来恢复仍必须使用 revision 条件保存；若 Project 在读取后变化，返回 `PROJECT_CONFLICT` 并保留
包。删除不应通过修改 Project 来“证明”安全性；先读取、再最终复检，任一竞争不确定时保持文件。

## 7. 候选公共 API 与分层（仅设计）

前端不得扫描 `modelica-builds/`。未来 API 应保持“只读检查”和“有副作用操作”分离，并全部通过
Backend facade：

| 候选能力 | I/O / OMC / 副作用 | contracts 与兼容性建议 |
|---|---|---|
| `inspect_project_modelica_build(project_root, build_id)` | 读文件/Project；不 OMC、不写 | 可在 9.7B 新增 Farcel-owned inspection DTO；不改变既有签名或 schema。 |
| `list_project_modelica_build_packages(project_root)` | 扫描受控根后只读检查；不 OMC、不写 | 仅列受控候选及诊断，未知目录不接管；可能成本较高。 |
| `recover_project_modelica_build(project, build_id, ...)` | 读、条件保存 Project；不 OMC | 仅 manifest-bearing、完整验证包；需要新明确 request/result DTO 和用户确认。 |
| `discard_unregistered_modelica_build(project_root, build_id, ...)` | 读、最终复检、删除；不 OMC | 只在 9.7D 讨论；需确认 token/摘要及严格锁/引用检查。 |

`contracts` 只声明 Farcel-owned DTO/ports；`application` 编排检查、状态分类、Project revision 和
幂等登记；`infrastructure/project` 实现安全文件枚举、marker/布局、散列与原子持久化；FMPy/OMC
仍留在 infrastructure adapter。优先复用 `ModelAsset`、`ModelicaBuildProvenance`、
`ProjectRevision`、`ValidationReport` 和现有 ErrorCode；仅当诊断无法由 issue code 表达时才在
后续阶段评估新的 ErrorCode。新 API 均为加法，不破坏旧前端和 schema 1.1。

## 8. 验证证据与未来测试矩阵

本阶段复用而不复制现有测试：

| 行为 | 现有证据 |
|---|---|
| 快照、发布白名单、FMU散列、目标不覆盖 | `tests.unit.test_modelica_asset_store.LocalModelicaAssetStoreTests.test_snapshots_source_and_publishes_verified_fmu_as_one_build_directory`、`test_existing_formal_build_directory_is_never_overwritten`。 |
| 当前工作区失败清理只限受控资源，未知目录保留 | `test_cleanup_only_removes_marker_owned_workspace`、`test_compiler_output_cleanup_failure_prevents_publication_and_preserves_other_builds`。 |
| 源/FMU失败不会发布完成包 | `tests.application.test_modelica_asset_staging.ModelicaAssetStagingServiceTests.test_compiler_failure_or_bad_fmu_never_publishes_a_completed_build`。 |
| 发布后登记失败保留 build ID 与路径诊断 | `tests.application.test_project_modelica_service.ProjectModelicaServiceTests.test_persistence_failure_keeps_build_identification_in_error_details`、`test_post_staging_validation_failure_preserves_issues_and_build_identification`、`test_project_io_persistence_failure_keeps_build_identification`。 |
| schema 1.0/1.1、provenance、源/FMU散列、移动性 | `tests.unit.test_project_schema_1_1.ProjectSchema11Tests.test_project_validator_checks_provenance_files_association_and_hashes`、`test_validator_rejects_source_escape_missing_asset_mismatch_and_relocation`。 |
| 条件保存和 Project 冲突 | `test_conditional_save_rejects_stale_revision_without_overwriting`。 |
| 真实 Project 编译、登记、移动和散列 | opt-in `tests.integration.test_modelica_compile_public_api.ModelicaCompilePublicApiRealIntegrationTests.test_project_compile_register_reopen_run_and_relocate`。 |

9.7B 应新增纯临时目录/Fake 资产的只读检查测试：合法已登记、合法未登记候选、marker 不匹配、额外
文件、缺源/缺 FMU、散列失配、源或 FMU symlink/junction、路径逃逸、重复 build ID、Project 变更
期间重新检查和 schema 1.0/1.1。不得调用删除 primitive。9.7C 才测试 manifest 完整性、同一
build 的幂等恢复、资产/路径冲突、条件保存失败保留包、重复恢复竞争。9.7D 若获批准，才在测试
专用临时目录模拟 Windows 占用、取消、锁、rename 竞争和显式删除；绝不触及真实用户文件。

## 9. Phase 9.7B/9.7C/9.7D 建议

**9.7B（推荐下一个最小范围）：** 只读 `inspect`（可选受控 `list`）、所有权候选验证、固定布局
与完整性验证、已登记/未登记/未知状态识别和完整诊断。不得写 Project、调用 OMC、恢复、缓存或删除。

**9.7C（须在 9.7B 验收后）：** 只为未来 manifest-bearing 的完整验证包提供显式、确认后的恢复
登记；条件保存、幂等、不覆盖、失败后仍可恢复。历史无 manifest 包保持只读诊断，必要时用户
重新编译。

**9.7D（可延期）：** 仅在实际收益和强测试成立时评估受控清理；自动缓存也不应与删除捆绑。严格
缓存键、工具链依赖闭包、缓存失效和跨平台复用建议延至 Phase 10。

## 10. 推荐与不推荐方案

推荐：保持现有显式编译和乐观条件保存，先实现无副作用检查，再为未来包设计可验证 manifest 后
再考虑恢复。它不会误删、不会捏造 provenance，不改变 schema 1.1 或旧 API，也给前端稳定的
只读诊断边界。

不推荐：自动扫描后登记、marker 即恢复、marker 即删除、source SHA 作为缓存键、或为理论事务
引入数据库/全局锁。前四者会把未知/可篡改文件误当 Farcel 资产，最后者在当前单机范围内成本高于
收益。

本文所有恢复、manifest、检查 DTO、锁、删除和缓存均为设计候选，尚未实现。
