import tempfile
import shutil
import os
import base64
import mimetypes
from pathlib import Path

from flask import Flask, render_template, request, jsonify, current_app
from werkzeug.utils import secure_filename

# Import your face search implementation
from face_searching import find_similar_faces

# -------- CONFIG --------
BASE_DIR = Path(__file__).parent.resolve()
UPLOADS_DIR = BASE_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

ALLOWED_EXT = {".jpg", ".jpeg", ".png"}

app = Flask(__name__, static_folder=str(BASE_DIR / "static"), template_folder=str(BASE_DIR / "templates"))
app.secret_key = "change-this-secret"  # change in production


def allowed_file(filename):
    return filename and Path(filename).suffix.lower() in ALLOWED_EXT


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/api/search", methods=["POST"])
def api_search():
    if "selfie" not in request.files:
        return jsonify({"error": "No selfie file provided"}), 400

    selfie_file = request.files["selfie"]
    if selfie_file.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    if not allowed_file(selfie_file.filename):
        return jsonify({"error": "Unsupported file type"}), 400

    # create temporary workspace for uploaded selfie and any temporary result files
    tmp_upload_dir = Path(tempfile.mkdtemp(dir=str(UPLOADS_DIR)))
    tmp_results_dir = Path(tempfile.mkdtemp(dir=str(UPLOADS_DIR)))
    try:
        # save selfie temporarily
        filename = secure_filename(selfie_file.filename)
        saved_selfie = tmp_upload_dir / filename
        selfie_file.save(str(saved_selfie))
        

        # parse top_k
        try:
            top_k = int(request.form.get("top_k", 5))
        except Exception:
            top_k = 5

        # Call find_similar_faces:
        # - request embed_images=True so function may return data_uri directly if supported
        # - pass tmp_results_dir so any created files are temporary
        saved_results = find_similar_faces(
            str(saved_selfie),
            top_k=top_k,
            output_dir=str(tmp_results_dir),
            embed_images=True,
            thumb_max_width=160
        )

        matches = []
        for r in saved_results:
            # r may contain: 'saved' (filename), 'score', 'original', 'data_uri' (if supported)
            score = r.get("score")
            original = r.get("original")

            # Prefer returned data_uri if present
            data_uri = r.get("data_uri")
            if not data_uri:
                # try to get saved filename and read + convert to data URI
                saved_name = r.get("saved")
                if saved_name:
                    file_path = tmp_results_dir / saved_name
                    if not file_path.exists():
                        # maybe function returned an absolute path
                        alt = Path(saved_name)
                        if alt.exists():
                            file_path = alt
                    if file_path.exists():
                        b = file_path.read_bytes()
                        mime = mimetypes.guess_type(str(file_path))[0] or "image/jpeg"
                        data_uri = f"data:{mime};base64," + base64.b64encode(b).decode("ascii")

            # If still not available, skip this match (or you could return partial info)
            if not data_uri:
                continue

            matches.append({
                "original": original,
                "score": score,
                "data_uri": data_uri
            })

        return jsonify({"matches": matches}), 200

    except Exception as e:
        current_app.logger.exception("API search failed")
        return jsonify({"error": str(e)}), 500

    finally:
        # cleanup temp directories
        try:
            shutil.rmtree(str(tmp_upload_dir))
        except Exception:
            pass
        try:
            shutil.rmtree(str(tmp_results_dir))
        except Exception:
            pass


if __name__ == "__main__":
    UPLOADS_DIR.mkdir(exist_ok=True)
    app.run(host="127.0.0.1", port=5000, debug=True)
