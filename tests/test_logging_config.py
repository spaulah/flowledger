"""Tests for logging_config.configure_logging. Run from the project root: python -m unittest discover -s tests"""
import logging
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from logging_config import configure_logging  # noqa: E402


class ConfigureLoggingTests(unittest.TestCase):
    def tearDown(self):
        # Leave the root logger as the test runner expects it.
        logging.getLogger().setLevel(logging.WARNING)
        for name in ("httpx", "httpcore", "openai", "chromadb", "urllib3"):
            logging.getLogger(name).setLevel(logging.NOTSET)

    def test_default_keeps_app_info_but_hides_library_requests(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LOG_LEVEL", None)
            configure_logging()
        self.assertEqual(logging.getLogger().level, logging.INFO)
        self.assertEqual(logging.getLogger("httpx").level, logging.WARNING)
        self.assertEqual(logging.getLogger("openai").level, logging.WARNING)
        self.assertEqual(logging.getLogger("chromadb").level, logging.WARNING)

    def test_debug_shows_library_requests(self):
        with mock.patch.dict(os.environ, {"LOG_LEVEL": "debug"}):
            configure_logging()
        self.assertEqual(logging.getLogger().level, logging.DEBUG)
        self.assertEqual(logging.getLogger("httpx").level, logging.DEBUG)

    @staticmethod
    def passes(name, level):
        """Would a record from logger `name` at `level` get through the root handler's filter?"""
        record = logging.LogRecord(name, level, __file__, 1, "message", None, None)
        return all(h.filter(record) for h in logging.getLogger().handlers)

    def test_info_from_other_packages_is_dropped_whatever_their_logger_name(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("LOG_LEVEL", None)
            configure_logging()
        # "httpx2" is what a newer httpx logs as. A fixed list of names would have missed it.
        for name in ("httpx", "httpx2", "httpx2._client", "openai._base_client", "chromadb.telemetry", "anything_new"):
            self.assertFalse(self.passes(name, logging.INFO), name)
            self.assertFalse(self.passes(name, logging.DEBUG), name)

    def test_own_info_passes(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("LOG_LEVEL", None)
            configure_logging()
        for name in ("root", "__main__", "api", "rag_service", "llm_clients"):
            self.assertTrue(self.passes(name, logging.INFO), name)

    def test_warnings_and_errors_pass_from_every_package(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("LOG_LEVEL", None)
            configure_logging()
        for name in ("httpx2", "openai", "chromadb"):
            self.assertTrue(self.passes(name, logging.WARNING), name)
            self.assertTrue(self.passes(name, logging.ERROR), name)

    def test_debug_lets_everything_through(self):
        with mock.patch.dict(os.environ, {"LOG_LEVEL": "DEBUG"}):
            configure_logging()
        self.assertTrue(self.passes("httpx2", logging.DEBUG))
        self.assertTrue(self.passes("httpx2", logging.INFO))

    def test_unknown_level_falls_back_to_info(self):
        with mock.patch.dict(os.environ, {"LOG_LEVEL": "loud"}):
            configure_logging()
        self.assertEqual(logging.getLogger().level, logging.INFO)


if __name__ == "__main__":
    unittest.main()
