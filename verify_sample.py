"""Run the contest scorer three times with locally measured selector CPU."""

import os
import re
import resource
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path


def cpu_used():
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


def main(kit, source):
    selector = Path(__file__).with_name("selector.py")
    sample = kit / "sample"
    env = dict(
        os.environ,
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONHASHSEED="0",
        OPENBLAS_NUM_THREADS="1",
        OMP_NUM_THREADS="1",
        SKIP_INTEGRATION="0",
    )
    totals = []
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(3):
            output = Path(tmp) / "selection.jsonl"
            with output.open("wb") as stream:
                before = cpu_used()
                subprocess.run(
                    [sys.executable, selector, sample, source],
                    stdout=stream,
                    env=env,
                    check=True,
                )
                cpu = cpu_used() - before
            report = subprocess.check_output(
                [
                    sys.executable,
                    kit / "score.py",
                    sample,
                    sample / "answers.json",
                    output,
                    "--cpu-seconds",
                    str(cpu),
                ],
                text=True,
            )
            print(f"Run {i+1}\n{report}", flush=True)
            totals.append(float(re.search(r"Total:\s+([\d.]+)", report)[1]))
    print(f"Local median {statistics.median(totals):.1f} s")


if __name__ == "__main__":
    main(*(Path(arg).resolve() for arg in sys.argv[1:]))
