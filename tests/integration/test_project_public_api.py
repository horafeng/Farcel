import hashlib
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from farcel import create_backend
from farcel.contracts import (
    EngineError,
    ErrorCode,
    GraphSimulationConfig,
    InterfaceType,
    ModelAsset,
    ModelNode,
    ModelNodeConfig,
    SimulationCase,
    SimulationGraph,
    SimulationProject,
)


ROOT = Path(__file__).resolve().parents[2]
VAN_DER_POL = ROOT / "examples" / "fmus" / "VanDerPol.fmu"


@unittest.skipUnless(VAN_DER_POL.is_file(), "VanDerPol FMU is unavailable")
class ProjectPublicApiIntegrationTests(unittest.TestCase):
    def test_public_lifecycle_save_open_validate_and_relocation(self) -> None:
        with TemporaryDirectory() as directory:
            first_root = Path(directory) / "LocationA" / "Project"
            project = _project_with_van_der_pol(first_root)
            backend = create_backend()

            backend.save_project(first_root, project)
            opened = backend.open_project(first_root)
            report = backend.validate_project(first_root, opened)

            relocated_root = Path(directory) / "LocationB" / "Project"
            shutil.copytree(first_root, relocated_root)
            relocated = backend.open_project(relocated_root)
            relocated_report = backend.validate_project(relocated_root, relocated)

            self.assertEqual(opened, project)
            self.assertTrue(report.is_valid)
            self.assertTrue(relocated_report.is_valid)
            self.assertEqual(relocated.model_assets[0].relative_path, "models/VanDerPol.fmu")
            self.assertFalse((first_root / "results").exists())

    def test_open_and_save_are_structural_while_validate_checks_asset_integrity(self) -> None:
        with TemporaryDirectory() as directory:
            project_root = Path(directory) / "Project"
            model_path = project_root / "models" / VAN_DER_POL.name
            model_path.parent.mkdir(parents=True)
            shutil.copyfile(VAN_DER_POL, model_path)
            project = SimulationProject(
                "project",
                "Project",
                (ModelAsset("vdp", "Van der Pol", "models/VanDerPol.fmu", "0" * 64),),
            )
            backend = create_backend()

            backend.save_project(project_root, project)
            opened = backend.open_project(project_root)

            with self.assertRaises(EngineError) as raised:
                backend.validate_project(project_root, opened)

            self.assertEqual(raised.exception.code, ErrorCode.CONFIG_ERROR)
            self.assertIn(
                "ASSET_CHECKSUM_MISMATCH",
                tuple(issue["code"] for issue in raised.exception.details["issues"]),
            )
            self.assertFalse((project_root / "results").exists())


class ProjectPublicApiErrorIntegrationTests(unittest.TestCase):
    def test_missing_and_malformed_project_json_keep_repository_error_codes(self) -> None:
        backend = create_backend()
        with TemporaryDirectory() as directory:
            missing_root = Path(directory) / "Missing"
            with self.assertRaises(EngineError) as missing:
                backend.open_project(missing_root)

            malformed_root = Path(directory) / "Malformed"
            malformed_root.mkdir()
            (malformed_root / "project.json").write_text("{", encoding="utf-8")
            with self.assertRaises(EngineError) as malformed:
                backend.open_project(malformed_root)

        self.assertEqual(missing.exception.code, ErrorCode.PROJECT_IO_ERROR)
        self.assertEqual(malformed.exception.code, ErrorCode.PROJECT_FORMAT_ERROR)


def _project_with_van_der_pol(project_root: Path) -> SimulationProject:
    model_path = project_root / "models" / VAN_DER_POL.name
    model_path.parent.mkdir(parents=True)
    shutil.copyfile(VAN_DER_POL, model_path)
    relative_path = "models/VanDerPol.fmu"
    asset = ModelAsset("vdp", "Van der Pol", relative_path, _sha256(model_path))
    graph = SimulationGraph(
        nodes=(
            ModelNode(
                "plant",
                relative_path,
                ModelNodeConfig(
                    selected_outputs=("x0",),
                    execution_interface=InterfaceType.CO_SIMULATION,
                ),
            ),
        )
    )
    case = SimulationCase(
        "case-1",
        "Nominal",
        graph,
        GraphSimulationConfig(stop_time=0.02, communication_step=0.01),
    )
    return SimulationProject("project", "Project", (asset,), (case,))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
