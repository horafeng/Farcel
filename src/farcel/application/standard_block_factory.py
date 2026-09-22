"""Create graph model nodes from registered standard-block descriptors."""

from __future__ import annotations

from collections.abc import Mapping
from importlib import resources
from os import fspath

from farcel.application.standard_library import StandardBlockCatalog
from farcel.contracts.graph import ModelNode, ModelNodeConfig


class StandardBlockFactory:
    """Bridge standard-block metadata to existing graph model-node contracts.

    The factory deliberately only resolves a packaged FMU asset to the local
    path expected by ``ModelNode``. FMU parsing and simulation remain owned by
    the existing infrastructure and graph runtime layers.
    """

    def __init__(
        self,
        catalog: StandardBlockCatalog,
        *,
        package: str = "farcel.standard_library",
        assets_directory: str = "assets",
    ) -> None:
        self._catalog = catalog
        self._package = package
        self._assets_directory = assets_directory

    def create_model_node(
        self,
        block_id: str,
        node_id: str,
        *,
        parameter_overrides: Mapping[str, object] | None = None,
    ) -> ModelNode:
        """Create an existing ``ModelNode`` for a registered standard block.

        ``node_id`` is supplied by the graph owner because it is graph-specific;
        a descriptor's ``block_id`` identifies the reusable standard block.
        """

        block = self._catalog.get_block(block_id)
        asset = resources.files(self._package).joinpath(
            self._assets_directory,
            block.fmu_asset,
        )

        if not asset.is_file():
            raise ValueError(f"标准模块 FMU asset 不存在: {block.fmu_asset}")

        try:
            model_path = fspath(asset)
        except TypeError as error:
            raise ValueError(
                f"标准模块 FMU asset 未提供本地文件路径: {block.fmu_asset}"
            ) from error

        return ModelNode(
            node_id=node_id,
            model_path=model_path,
            config=ModelNodeConfig(
                parameters=dict(parameter_overrides or {}),
                execution_interface=block.execution_interface,
            ),
        )
