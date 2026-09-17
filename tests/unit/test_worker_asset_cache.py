from __future__ import annotations

import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from farcel.contracts.errors import EngineError, ErrorCode
from farcel.infrastructure.worker_assets import LocalWorkerAssetCache


class WorkerAssetCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = TemporaryDirectory()
        self.root = Path(self._temporary_directory.name)
        self.cache = LocalWorkerAssetCache(self.root)
        self.content = b"worker-owned fmu bytes"
        self.sha256 = hashlib.sha256(self.content).hexdigest()

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_put_has_and_resolve_use_worker_owned_content_addressed_path(self) -> None:
        self.cache.put_asset(self.sha256, self.content)

        expected = self.root / "assets" / f"{self.sha256}.fmu"
        actual = self.cache.resolve_asset(self.sha256)
        self.assertTrue(self.cache.has_asset(self.sha256))
        self.assertEqual(actual, expected.resolve(strict=True))
        self.assertEqual(actual.parent.name, "assets")
        self.assertEqual(actual.name, f"{self.sha256}.fmu")
        self.assertEqual(actual.read_bytes(), self.content)

    def test_repeated_put_and_preexisting_correct_file_are_idempotent(self) -> None:
        self.cache.put_asset(self.sha256, self.content)
        path = self.cache.resolve_asset(self.sha256)
        self.cache.put_asset(self.sha256, self.content)
        self.assertEqual(path.read_bytes(), self.content)

        other_root = self.root / "preexisting"
        path = other_root / "assets" / f"{self.sha256}.fmu"
        path.parent.mkdir(parents=True)
        path.write_bytes(self.content)
        other_cache = LocalWorkerAssetCache(other_root)
        self.assertTrue(other_cache.has_asset(self.sha256))
        other_cache.put_asset(self.sha256, self.content)
        actual = other_cache.resolve_asset(self.sha256)
        self.assertEqual(actual, path.resolve(strict=True))
        self.assertEqual(actual.parent.name, "assets")
        self.assertEqual(actual.name, f"{self.sha256}.fmu")
        self.assertEqual(actual.read_bytes(), self.content)

    def test_missing_and_corrupted_entries_are_not_cache_hits_and_valid_put_repairs_file(self) -> None:
        self.assertFalse(self.cache.has_asset(self.sha256))
        with self.assertRaises(EngineError) as raised:
            self.cache.resolve_asset(self.sha256)
        self.assertEqual((raised.exception.code, raised.exception.details["phase"]), (ErrorCode.IMPORT_ERROR, "worker_asset_resolve"))

        path = self.root / "assets" / f"{self.sha256}.fmu"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"corrupted")
        self.assertFalse(self.cache.has_asset(self.sha256))
        self.cache.put_asset(self.sha256, self.content)
        self.assertTrue(self.cache.has_asset(self.sha256))
        self.assertEqual(path.read_bytes(), self.content)

    def test_invalid_sha256_is_rejected_by_every_public_operation(self) -> None:
        invalid_values = ("", "a" * 63, "a" * 65, "A" * 64, "g" * 64, 1)
        for sha256 in invalid_values:
            for operation in (
                lambda: self.cache.has_asset(sha256),
                lambda: self.cache.put_asset(sha256, self.content),
                lambda: self.cache.resolve_asset(sha256),
            ):
                with self.subTest(sha256=sha256, operation=operation):
                    with self.assertRaises(EngineError) as raised:
                        operation()
                    self.assertEqual(
                        (raised.exception.code, raised.exception.details["phase"]),
                        (ErrorCode.VALIDATION_ERROR, "worker_asset_validate"),
                    )

    def test_content_type_hash_and_size_failures_leave_no_asset_or_temp_file(self) -> None:
        with self.assertRaises(EngineError):
            self.cache.put_asset(self.sha256, bytearray(self.content))
        with self.assertRaises(EngineError):
            self.cache.put_asset(self.sha256, b"different")
        self.assertFalse((self.root / "assets" / f"{self.sha256}.fmu").exists())
        self.assertEqual(list((self.root / "assets").glob("*.tmp")) if (self.root / "assets").exists() else [], [])

        with patch("farcel.infrastructure.worker_assets.MAX_ASSET_FRAME_BYTES", 3):
            too_large = b"four"
            with self.assertRaises(EngineError) as raised:
                self.cache.put_asset(hashlib.sha256(too_large).hexdigest(), too_large)
        self.assertEqual(raised.exception.code, ErrorCode.VALIDATION_ERROR)

    def test_failed_atomic_replace_cleans_up_temp_file(self) -> None:
        with patch("farcel.infrastructure.worker_assets.os.replace", side_effect=OSError("injected")):
            with self.assertRaises(EngineError) as raised:
                self.cache.put_asset(self.sha256, self.content)

        self.assertEqual((raised.exception.code, raised.exception.details["phase"]), (ErrorCode.PROJECT_IO_ERROR, "worker_asset_put"))
        assets_root = self.root / "assets"
        self.assertEqual(list(assets_root.glob("*.tmp")), [])
        self.assertFalse((assets_root / f"{self.sha256}.fmu").exists())

    def test_directory_and_symlink_entries_are_not_trusted(self) -> None:
        path = self.root / "assets" / f"{self.sha256}.fmu"
        path.parent.mkdir(parents=True)
        path.mkdir()
        with self.assertRaises(EngineError) as raised:
            self.cache.has_asset(self.sha256)
        self.assertEqual(raised.exception.code, ErrorCode.PROJECT_IO_ERROR)

        self.cache.put_asset(self.sha256, self.content)
        self.assertTrue(self.cache.has_asset(self.sha256))

        path.unlink()
        outside = self.root / "outside.fmu"
        outside.write_bytes(self.content)
        try:
            os.symlink(outside, path)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"当前平台无法创建 symlink: {exc}")

        self.assertFalse(self.cache.has_asset(self.sha256))
        with self.assertRaises(EngineError):
            self.cache.resolve_asset(self.sha256)
        self.cache.put_asset(self.sha256, self.content)
        self.assertFalse(path.is_symlink())
        self.assertEqual(path.read_bytes(), self.content)
        self.assertEqual(outside.read_bytes(), self.content)

    def test_existing_file_hashing_uses_chunked_path(self) -> None:
        self.cache.put_asset(self.sha256, self.content)
        with patch("farcel.infrastructure.worker_assets._HASH_CHUNK_SIZE", 1):
            self.assertTrue(self.cache.has_asset(self.sha256))


if __name__ == "__main__":
    unittest.main()
