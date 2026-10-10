"""Exercise the shipped legacy exclusion guard with a failed metadata read."""

import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


class TransportExclusionTests(unittest.TestCase):
    def test_unreadable_labels_stop_before_claiming_a_review_gate(self):
        workflow = Path(__file__).resolve().parents[2] / ".github/workflows/wake-on-ci-green.yml"
        block = workflow.read_text().split("      - name: Check label and required-check status", 1)[1]
        guard = textwrap.dedent(block.split("        run: |\n", 1)[1].split("          has_label=", 1)[0])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            env = {"PATH": os.defpath, "GITHUB_OUTPUT": str(output), "REPO": "owner/repo",
                   "PR_NUMBER": "1", "EXCLUDED_LABEL": "review-first-ci-active"}
            for response, expected_code, expected_skip in [
                ("return 22", 22, False),
                ("printf '%s\\n' review-first-ci-active", 0, True),
                ("printf '%s\\n' unrelated", 0, False),
            ]:
                with self.subTest(response=response):
                    output.write_text("")
                    result = subprocess.run(["bash", "-c", "gh() { " + response + "; }\n" + guard],
                                            env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, expected_code, result.stderr)
                    self.assertEqual("skip=true" in output.read_text(), expected_skip)


if __name__ == "__main__":
    unittest.main()
