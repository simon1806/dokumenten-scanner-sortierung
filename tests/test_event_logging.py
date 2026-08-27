from __future__ import annotations

import re
import unittest

from scanner_sorter.event_logging import (
    EVENT_SCHEMA_VERSION,
    current_session_id,
    safe_event_value,
    structured_event,
)


class StructuredEventLoggingTests(unittest.TestCase):
    def test_event_contains_stable_schema_code_and_process_session(self) -> None:
        message = structured_event(
            "Vorgang abgeschlossen",
            "processing_completed",
            status="erfolgreich",
            gesamt_s="1.250",
        )

        self.assertIn(f"schema={EVENT_SCHEMA_VERSION}", message)
        self.assertIn("ereignis=processing_completed", message)
        self.assertIn(f"sitzung={current_session_id()}", message)
        self.assertIn("status=erfolgreich", message)
        self.assertRegex(current_session_id(), re.compile(r"^[0-9a-f]{32}$"))

    def test_dynamic_values_cannot_create_fields_or_additional_lines(self) -> None:
        value = safe_event_value("erste Zeile\nzweite; status=manipuliert")

        self.assertEqual("erste Zeile zweite, status=manipuliert", value)
        self.assertNotIn("\n", value)
        self.assertNotIn(";", value)

    def test_invalid_event_and_field_names_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            structured_event("Test", "Ungültig")
        with self.assertRaises(ValueError):
            structured_event("Test", "valid", **{"kein-bindestrich": 1})


if __name__ == "__main__":
    unittest.main()
