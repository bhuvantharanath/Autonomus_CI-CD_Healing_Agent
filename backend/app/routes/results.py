"""Results endpoints – read/write shared results.json."""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

RESULTS_PATH = Path(__file__).resolve().parents[3] / "shared" / "results.json"


@router.get("/")
async def get_results():
    """Return the current contents of results.json."""
    if not RESULTS_PATH.exists():
        return {"runs": []}
    return json.loads(RESULTS_PATH.read_text())


@router.get("/{job_id}")
async def get_result_by_job(job_id: str):
    """Return results for a specific job."""
    if not RESULTS_PATH.exists():
        raise HTTPException(status_code=404, detail="No results found")
    data = json.loads(RESULTS_PATH.read_text())
    for run in data.get("runs", []):
        if run.get("job_id") == job_id:
            return run
    raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
