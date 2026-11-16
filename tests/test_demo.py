import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from analysis_archive.__main__ import main


class CLI(unittest.TestCase):
    def call(self, argv):
        output = io.StringIO()
        with redirect_stdout(output):
            main(argv)
        return json.loads(output.getvalue())

    def test_demo(self):
        output = self.call(["demo"])
        self.assertEqual(output["task_count"], 5)
        self.assertFalse(output["failure_published_result"])
        self.assertEqual(output["historical"]["revision"], 1)
        self.assertEqual(output["revised"]["revision"], 2)
        self.assertEqual(output["retry"]["statistics"], output["historical"]["statistics"])
        self.assertAlmostEqual(output["historical"]["statistics"]["means"][0], 2)
        self.assertAlmostEqual(output["revised"]["statistics"]["means"][0], 6)

    def test_seed_run_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "archive.sqlite")
            ids = self.call(["seed", path])
            result = self.call(["run", path, str(ids["pending"])])
            self.assertEqual(self.call(["result", path, str(ids["pending"])]), result)
            self.assertEqual(result["revision"], 1)
