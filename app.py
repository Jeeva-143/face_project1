# app.py  <-- REPLACE your existing app.py with this file
import os
import sqlite3
import json
import base64
import uuid
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from werkzeug.utils import secure_filename
import face_recognition
import numpy as np

# ---------------- CONFIG ----------------
STATIC_UPLOADS = os.path.join("static", "uploads")
DB_FILE = "database.db"
FACE_MODEL = "hog"        # "hog" (fast CPU) or "cnn" (more accurate, slower, needs proper dlib)
TOLERANCE = 0.48          # start ~0.48 ; tune between 0.40 and 0.60
ALLOWED_EXT = {".jpg", ".jpeg", ".png"}
# ----------------------------------------

app = Flask(__name__)
app.secret_key = "change_this_secret"
os.makedirs(STATIC_UPLOADS, exist_ok=True)


# ---------- Database init & (safe) migration ----------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # persons basic info with image_filename (we will store only filename)
    c.execute("""
        CREATE TABLE IF NOT EXISTS persons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            rollno TEXT,
            mobile TEXT,
            branch TEXT,
            section TEXT,
            image_filename TEXT
        )""")
    # encodings: multiple encodings per person (JSON stored)
    c.execute("""
        CREATE TABLE IF NOT EXISTS encodings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER,
            encoding TEXT,
            FOREIGN KEY(person_id) REFERENCES persons(id)
        )""")
    conn.commit()

    # Migration for old DB layout (optional):
    # If there is an old persons.encoding BLOB column or old uploads folder, try to migrate them safely.
    # - Move files from 'uploads/' to 'static/uploads/' (if that folder exists)
    # - If persons table has image_path column with full path, normalize to filename
    # - If persons table has legacy 'encoding' BLOB column, try to move it to encodings table
    try:
        # Move files
        old_upload_dir = "uploads"
        if os.path.isdir(old_upload_dir):
            for f in os.listdir(old_upload_dir):
                oldp = os.path.join(old_upload_dir, f)
                if os.path.isfile(oldp):
                    newp = os.path.join(STATIC_UPLOADS, f)
                    if not os.path.exists(newp):
                        os.replace(oldp, newp)
            # (optionally) remove old folder if empty
            try:
                os.rmdir(old_upload_dir)
            except OSError:
                pass

        # Normalize image filenames in persons table if a column exists named image_path
        cols = [r[1] for r in c.execute("PRAGMA table_info(persons)").fetchall()]
        if "image_path" in cols:
            rows = c.execute("SELECT id, image_path FROM persons").fetchall()
            for pid, ipath in rows:
                if ipath:
                    fname = os.path.basename(ipath)
                    # if file exists in static/uploads keep it; otherwise try to move
                    possible_old = os.path.join("uploads", fname)
                    possible_new = os.path.join(STATIC_UPLOADS, fname)
                    if os.path.exists(possible_old) and not os.path.exists(possible_new):
                        os.replace(possible_old, possible_new)
                    # update DB to new filename (if not already)
                    c.execute("UPDATE persons SET image_filename = ? WHERE id = ?", (fname, pid))
            # commit if updated
            conn.commit()

        # Migrate legacy persons.encoding blob -> encodings table
        if "encoding" in cols:
            # check encodings table empty -> perform migration
            count = c.execute("SELECT COUNT(*) FROM encodings").fetchone()[0]
            if count == 0:
                rows = c.execute("SELECT id, encoding FROM persons WHERE encoding IS NOT NULL").fetchall()
                for pid, enc_blob in rows:
                    if enc_blob:
                        try:
                            arr = np.frombuffer(enc_blob, dtype=np.float64)
                            enc_json = json.dumps(arr.tolist())
                            c.execute("INSERT INTO encodings (person_id, encoding) VALUES (?,?)", (pid, enc_json))
                        except Exception as ex:
                            print("Migration error for person", pid, ex)
                conn.commit()
                print("[migrate] moved legacy persons.encoding into encodings table.")
    except Exception as ex:
        print("Migration check error:", ex)
    finally:
        conn.close()


init_db()


# ---------- Helpers ----------
def allowed_filename(filename):
    _, ext = os.path.splitext(filename.lower())
    return ext in ALLOWED_EXT

def save_file_to_static(fs):
    """Save a Werkzeug FileStorage into static/uploads and return filename (not full path)."""
    filename = secure_filename(fs.filename or (str(uuid.uuid4()) + ".jpg"))
    if not allowed_filename(filename):
        # change extension to .jpg if unknown
        filename = filename + ".jpg"
    unique = f"{uuid.uuid4().hex}_{filename}"
    full = os.path.join(STATIC_UPLOADS, unique)
    fs.save(full)
    return unique

def save_base64_to_static(dataurl):
    """Save data URL (data:image/jpeg;base64,...) into static/uploads and return filename."""
    header, b64 = dataurl.split(",", 1) if "," in dataurl else (None, dataurl)
    img_bytes = base64.b64decode(b64)
    unique = f"{uuid.uuid4().hex}.jpg"
    full = os.path.join(STATIC_UPLOADS, unique)
    with open(full, "wb") as f:
        f.write(img_bytes)
    return unique

def compute_first_encoding_from_file(filename):
    full = os.path.join(STATIC_UPLOADS, filename)
    img = face_recognition.load_image_file(full)
    encs = face_recognition.face_encodings(img, model=FACE_MODEL)
    if not encs:
        return None
    return encs[0]


