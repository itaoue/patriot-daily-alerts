"""Resize and center-crop an image to exactly W x H (cover), using Pillow."""
from PIL import Image, ImageOps


def fit(path, width=1200, height=630, anchor=0.5, out=None):
    img = Image.open(path)
    img = ImageOps.exif_transpose(img).convert("RGB")
    scale = max(width / img.width, height / img.height)
    img = img.resize((max(width, round(img.width * scale)), max(height, round(img.height * scale))), Image.LANCZOS)
    left = (img.width - width) // 2
    top = round((img.height - height) * anchor)
    img = img.crop((left, top, left + width, top + height))
    img.save(out or path, "JPEG", quality=86, optimize=True, progressive=True)
    return out or path
