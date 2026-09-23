FROM python:3.12-alpine

WORKDIR /app
RUN pip install --no-cache-dir earthengine-api google-auth numpy pandas
COPY monitor.py ./
COPY authorize.py ./
COPY initialize.py ./
COPY sample_latest.py ./
COPY backfill_langtang.py ./
COPY validate_langtang.py ./
COPY score_langtang.py ./
COPY validate_scores.py ./
COPY build_langtang_research_state.py ./
COPY validate_research_state.py ./
COPY audit_langtang_research_state.py ./
COPY validate_audit.py ./
COPY build_langtang_research_state_v2.py ./
COPY audit_langtang_research_state_v2.py ./
COPY validate_research_state_v2.py ./
COPY diagnose_platform_coverage.py ./
COPY build_langtang_orbit_state.py ./
COPY publish_langtang_snapshot.py ./
COPY himalaya_batch.py ./
COPY himalaya_pipeline.py ./

USER nobody
ENTRYPOINT ["python", "-B", "monitor.py"]
