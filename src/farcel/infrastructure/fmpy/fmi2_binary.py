from __future__ import annotations

import os
import zipfile

from fmpy import platform as current_platform, sharedLibraryExtension


def fmi2_native_library_archive_member(model_identifier: str) -> str:
    """Return the exact FMI2 library member selected by FMPy 0.3.31."""

    return f"binaries/{current_platform}/{model_identifier}{sharedLibraryExtension}"


def fmi2_native_library_is_present(source_path: str, model_identifier: str) -> bool:
    """Check the final FMPy FMI2 library path without loading native code."""

    expected_member = fmi2_native_library_archive_member(model_identifier)
    normalize = str.casefold if os.name == "nt" else str
    try:
        with zipfile.ZipFile(source_path) as archive:
            return any(
                normalize(member.replace("\\", "/")) == normalize(expected_member)
                for member in archive.namelist()
            )
    except (OSError, zipfile.BadZipFile):
        return False
