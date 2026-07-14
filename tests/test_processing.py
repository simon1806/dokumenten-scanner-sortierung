from __future__ import annotations

import unittest

from scanner_sorter.models import DetectedDocument
from scanner_sorter.processing import ProcessingError, group_page_detections


class GroupingTests(unittest.TestCase):
    def test_continuation_page_stays_with_document(self) -> None:
        nowak = DetectedDocument("LS", "4781776", "Nowak")
        heitzer = DetectedDocument("LS", "26060887", "Heitzer")

        groups = group_page_detections([nowak, None, heitzer])

        self.assertEqual([[0, 1], [2]], [group.page_indexes for group in groups])

    def test_first_page_must_be_recognised(self) -> None:
        with self.assertRaises(ProcessingError):
            group_page_detections([None, DetectedDocument("AM", "3250672")])


if __name__ == "__main__":
    unittest.main()
