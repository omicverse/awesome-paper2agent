"""Regression tests for the local acceptance scripts."""

import unittest
from types import SimpleNamespace

from audit.check_reproducibility import checks_pass
from audit.mcp_result_fields import result_field


class AuditHarnessTests(unittest.TestCase):
    def test_tool_result_fields_accept_both_mcp_client_shapes(self):
        legacy = SimpleNamespace(isError=False, structuredContent={"cells": 3})
        modern = SimpleNamespace(is_error=True, structured_content=None)
        self.assertFalse(result_field(legacy, "isError", "is_error"))
        self.assertEqual(result_field(legacy, "structuredContent", "structured_content"), {"cells": 3})
        self.assertTrue(result_field(modern, "isError", "is_error"))
        self.assertIsNone(result_field(modern, "structuredContent", "structured_content"))
        with self.assertRaises(AttributeError):
            result_field(SimpleNamespace(), "isError", "is_error")

    def test_reproducibility_requires_both_checks(self):
        self.assertTrue(checks_pass({"repeat_equal": True, "eol_equal": True}))
        self.assertFalse(checks_pass({"repeat_equal": True, "eol_equal": False}))
        self.assertFalse(checks_pass({"repeat_equal": False, "eol_equal": True}))


if __name__ == "__main__":
    unittest.main()
