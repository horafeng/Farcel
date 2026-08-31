import unittest

from farcel.contracts._arrays import ArrayShapeError, flatten_array, reshape_array


class ArrayValueTests(unittest.TestCase):
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
