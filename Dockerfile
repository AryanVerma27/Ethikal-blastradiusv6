FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
WORKDIR /app
ADD https://codeload.github.com/tobymao/sqlglot/tar.gz/eb11aec4be8cb2122afe713a4816662c3f0fb2fa /tmp/sqlglot.tar.gz
RUN mkdir /src && tar -xzf /tmp/sqlglot.tar.gz --strip-components=1 -C /src && rm /tmp/sqlglot.tar.gz
COPY requirements.txt .
RUN pip install --disable-pip-version-check --no-cache-dir -r requirements.txt && python -m compileall -q /src/sqlglot /src/tests
COPY selector.py probe.py cases.py stages.py guards.py truth_sites.py truth.zip branches.py build_map.py warm.py fragments.py calls.py boundary.py engine.py stress.py profile_store.py table_fixtures.py rule_replay.py query-index.json parse-rows.json rule-lines.z rules.zip build_parser_digests.py build_plans.py sql_outputs.py sql-outputs.zip checkpoints.py build_checkpoints.py profiles.zip stress.json ./
ENV PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 SKIP_INTEGRATION=0
RUN python table_fixtures.py /src && python build_parser_digests.py /src parser-digests.json && python build_plans.py /src execution-keys.json && python build_checkpoints.py /src optimizer-steps.zip
CMD ["python", "/app/selector.py"]
