"""Generate a small recognizable input PNG used by edits-API probes."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "inputs" / "test_square_1024.png"


def main() -> None:
    img = Image.new("RGB", (1024, 1024), (40, 80, 160))
    d = ImageDraw.Draw(img)
    # Simple geometric scene so the model has obvious things to "edit".
    d.ellipse((280, 280, 744, 744), fill=(240, 200, 60))  # yellow sun
    d.rectangle((0, 760, 1024, 1024), fill=(50, 130, 70))  # green ground
    d.polygon([(120, 760), (300, 480), (480, 760)], fill=(80, 80, 80))  # gray mountain
    try:
        font = ImageFont.load_default()
        d.text((40, 40), "PROBE INPUT", fill="white", font=font)
    except Exception:  # noqa: BLE001
        pass
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, format="PNG")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
