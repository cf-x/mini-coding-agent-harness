import unittest

from names import normalize_name


class NormalizeNameTest(unittest.TestCase):
    def test_trims_collapses_and_lowercases(self) -> None:
        self.assertEqual(normalize_name("  Ada   LOVELACE  "), "ada lovelace")

    def test_handles_tabs_and_newlines(self) -> None:
        self.assertEqual(normalize_name("\tGrace\nHopper "), "grace hopper")

    def test_empty_whitespace(self) -> None:
        self.assertEqual(normalize_name("   "), "")


if __name__ == "__main__":
    unittest.main()
