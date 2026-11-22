# Blast Radius selector

The selector reads the mounted test catalog and patches and emits one JSON line per commit. Its coverage map comes from running all 1,620 catalogued tests against SQLGlot revision `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`.

The profile records executed lines, fixture reads, parser dispatch calls, configuration access and operand types. At runtime the selector compares the original and edited Python syntax trees. For small candidate sets it runs the affected tests against the patched code in a time-limited child process. It evaluates one through eight worker schedules using the recorded durations and the two-second worker charge.

## Repeat the sample benchmark

From this directory, run the command below with a Python environment containing the packages in `requirements.txt`. The first argument is the unpacked starter kit containing `score.py` and `sample/`. The second is the unpacked SQLGlot revision above. The three runs use the starter kit's scorer and include local process CPU time. Only the verification script reads the sample answers.

```sh
python verify_sample.py /path/to/unpacked-starter-kit /path/to/pinned-sqlglot
```

## Container entry point

```sh
docker build --platform linux/amd64 -t blast-radius .
docker run --rm --platform linux/amd64 --network none --read-only \
  --cpus 2 --memory 4g --pids-limit 256 --user 65534:65534 \
  --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp:rw,size=64m,mode=1777 -e HOME=/tmp \
  -v "$SAMPLE/commits:/data/commits:ro" \
  -v "$SAMPLE/tests.json:/data/tests.json:ro" blast-radius
```

Set `SAMPLE` to the absolute path of the kit's `sample` directory. The image downloads the pinned upstream source, caches the converted CSV rows, and records clean parser results during the build. The image uses the same hash seed while building the parser comparisons and while checking patches.

## Rebuild the coverage map

The checked-in profiles.zip and stress.json are ready to use. To regenerate it, use Python 3.13  with the test dependencies and the pinned source. The profiler runs process-pool tasks inline, including a pickle round trip for arguments and results, so it can trace both computation and serialization.

```sh
python build_map.py /path/to/pinned-sqlglot /path/to/sample/tests.json coverage.json
```

## How expensive selections are checked

The additional maps record the execution of individual TPC queries and generator dialects, the scalar values observed at comparisons, and the execution of guarded expressions. When a TPC fixture changes, the selector compares parsed query pairs and runs the affected iterations using the original assertions. For code changes confined to a traced SQL transformation, the selector can check its output against the baseline and run the original query-level test if the output differs. Unfinished checks retain the candidate test.

The runtime loads the packed profiles on demand. The JSON files below are intermediate build products. Each profile can be regenerated from the clean upstream checkout with the same Python environment used for the main map. In the commands below, `SRC` is the absolute upstream path and `CATALOG` is the absolute path to the starter kit's `sample/tests.json`.

```sh
python build_cases.py "$SRC" cases.json
python build_stages.py "$SRC" stages.json
python build_branches.py "$SRC" "$CATALOG" branches.json
python build_values.py "$SRC" "$CATALOG" values.json
python build_values.py "$SRC" "$CATALOG" guards.json --guards
python build_rows.py "$SRC" "$CATALOG" rows.z
python build_calls.py "$SRC" "$CATALOG" calls.z
python build_boundary.py "$SRC" "$CATALOG" boundary.z
python build_engine.py "$SRC" engine.z
python build_stress.py "$SRC" stress.json
python compile_profiles.py . "$SRC"
python build_rules.py "$SRC" "$CATALOG" rules.zip
python build_rule_lines.py "$SRC" rule-lines.z
python build_query_index.py "$SRC" query-index.json
PYTHONHASHSEED=0 python build_sql_outputs.py "$SRC" sql-outputs.zip
PYTHONHASHSEED=0 python build_checkpoints.py "$SRC" optimizer-steps.zip
python build_truth.py "$SRC" "$CATALOG" truth.zip
PYTHONHASHSEED=0 python build_parse_rows.py "$SRC" parse-rows.json
python table_fixtures.py "$SRC"
PYTHONHASHSEED=0 python build_plans.py "$SRC" execution-keys.json
PYTHONHASHSEED=0 python build_parser_digests.py "$SRC" parser-digests.json
```

## Runtime checks

The selector traces optimizer fixture rows and individual dialect assertion calls on the pinned baseline. For eligible edits it replays the affected assertions. For expensive SQL queries it can compare parser outputs, execution plans and SQL passed to the reference engine. The worker schedule accounts for both test duration and startup cost.

A parser stress check uses two smaller inputs when baseline coverage and the edited source meet its eligibility rules. A failure visible only at full input size can escape this check. All performance figures from the sample depend on the test mix and local CPU.

For eligible optimizer changes, rules.zip supplies inputs captured at individual rule calls. Matching all observed outputs can eliminate a whole test. A changed output resumes the normal check.

The selector compares observed combinations of pure Boolean operands when a patch changes an isolated condition. For TPC parser changes it attributes different parser outputs to their fixture rows. It checks the executable plan or reruns the original assertions on those rows. Missing attribution retains the test.

The image builds optimizer checkpoints and typed DuckDB tables from the pinned source. For eligible edits, the selector replays the rules reached by each query and compares their output and arguments. For SQL generator changes, it compares the generated SQL and runs unresolved fixture rows using the original assertions.
