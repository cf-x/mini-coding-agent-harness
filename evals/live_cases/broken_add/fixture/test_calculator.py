import unittest

from calculator import add


class AddTest(unittest.TestCase):
    def test_positive_numbers(self) -> None:
        self.assertEqual(add(7, 5), 12)

    def test_negative_number(self) -> None:
        self.assertEqual(add(-2, 5), 3)


if __name__ == "__main__":
    unittest.main()