# ---------- Routes ----------
@app.route("/")
def home():
    return render_template("base.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    """
    Accept:
     - uploaded files input name="images" (multiple allowed)
     - captured images as hidden JSON input name="capturedImages" (list of dataURLs)
     - form fields: name, rollno, mobile, branch, section
    Stores:
     - persons.image_filename = filename (first image)
     - encodings table receives one row per image encoding (JSON text)
    """
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        rollno = request.form.get("rollno", "").strip()
        mobile = request.form.get("mobile", "").strip()
        branch = request.form.get("branch", "").strip()
        section = request.form.get("section", "").strip()

        uploaded_files = request.files.getlist("images")
        captured_images_json = request.form.get("capturedImages")
        captured = json.loads(captured_images_json) if captured_images_json else []

        saved_filenames = []

        # Save uploaded files
        for fs in uploaded_files:
            if fs and fs.filename:
                fname = save_file_to_static(fs)
                saved_filenames.append(fname)

        # Save captured base64 frames
        for dataurl in captured:
            try:
                fname = save_base64_to_static(dataurl)
                saved_filenames.append(fname)
            except Exception as e:
                print("Failed saving base64 image:", e)

        if not saved_filenames:
            flash("No images provided. Upload or capture at least one image.", "danger")
            return redirect(url_for("register"))

        # Insert person (use first saved filename as display picture)
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("INSERT INTO persons (name, rollno, mobile, branch, section, image_filename) VALUES (?,?,?,?,?,?)",
                  (name, rollno, mobile, branch, section, saved_filenames[0]))
        person_id = c.lastrowid

        # Compute encodings for each saved file and insert
        enc_count = 0
        for fname in saved_filenames:
            enc = compute_first_encoding_from_file(fname)
            if enc is not None:
                c.execute("INSERT INTO encodings (person_id, encoding) VALUES (?,?)", (person_id, json.dumps(enc.tolist())))
                enc_count += 1
            else:
                print("[register] no face found in", fname)

        conn.commit()
        conn.close()

        flash(f"Registered {name} with {enc_count} face encodings.", "success")
        return redirect(url_for("list_persons"))

    return render_template("register.html")


@app.route("/list")
def list_persons():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, name, rollno, mobile, branch, section, image_filename FROM persons")
    rows = c.fetchall()
    conn.close()
    # rows is list of tuples: (id,name,rollno,mobile,branch,section,image_filename)
    return render_template("list.html", persons=rows)


@app.route("/recognize", methods=["GET", "POST"])
def recognize():
    """
    POST: expects form-data with 'image' file OR 'image' as a single dataURL
    Returns JSON:
      { "match": true|false, "distance": 0.412, "person": { ... }, "image_b64": "..." }
    """
    if request.method == "POST":
        # Accept file or form data
        probe_filename = None
        if "image" in request.files and request.files["image"].filename:
            probe_filename = save_file_to_static(request.files["image"])
        elif "image" in request.form and request.form["image"]:
            probe_filename = save_base64_to_static(request.form["image"])
        else:
            return jsonify({"match": False, "msg": "No image provided"}), 400

        probe_enc = compute_first_encoding_from_file(probe_filename)
        if probe_enc is None:
            return jsonify({"match": False, "msg": "No face detected in probe image"}), 400

        # Load all encodings and compute distances
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("""
            SELECT encodings.person_id, encodings.encoding, persons.name, persons.rollno, persons.mobile,
                   persons.branch, persons.section, persons.image_filename
            FROM encodings JOIN persons ON encodings.person_id = persons.id
        """)
        rows = c.fetchall()
        conn.close()

        if not rows:
            return jsonify({"match": False, "msg": "No registered persons"}), 200

        # compute min distance per person
        best = {}  # person_id -> (min_distance, details)
        for person_id, enc_json, name, rollno, mobile, branch, section, image_filename in rows:
            try:
                db_enc = np.array(json.loads(enc_json), dtype=np.float64)
            except Exception as e:
                print("Invalid DB encoding for person", person_id, e)
                continue
            dist = float(np.linalg.norm(probe_enc - db_enc))
            # debug
            print(f"[compare] person_id={person_id} name={name} dist={dist:.4f}")
            if person_id not in best or dist < best[person_id][0]:
                best[person_id] = (dist, {
                    "person_id": person_id, "name": name, "rollno": rollno,
                    "mobile": mobile, "branch": branch, "section": section,
                    "image_filename": image_filename
                })

        # pick global best
        best_person_id, best_distance, best_details = None, None, None
        for pid, (dist, details) in best.items():
            if best_distance is None or dist < best_distance:
                best_person_id, best_distance, best_details = pid, dist, details

        print(f"[result] best_person_id={best_person_id} dist={best_distance:.4f}")

        if best_distance is not None and best_distance < TOLERANCE:
            # prepare image base64 for response
            img_path = os.path.join(STATIC_UPLOADS, best_details["image_filename"]) if best_details["image_filename"] else None
            img_b64 = None
            if img_path and os.path.exists(img_path):
                with open(img_path, "rb") as f:
                    img_b64 = base64.b64encode(f.read()).decode("utf-8")
            response = {
                "match": True, "distance": best_distance,
                "person": {k: v for k, v in best_details.items() if k != "image_filename"},
                "image_b64": img_b64,
                "image_url": url_for('static', filename=f"uploads/{best_details['image_filename']}") if best_details.get("image_filename") else None
            }
            return jsonify(response), 200
        else:
            return jsonify({"match": False, "best_distance": best_distance, "msg": "No match"}), 200

    # GET (render page)
    return render_template("recognize.html")


if __name__ == "__main__":
    init_db()
    print("[config] FACE_MODEL=", FACE_MODEL, "TOLERANCE=", TOLERANCE)
    app.run(debug=True)
