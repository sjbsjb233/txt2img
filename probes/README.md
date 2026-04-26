# bltcy.ai image-API probes

Lightweight probes that exercise the `https://api.bltcy.ai` relay and report
whether requested parameters (model, size, aspect ratio, quality) are actually
honored by the upstream models.

## Layout

- `probe_lib.py` — shared HTTP helpers, success-budget enforcement, image-decode/measure utilities, and JSON reporting.
- `probe_nanobanana.py` — Gemini-native `POST /v1beta/models/{model}:generateContent` probes. Tests `gemini-2.5-flash-image` (Nano Banana), `gemini-3-pro-image-preview` (Pro), `gemini-3.1-flash-image-preview` (Nano Banana 2). Varies `aspectRatio` and `imageSize`, plus a multi-modal edit (input image + prompt -> image).
- `probe_gpt_image_2.py` — OpenAI-native `POST /v1/images/generations` and `POST /v1/images/edits` probes for `gpt-image-2` (with fallback to `gpt-image-1.5` / `gpt-image-1`). Varies `size` (1024×1024, 1024×1536, 2048×2048, 4096×4096) and `quality`.
- `make_input_image.py` — regenerates the small `inputs/test_square_1024.png` used by the edit probes.
- `outputs/report_*.json`, `outputs/*_run.log` — last run's structured results and console log.

## Running

Keys are read from environment variables; nothing is committed.

```bash
export BLTCY_NANOBANANA_KEY=sk-...
export BLTCY_GPT_IMAGE_KEY=sk-...
# Optional: change the label that appears in the report and saved-file names.
export BLTCY_GPT_IMAGE_LABEL=gpt_image_2_key3

python3 probe_nanobanana.py
python3 probe_gpt_image_2.py
```

Each script enforces a hard cap of 5 *successful* image deliveries per key.
Failures (HTTP 4xx/5xx, no image bytes returned) do **not** count toward the
cap because the relay does not bill on those.

Generated images land in `outputs/` (gitignored). The JSON report records the
requested parameters, what the relay echoed back, the actual decoded
width/height, and a `match` block flagging any mismatch versus the requested
size.
