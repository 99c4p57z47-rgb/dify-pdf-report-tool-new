#!/usr/bin/env python3
"""Create compact visual-QA contact sheets for the packaged assets."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = ROOT / "assets"


def main() -> None:
    records = json.loads((ASSET_ROOT / "manifest.json").read_text(encoding="utf-8"))["assets"]
    output = ROOT / "qa"
    output.mkdir(exist_ok=True)
    font = ImageFont.load_default()
    for sheet_number, offset in enumerate(range(0, len(records), 25), 1):
        canvas = Image.new("RGB", (2000, 1500), "white")
        draw = ImageDraw.Draw(canvas)
        for local_index, record in enumerate(records[offset : offset + 25]):
            row, column = divmod(local_index, 5)
            x, y = column * 400, row * 300
            with Image.open(ASSET_ROOT / record["path"]) as source:
                tile = ImageOps.contain(source.convert("RGB"), (370, 245))
            canvas.paste(tile, (x + (400 - tile.width) // 2, y + 8))
            draw.text((x + 12, y + 262), record["asset_id"], fill="#20252B", font=font)
        canvas.save(output / f"contact_sheet_{sheet_number:02d}.jpg", quality=88, optimize=True)
    print(f"Wrote {(len(records) + 24) // 25} contact sheets to {output}")


if __name__ == "__main__":
    main()
