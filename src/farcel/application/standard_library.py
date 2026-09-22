"""Application-level in-memory catalog for Farcel standard-block descriptors."""

from __future__ import annotations

from farcel.contracts.blocks import BlockCategoryDescriptor, BlockDescriptor


class StandardBlockCatalog:
    """Registers and queries Farcel-owned standard-block descriptors."""

    def __init__(self) -> None:
        self._categories: dict[str, BlockCategoryDescriptor] = {}
        self._blocks: dict[str, BlockDescriptor] = {}

    def register_category(self, category: BlockCategoryDescriptor) -> None:
        """Register one category exactly once."""
        if category.category_id in self._categories:
            raise ValueError(f"category_id 已注册: {category.category_id}")
        self._categories[category.category_id] = category

    def register_block(self, block: BlockDescriptor) -> None:
        """Register one block whose category is already registered."""
        if block.block_id in self._blocks:
            raise ValueError(f"block_id 已注册: {block.block_id}")
        if block.category_id not in self._categories:
            raise ValueError(f"block.category_id 尚未注册: {block.category_id}")
        self._blocks[block.block_id] = block

    def list_categories(self) -> tuple[BlockCategoryDescriptor, ...]:
        """Return categories in their caller-defined registration order."""
        return tuple(self._categories.values())

    def list_blocks(self) -> tuple[BlockDescriptor, ...]:
        """Return blocks in their caller-defined registration order."""
        return tuple(self._blocks.values())

    def get_block(self, block_id: str) -> BlockDescriptor:
        """Return one registered block or report a clear missing-ID error."""
        try:
            return self._blocks[block_id]
        except KeyError:
            raise ValueError(f"标准模块不存在: {block_id}") from None
