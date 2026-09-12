from __future__ import annotations

from pathlib import Path
import unittest

from farcel.application.worker_runtime_factory import WorkerRuntimeFactory
from farcel.contracts.errors import EngineError, ErrorCode
from farcel.contracts.models import (
    CapabilitySet,
    InterfaceCapability,
    InterfaceType,
    ModelMetadata,
    SimulationConfig,
)
from farcel.contracts.worker_protocol import CreateRuntimeRequest


_SHA = "a" * 64


class _AssetStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.requests: list[str] = []

    def resolve_asset(self, sha256: str) -> Path:
        self.requests.append(sha256)
        return self.path


class _Importer:
    def __init__(self, metadata: ModelMetadata | Exception) -> None:
        self.metadata = metadata
        self.paths: list[Path] = []

    def load(self, path: Path) -> ModelMetadata:
        self.paths.append(path)
        if isinstance(self.metadata, Exception):
            raise self.metadata
        return self.metadata


class _Factory:
    def __init__(self, runtime: object) -> None:
        self.runtime = runtime
        self.calls: list[tuple[ModelMetadata, SimulationConfig]] = []

    def create(self, metadata: ModelMetadata, config: SimulationConfig) -> object:
        self.calls.append((metadata, config))
        return self.runtime


class WorkerRuntimeFactoryTests(unittest.TestCase):
    def test_resolves_worker_local_asset_and_selects_co_simulation_factory(self) -> None:
        path = Path("worker-cache/assets") / f"{_SHA}.fmu"
        asset_store = _AssetStore(path)
        importer = _Importer(_metadata(InterfaceType.CO_SIMULATION))
        runtime = object()
        co_factory, me_factory = _Factory(runtime), _Factory(object())
        factory = WorkerRuntimeFactory(asset_store, importer, co_factory, me_factory)

        result = factory.create(CreateRuntimeRequest("node", _SHA, SimulationConfig()))

        self.assertIs(result, runtime)
        self.assertEqual(asset_store.requests, [_SHA])
        self.assertEqual(importer.paths, [path])
        self.assertEqual(len(co_factory.calls), 1)
        self.assertEqual(me_factory.calls, [])

    def test_selects_model_exchange_factory(self) -> None:
        runtime = object()
        co_factory, me_factory = _Factory(object()), _Factory(runtime)
        factory = WorkerRuntimeFactory(
            _AssetStore(Path("worker-cache/model.fmu")),
            _Importer(_metadata(InterfaceType.MODEL_EXCHANGE)),
            co_factory,
            me_factory,
        )

        self.assertIs(factory.create(CreateRuntimeRequest("node", _SHA, SimulationConfig())), runtime)
        self.assertEqual(co_factory.calls, [])
        self.assertEqual(len(me_factory.calls), 1)

    def test_invalid_config_is_rejected_at_worker_boundary(self) -> None:
        factory = WorkerRuntimeFactory(
            _AssetStore(Path("worker-cache/model.fmu")),
            _Importer(_metadata(InterfaceType.CO_SIMULATION)),
            _Factory(object()),
            _Factory(object()),
        )

        with self.assertRaises(EngineError) as raised:
            factory.create(CreateRuntimeRequest("node", _SHA, SimulationConfig(stop_time=0.0)))

        self.assertEqual((raised.exception.code, raised.exception.details["phase"]), (ErrorCode.CONFIG_ERROR, "worker_runtime_creation"))
        self.assertTrue(raised.exception.details["issues"])

    def test_stable_asset_error_is_preserved_and_unexpected_importer_error_is_wrapped(self) -> None:
        class FailingStore(_AssetStore):
            def resolve_asset(self, sha256: str) -> Path:
                raise EngineError(ErrorCode.IMPORT_ERROR, "asset missing")

        factory = WorkerRuntimeFactory(
            FailingStore(Path("unused")), _Importer(_metadata(InterfaceType.CO_SIMULATION)), _Factory(object()), _Factory(object())
        )
        with self.assertRaises(EngineError) as raised:
            factory.create(CreateRuntimeRequest("node", _SHA, SimulationConfig()))
        self.assertEqual(raised.exception.code, ErrorCode.IMPORT_ERROR)

        factory = WorkerRuntimeFactory(
            _AssetStore(Path("worker-cache/model.fmu")), _Importer(RuntimeError("native failure")), _Factory(object()), _Factory(object())
        )
        with self.assertRaises(EngineError) as raised:
            factory.create(CreateRuntimeRequest("node", _SHA, SimulationConfig()))
        self.assertEqual((raised.exception.code, raised.exception.details["phase"]), (ErrorCode.INTERNAL_ERROR, "worker_runtime_creation"))


def _metadata(interface: InterfaceType) -> ModelMetadata:
    return ModelMetadata(
        model_id="model",
        source_path="worker-local-only",
        fmi_version="2.0",
        model_name="model",
        interface_types=(interface,),
        executable_interface=interface,
        interface_capabilities=(InterfaceCapability(interface, can_execute=True),),
        capabilities=CapabilitySet(can_execute=True),
    )
