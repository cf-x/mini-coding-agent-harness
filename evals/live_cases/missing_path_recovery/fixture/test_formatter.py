import unittest

from app.formatter import format_title


class FormatTitleTest(unittest.TestCase):
    def test_title_cases_words(self) -> None:
        self.assertEqual(format_title("coding agent harness"), "Coding Agent Harness")

    def test_trims_outer_whitespace(self) -> None:
        self.assertEqual(format_title("  trace replay  "), "Trace Replay")


if __name__ == "__main__":
    unittest.main()
