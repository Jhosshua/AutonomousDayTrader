"""(Re)build the checked-in synthetic parity fixture:
backend/tests/unit/orbs/fixtures/parity_synthetic_2031-03-04.tar.gz

    python scripts/orbs_parity/make_synthetic_fixture.py

Writes the synthetic tape (synthetic.py) into a temp cache root, then runs ORBStraddle's ORIGINAL modules
over it in record mode with the synthetic relay answering (no network at all), which saves every non-tape
response into the exact store and the original's full output as golden_original.json. The day directory
is then packed into one tar.gz. The always-on test unpacks it, replays ADT's copy on the same inputs and
requires an exact match with the golden output.
"""
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
FIXTURE = os.path.join(REPO, "backend", "tests", "unit", "orbs", "fixtures", "parity_synthetic_2031-03-04.tar.gz")
WORK = tempfile.mkdtemp(prefix="orbs_fixture_")
os.environ["ADT_PARITY_CACHE_ROOT"] = WORK
os.environ["ADT_PARITY_SYNTHETIC"] = "1"
sys.path.insert(0, HERE)
import parity_common as common  # noqa: E402
import parity_synthetic as synthetic  # noqa: E402


def main():
    try:
        ddir = common.day_dir(synthetic.DAY)
        os.makedirs(ddir)
        synthetic.write_tape(common.TapeStore(synthetic.DAY))
        out = os.path.join(ddir, "golden_original.json")
        cmd = [sys.executable, os.path.join(HERE, "orig_runner.py"), "--date", synthetic.DAY, "--mode", "record",
               "--out", out]
        if subprocess.run(cmd, env=dict(os.environ)).returncode:
            raise SystemExit("original run failed")
        os.makedirs(os.path.dirname(FIXTURE), exist_ok=True)
        with tarfile.open(FIXTURE, "w:gz") as tar:
            tar.add(ddir, arcname=synthetic.DAY)
        print("fixture written:", FIXTURE, os.path.getsize(FIXTURE), "bytes")
    finally:
        shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    main()
