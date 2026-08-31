import unittest

from farcel.contracts._arrays import (
    ArrayShapeError,
    EffectiveShapeError,
    flatten_array,
    reshape_array,
    resolve_effective_shape,
)


class ArrayValueTests(unittest.TestCase):
    def test_resolves_structural_dimension_references_without_mutating_defaults(self) -> None:
        default_shape = (3, 3)

        self.assertEqual(
            resolve_effective_shape(default_shape, (2, 2), {2: 4}),
            (4, 4),
        )
        self.assertEqual(
            resolve_effective_shape(default_shape, (2, 1), {1: 2, 2: 4}),
            (4, 2),
        )
        self.assertEqual(
            resolve_effective_shape((2, 3), (None, 10), {10: 5}),
            (2, 5),
        )

    def test_effective_shape_rejects_missing_or_invalid_structural_dimensions(self) -> None:
        for values in ({}, {10: -1}, {10: True}, {10: 2.5}):
            with self.subTest(values=values):
                with self.assertRaises(EffectiveShapeError):
                    resolve_effective_shape((2, 3), (None, 10), values)
        self.assertEqual(resolve_effective_shape((2,), (10,), {10: 0}), (0,))

    def test_flatten_and_reshape_one_dimension(self) -> None:
        self.assertEqual(flatten_array([1.0, 2.0, 3.0], (3,)), (1.0, 2.0, 3.0))
        self.assertEqual(reshape_array((1.0, 2.0, 3.0), (3,)), (1.0, 2.0, 3.0))

    def test_flatten_and_reshape_two_dimensions_in_row_major_order(self) -> None:
        value = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
        self.assertEqual(flatten_array(value, (2, 3)), (1.0, 0.0, 0.0, 0.0, 1.0, 0.0))
        self.assertEqual(reshape_array(flatten_array(value, (2, 3)), (2, 3)), value)

    def test_flatten_rejects_wrong_rank_and_dimension_length(self) -> None:
        for value in ((1.0, 2.0), ((1.0, 2.0, 3.0),)):
            with self.subTest(value=value):
                with self.assertRaises(ArrayShapeError):
                    flatten_array(value, (3,))


if __name__ == "__main__":
    unittest.main()
