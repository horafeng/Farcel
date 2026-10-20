# Phase 9.1D — Modelica 公共编译 API

## 目标与公共接口

Phase 9.1D 将已验收的 `OpenModelicaCompiler` 接入 Farcel 的 application facade，但没有
重新实现编译器，也没有新增 CLI、GUI 或异步运行时。公开调用为：

```python
backend = create_backend()
result = backend.compile_modelica(request)
```

`request` 是既有 `ModelicaCompileRequest`，返回既有 `ModelicaCompileResult`。调用方必须
预先创建 `output_directory`。编译完成不会自动仿真；生成的 FMU 仍使用既有
`load_fmu()`、`validate_config()`、`run_fmu()` 和 `export_result()`。

## 装配与边界

依赖方向保持：

```text
GUI / CLI -> FarcelEngine -> ModelicaService -> ModelicaCompiler Port
                                                    ^
                                      OpenModelicaCompiler (infrastructure)
```

`ModelicaService` 仍保留环境检测，并新增 `compile_modelica(request)`：先调用 application
的请求验证，再调用 `ModelicaCompiler` Port。`FarcelEngine.compile_modelica(request)` 只做
Service 委托，`SimulationEngine` Protocol 同步声明该方法。`create_backend()` 装配
`OpenModelicaEnvironmentDetector` 和 `OpenModelicaCompiler`，但不会在构造 backend 时
启动 OMC、创建文件或要求 OMC 已安装。

保留 `ModelicaService(detector)` 的兼容构造方式；若未注入 compiler，编译返回稳定
`NOT_IMPLEMENTED`，而不是 `AttributeError`。Application 不导入具体 OpenModelica 类型；
infrastructure 也不反向导入 application。

## 错误行为

无效请求由 application 返回带字段级 issues 的 `CONFIG_ERROR`。适配器产生的
`MODELICA_COMPILER_NOT_FOUND`、`MODELICA_COMPILER_UNUSABLE`、
`MODELICA_COMPILATION_ERROR`、`MODELICA_FMU_INVALID`、
`MODELICA_OUTPUT_CONFLICT`、`MODELICA_OUTPUT_ERROR` 和 `TIMEOUT` 原样保留为
`EngineError`。application 不解析 OMC 日志，也不接触 subprocess。

同名产物的第二次编译仍由 adapter 的排他发布拒绝，第一次已验证 FMU 不会被覆盖。

## 前端职责

前端仅导入 `farcel` 与 `farcel.contracts`，先调用 `get_modelica_environment()`，然后在
自己的工作线程中调用同步 `compile_modelica()`。前端不得导入 infrastructure、调用
`omc.exe`、生成 `.mos`、解析 OMC 输出或阻塞 GUI 主线程。

## 验证范围与限制

普通公共 API 测试不依赖 OMC，覆盖无效请求、无效显式 compiler 路径、默认 backend
创建以及既有 FMU 加载。真实测试以环境变量
`FARCEL_RUN_OPENMODELICA_INTEGRATION=1` 启用，使用 OpenModelica 1.26.3 编译
`FarcelFirstOrder.mo`，检查 SHA-256、Farcel 加载、`T=0.5` 数值误差、CSV 导出与同名
产物冲突保护。没有 OMC 的 CI 明确跳过真实测试。

当前范围仍只支持单一 `.mo`、一个普通或点分模型名、FMI 2.0 Co-Simulation 和同步本机
编译。没有 Project schema、资产关联、编译缓存、CLI 编译命令、GUI 或异步 API。
Phase 9.2 才处理已验证 FMU 与 Project 资产关联及持久化规则。

## 本机验证记录（2026-10-20）

在 Windows 主机以 `D:\OpenModelica\bin\omc.exe`（OpenModelica 1.26.3）通过默认
`create_backend()` 运行真实公共 API：`FarcelFirstOrder.mo` 生成
`FarcelFirstOrder.fmu`，一次运行的 SHA-256 为
`f87567f5810d172bf4b848c92533703298058ac50c7fc535897da2ce6706048e`。Farcel 成功加载、
验证并以 `T=0.5`、`u=1` 从 0 到 2 秒运行；相对解析解的最大绝对误差为
`0.00370976108433`，CSV 导出为 201 行。对同一输出目录的第二次编译返回
`MODELICA_OUTPUT_CONFLICT`，且第一次 FMU 的 SHA-256 保持不变。

新增 application 测试 6 个、公共 API 集成测试 4 个；完整后端测试发现并执行 787 项，
启用真实 OMC 的本机回归以零退出状态完成。GitHub Actions 结果在推送后单独核验。
