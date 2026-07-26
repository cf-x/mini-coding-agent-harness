import unittest

from validators import parse_age


class ParseAgeTest(unittest.TestCase):
    def test_valid_boundaries(self) -> None:
        self.assertEqual(parse_age("0"), 0)
        self.assertEqual(parse_age(" 130 "), 130)

    def test_rejects_out_of_range_values(self) -> None:
        for value in ("-1", "131"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_age(value)

    def test_rejects_non_decimal_values(self) -> None:
        for value in ("", "2.5", "age"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_age(value)


if __name__ == "__main__":
    unittest.main()
