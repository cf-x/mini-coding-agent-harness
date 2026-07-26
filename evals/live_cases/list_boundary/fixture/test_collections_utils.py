import unittest

from collections_utils import last_item


class LastItemTest(unittest.TestCase):
    def test_empty_list(self) -> None:
        self.assertIsNone(last_item([]))

    def test_one_item(self) -> None:
        self.assertEqual(last_item(["only"]), "only")

    def test_multiple_items(self) -> None:
        self.assertEqual(last_item([1, 2, 3]), 3)


if __name__ == "__main__":
    unittest.main()
