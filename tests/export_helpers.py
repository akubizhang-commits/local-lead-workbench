"""Export through the background job API (the synchronous export endpoint was removed, design §10.2)."""
import time


def export_job_file(client, timeout=30, **params):
    """POST /api/export-jobs, poll until succeeded (at most ``timeout`` s), return the file response.

    Defaults match the removed synchronous export: CSV, companies layout."""
    body = {"format": "csv", "layout": "companies", **params}
    response = client.post("/api/export-jobs", json=body)
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    deadline = time.monotonic() + timeout
    while True:
        job = client.get(f"/api/export-jobs/{job_id}").json()
        if job["status"] == "succeeded":
            break
        assert job["status"] in {"queued", "running"}, job
        assert time.monotonic() < deadline, f"export job {job_id} did not finish: {job}"
        time.sleep(0.1)
    file = client.get(f"/api/export-jobs/{job_id}/file")
    assert file.status_code == 200, file.text
    return file


def export_via_jobs(client, timeout=30, **params) -> bytes:
    return export_job_file(client, timeout, **params).content
