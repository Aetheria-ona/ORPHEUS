"""SSQ resolves optional inputs and cache identities in the selected runtime."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class QueryRuntimePaths(unittest.TestCase):
    def test_switching_packs_with_equal_file_sizes_and_times(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        probe = (
            "import json\n"
            "from tools import ssq\n"
            "values = [ssq.names()['buttons'][1], ssq.codec_entry('STU_00000001'), ssq.signature()]\n"
            "print(json.dumps(values))"
        )
        signatures = []
        for label in ("A", "B"):
            data = root / label / "data"
            data.mkdir(parents=True)
            names = {"buttons": {"1": label}, "vars": {}, "graphs": {}, "modes": {}}
            codecs = {"classes": {"00000001": {"label": label}}}
            for filename, table in (
                ("statescript_names_174.json", names), ("statescript_codecs_174.json", codecs),
            ):
                path = data / filename
                path.write_text(json.dumps(table), encoding="utf-8")
                os.utime(path, (1_700_000_000, 1_700_000_000))
            output = subprocess.check_output(
                [sys.executable, "-B", "-c", probe],
                cwd=Path(__file__).resolve().parents[2],
                env={**os.environ, "ORPHEUS_RUNTIME_DIR": str(data.parent)},
                text=True,
            )
            name, codec, signature = json.loads(output)
            self.assertEqual((name, codec), (label, {"label": label}))
            signatures.append(signature)
        self.assertNotEqual(signatures[0], signatures[1])
