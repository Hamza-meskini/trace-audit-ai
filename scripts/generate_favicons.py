"""Generate maximized, high-contrast favicons and logo assets in SVG, PNG, and multi-resolution ICO."""

import base64
import io
import os
import shutil
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance

src_path = "public/logo.png"
if not os.path.isfile(src_path):
    src_path = "public/exec-c7779ddf-191c-494e-8e10-9ee5aa40eb67.png"

orig = Image.open(src_path).convert("RGBA")
arr = np.array(orig)
alpha = arr[:, :, 3]
mask = alpha > 10
ymin, ymax = np.where(mask.any(axis=1))[0][[0, -1]]
xmin, xmax = np.where(mask.any(axis=0))[0][[0, -1]]
emblem = orig.crop((xmin, ymin, xmax + 1, ymax + 1))

# Boost color saturation and contrast slightly for crisp small favicon displays
emblem_enhanced = ImageEnhance.Color(emblem).enhance(1.15)
emblem_enhanced = ImageEnhance.Contrast(emblem_enhanced).enhance(1.1)

# Maximize scale: fill 486px width on 512px tile
canvas_size = 512
target_w = 486
w, h = emblem.size
target_h = int(h * (target_w / w))
resized = emblem_enhanced.resize((target_w, target_h), Image.Resampling.LANCZOS)

tile = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
draw = ImageDraw.Draw(tile)

# Clean crisp white rounded squircle badge (works brilliantly on both dark & light browser tabs)
draw.rounded_rectangle(
    [6, 6, 506, 506],
    radius=110,
    fill=(255, 255, 255, 255),
    outline=(226, 232, 240, 255),
    width=6,
)

px = (canvas_size - target_w) // 2
py = (canvas_size - target_h) // 2
tile.paste(resized, (px, py), resized)

# Save to public and dist/client
for d in ["public", "dist/client"]:
    os.makedirs(d, exist_ok=True)
    
    # 1. Square PNG
    tile.save(f"{d}/favicon.png", format="PNG")
    
    # 2. Multi-resolution ICO
    tile.save(
        f"{d}/favicon.ico",
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )

    # 3. Scalable SVG with embedded high-res tile
    buf = io.BytesIO()
    tile.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="100%" height="100%">\n'
        f'  <image href="data:image/png;base64,{b64}" width="512" height="512"/>\n'
        '</svg>\n'
    )
    with open(f"{d}/favicon.svg", "w", encoding="utf-8") as f:
        f.write(svg)

# Also ensure logo.svg and logo.png are copied to dist/client
if os.path.exists("public/logo.svg"):
    shutil.copy("public/logo.svg", "dist/client/logo.svg")
if os.path.exists("public/logo.png"):
    shutil.copy("public/logo.png", "dist/client/logo.png")

# Remove the temporary upload file if it exists
raw_uploaded = "public/exec-c7779ddf-191c-494e-8e10-9ee5aa40eb67.png"
if os.path.exists(raw_uploaded):
    os.remove(raw_uploaded)
    print(f"Removed temporary uploaded raw file: {raw_uploaded}")

print("Generated new logo & favicon assets successfully!")
