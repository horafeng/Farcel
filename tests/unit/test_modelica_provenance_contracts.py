from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
import unittest

from farcel.contracts import ModelicaBuildProvenance, ModelicaSourceSnapshot


_SHA256 = "a" * 64


class ModelicaProvenanceContractTests(unittest.TestCase):
    def test_source_snapshot_is_an_immutable_portable_value(self) -> None:
        snapshot = ModelicaSourceSnapshot("sources/plant/FirstOrder.mo", _SHA256)

        self.assertEqual("sources/plant/FirstOrder.mo", snapshot.relative_path)
        self.assertEqual(_SHA256, snapshot.sha256)
        with self.assertRaises(FrozenInstanceError):
            snapshot.relative_path = "sources/other.mo"  # type: ignore[misc]
        with self.assertRaises((AttributeError, TypeError)):
            snapshot.extra = "not supported"  # type: ignore[attr-defined]

    def test_build_provenance_links_a_source_snapshot_to_a_model_asset(self) -> None:
        provenance = ModelicaBuildProvenance(
            build_id="build-001",
            source_snapshot=ModelicaSourceSnapshot("sources/FirstOrder.mo", _SHA256),
            model_name="ExampleSystems.FirstOrder",
            compiler_name="OpenModelica",
            compiler_version="1.26.3",
            fmi_version="2.0",
            fmu_type="cs",
            fmu_asset_id="asset-first-order",
        )

        self.assertEqual("asset-first-order", provenance.fmu_asset_id)
        self.assertEqual("ExampleSystems.FirstOrder", provenance.model_name)
        self.assertEqual(
            (
                "build_id",
                "source_snapshot",
                "model_name",
                "compiler_name",
                "compiler_version",
                "fmi_version",
                "fmu_type",
                "fmu_asset_id",
            ),
            tuple(field.name for field in fields(ModelicaBuildProvenance)),
        )
        self.assertNotIn("fmu_sha256", {field.name for field in fields(ModelicaBuildProvenance)})
