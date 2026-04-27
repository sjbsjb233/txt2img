from fastapi import FastAPI

app = FastAPI(title="txt2img backend", version="0.0.0")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}
