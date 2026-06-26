"""
Degrade Invoice 4 into a realistic low-quality scan.
Converts the clean PDF -> image -> applies blur/rotation/noise/low-res ->
saves back as a PDF that LOOKS like a poorly scanned document.
This is what makes the "low extraction confidence" edge case genuine
rather than just claimed.
"""

from pdf2image import convert_from_path
from PIL import Image, ImageFilter, ImageEnhance
import random

INPUT_PDF = "invoices/invoice_4_scanned_clean_version.pdf"
OUTPUT_PDF = "invoices/invoice_4_scanned_lowqual.pdf"

# Step 1: render PDF page to a high-res image first
pages = convert_from_path(INPUT_PDF, dpi=150)
img = pages[0].convert("RGB")

# Step 2: downscale to simulate a low-res scanner, then upscale back
small = img.resize((img.width // 3, img.height // 3), Image.BILINEAR)
img = small.resize((img.width, img.height), Image.BILINEAR)

# Step 3: slight rotation, like a crooked scan
img = img.rotate(random.uniform(-3, 3), expand=True, fillcolor="white")

# Step 4: blur (moderate)
img = img.filter(ImageFilter.GaussianBlur(radius=1.1))

# Step 5: reduce contrast + add grey cast (toner-low look)
enhancer = ImageEnhance.Contrast(img)
img = enhancer.enhance(0.7)
enhancer = ImageEnhance.Brightness(img)
img = enhancer.enhance(1.05)

# Step 6: add subtle noise (simulates scanner grain)
import numpy as np
arr = np.array(img).astype(np.int16)
noise = np.random.normal(0, 6, arr.shape).astype(np.int16)
arr = np.clip(arr + noise, 0, 255).astype("uint8")
img = Image.fromarray(arr)

# Step 7: crop the top portion off to simulate invoice number being cut off
w, h = img.size
crop_top = int(h * 0.10)
img = img.crop((0, crop_top, w, h))

# Step 7: save as PDF
img.save(OUTPUT_PDF, "PDF", resolution=100.0)
print(f"Degraded scan saved: {OUTPUT_PDF}")
