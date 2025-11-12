# face_search.py
import face_recognition
import cv2
import os
import numpy as np
from concurrent.futures import ProcessPoolExecutor, as_completed
import time
import logging
import gc
from threading import Event
from typing import Optional
import shutil
from pathlib import Path
import base64
from io import BytesIO
from PIL import Image

# ---------------- LOGGING ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("FaceSearch")

# ---------------- PREPROCESS ----------------
def preprocess_image(image_path: str, max_size: int = 640):
    """Read and resize image for face encoding (returns RGB array)."""
    try:
        img = cv2.imread(image_path)
        if img is None:
            logger.error(f"❌ Failed to load image: {image_path}")
            return None

        h, w = img.shape[:2]
        if max(h, w) > max_size:
            scale = max_size / max(h, w)
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

        rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        del img
        return rgb_img
    except Exception as e:
        logger.exception(f"Error preprocessing {image_path}: {e}")
        return None

# ---------------- SINGLE IMAGE ----------------
def process_single_image(args):
    """Worker: compare one image to selfie, return (path, similarity)."""
    path, selfie_encoding, exclude_encodings = args
    try:
        img = preprocess_image(path)
        if img is None:
            return None

        encodings = face_recognition.face_encodings(img)
        del img

        if not encodings:
            return None

        # Skip excluded faces
        if exclude_encodings:
            for enc in encodings:
                if np.any(face_recognition.face_distance(exclude_encodings, enc) < 0.5):
                    return None

        distances = face_recognition.face_distance(encodings, selfie_encoding)
        similarity = 1 - float(np.min(distances))
        del encodings
        return (path, similarity)

    except Exception as e:
        logger.exception(f"Error processing {os.path.basename(path)}: {e}")
        return None
    finally:
        gc.collect()

# ---------------- BATCH PROCESS ----------------
def process_image_batch(image_paths, selfie_encoding, exclude_encodings, max_workers=4, batch_size=50, cancelled=None):
    total = len(image_paths)
    results = []
    logger.info(f"Processing {total} images...")

    # Process in batches to limit memory spike
    for i in range(0, total, batch_size):
        if cancelled and cancelled.is_set():
            logger.warning("Search cancelled")
            return results

        batch = image_paths[i:i + batch_size]
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(process_single_image, (path, selfie_encoding, exclude_encodings)) for path in batch]
            for future in as_completed(futures):
                if cancelled and cancelled.is_set():
                    executor.shutdown(wait=False)
                    return results

                try:
                    result = future.result()
                except Exception:
                    logger.exception("Worker raised an exception")
                    result = None

                if result:
                    results.append(result)

        logger.info(f"Progress: {min(i + batch_size, total)}/{total}")
        gc.collect()

    return results

# ---------------- MAIN FUNCTION ----------------
def find_similar_faces(selfie_path: str,
                       cancelled: Optional[Event] = None,
                       top_k: int = 10,
                       output_dir: Optional[str] = None,
                       embed_images: bool = False,
                       thumb_max_width: int = 180):
    """
    Search photos for faces similar to selfie_path.

    If output_dir is provided, top_k matched images are copied there and the function
    returns a list of dicts:
        [ { "original": "<orig path>", "saved": "<saved filename>", "score": 0.923, "data_uri": "data:..." }, ... ]

    If output_dir is None and embed_images=False, returns list of tuples: [(path, score), ...]
    """
    # Folder to search (adjust if needed)
    photos_dir = r"C:\Users\ACER\Downloads\ng_here\search_here"
    exclude_dir = r"C:\Users\ACER\Desktop\js_queue\exclude_faces"

    start_time = time.time()
    logger.info("Starting face search...")

    selfie = preprocess_image(selfie_path)
    if selfie is None:
        raise ValueError("Failed to load selfie image")

    selfie_faces = face_recognition.face_encodings(selfie)
    del selfie
    if not selfie_faces:
        raise ValueError("No face found in selfie image")
    selfie_encoding = selfie_faces[0]

    valid_ext = ('.jpg', '.jpeg', '.png')
    photos = [os.path.join(root, f)
              for root, _, files in os.walk(photos_dir)
              for f in files if f.lower().endswith(valid_ext)]

    logger.info(f"Found {len(photos)} images in {photos_dir}")

    # Load excluded faces if any
    exclude_encodings = []
    if exclude_dir:
        exclude_files = [os.path.join(root, f)
                         for root, _, files in os.walk(exclude_dir)
                         for f in files if f.lower().endswith(valid_ext)]
        for img_path in exclude_files:
            img = preprocess_image(img_path)
            if img is not None:
                encs = face_recognition.face_encodings(img)
                exclude_encodings.extend(encs)
                del img
        logger.info(f"Loaded {len(exclude_encodings)} exclude encodings")

    matches = process_image_batch(photos, selfie_encoding, exclude_encodings, cancelled=cancelled)
    matches = sorted(matches, key=lambda x: x[1], reverse=True)[:top_k]

    logger.info(f"Done in {time.time() - start_time:.2f} seconds")

    # If no output_dir requested and not embedding, return as before
    if not output_dir and not embed_images:
        return matches

    # Ensure output_dir exists
    outp = Path(output_dir)
    outp.mkdir(parents=True, exist_ok=True)

    saved_results = []
    for idx, (orig_path, score) in enumerate(matches):
        try:
            orig = Path(orig_path)
            # Create a safe filename to avoid collisions
            suffix = orig.suffix
            saved_name = f"match_{int(time.time())}_{idx}{suffix}"
            saved_path = outp / saved_name

            # Copy file (preserves original)
            shutil.copy2(str(orig), str(saved_path))

            result_item = {
                "original": str(orig.resolve()),
                "saved": saved_name,            # relative filename inside output_dir
                "saved_path": str(saved_path.resolve()),
                "score": float(score)
            }

            # If embedding requested, create small thumbnail and encode as base64 data URI
            if embed_images:
                try:
                    img = Image.open(str(saved_path))
                    # Create thumbnail preserving aspect ratio
                    w, h = img.size
                    if w > thumb_max_width:
                        new_h = int((thumb_max_width / float(w)) * h)
                        img = img.resize((thumb_max_width, new_h), Image.LANCZOS)

                    buf = BytesIO()
                    save_format = 'PNG' if suffix.lower() == '.png' else 'JPEG'
                    img.save(buf, format=save_format, quality=85, optimize=True)
                    buf.seek(0)
                    b64 = base64.b64encode(buf.read()).decode('ascii')
                    mime = 'image/png' if save_format == 'PNG' else 'image/jpeg'
                    data_uri = f"data:{mime};base64,{b64}"

                    result_item['data_uri'] = data_uri
                    result_item['thumb_width'], result_item['thumb_height'] = img.size
                    buf.close()
                    img.close()
                except Exception as e:
                    logger.exception(f"Failed to embed image for {saved_path}: {e}")

            saved_results.append(result_item)
        except Exception:
            logger.exception(f"Failed to copy match {orig_path}")

    return saved_results

# ---------------- RUN EXAMPLE ----------------
if __name__ == "__main__":
    selfie_path = r"C:\Users\ACER\Downloads\ng_here\selfie.jpg"   # <- Change this to your selfie image
    results = find_similar_faces(selfie_path, top_k=5, output_dir=r".\static\results", embed_images=True)
    print("\nTop matches:")
    for r in results:
        print(r.get("saved"), r.get("score"))
