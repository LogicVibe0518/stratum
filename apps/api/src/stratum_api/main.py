from fastapi import FastAPI
from stratum_spec import SCHEMA_VERSION

app = FastAPI(title="Stratum API", version="0.0.1")


@app.get("/healthz")
def healthz() -> dict[str, object]:
    return {"status": "ok", "spec_schema_version": SCHEMA_VERSION}
