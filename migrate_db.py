# migrate_db.py
import sqlite3, os, shutil, json
import numpy as np

DB = "database.db"
SRC_UPLOADS = "uploads"
DST_UPLOADS = os.path.join("static", "uploads")
os.makedirs(DST_UPLOADS, exist_ok=True)

def get_tables(c):
    return [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

def get_columns(c, table):
    return [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]

def run_migration():
    if not os.path.exists(DB):
        print("No database file found at", DB)
        return

    conn = sqlite3.connect(DB)
    c = conn.cursor()

    tables = get_tables(c)
    # create persons table if missing (rare)
    if "persons" not in tables:
        print("Creating missing 'persons' table...")
        c.execute("""
            CREATE TABLE persons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                rollno TEXT,
                mobile TEXT,
                branch TEXT,
                section TEXT,
                image_filename TEXT
            )
        """)
        conn.commit()
    cols = get_columns(c, "persons")
    print("persons columns before:", cols)

    # Add missing columns to persons
    for col in ("rollno", "mobile", "branch", "section", "image_filename"):
        if col not in cols:
            print("Adding column:", col)
            c.execute(f"ALTER TABLE persons ADD COLUMN {col} TEXT")
    conn.commit()

    # Ensure encodings table exists
    if "encodings" not in tables:
        print("Creating encodings table...")
        c.execute("""
            CREATE TABLE encodings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id INTEGER,
                encoding TEXT
            )
        """)
        conn.commit()

    # Move files from old 'uploads/' -> static/uploads/ if present
    if os.path.isdir(SRC_UPLOADS):
        for f in os.listdir(SRC_UPLOADS):
            src = os.path.join(SRC_UPLOADS, f)
            dst = os.path.join(DST_UPLOADS, f)
            if os.path.isfile(src) and not os.path.exists(dst):
                print("Moving", src, "->", dst)
                try:
                    shutil.move(src, dst)
                except Exception as e:
                    print("Move failed:", e)

    # If persons has image_path column, migrate values to image_filename
    cols = get_columns(c, "persons")
    if "image_path" in cols:
        rows = c.execute("SELECT id, image_path FROM persons WHERE image_path IS NOT NULL").fetchall()
        for pid, ipath in rows:
            if ipath:
                fname = os.path.basename(ipath)
                possible_old = os.path.join(SRC_UPLOADS, fname)
                possible_new = os.path.join(DST_UPLOADS, fname)
                if os.path.exists(possible_old) and not os.path.exists(possible_new):
                    try:
                        shutil.move(possible_old, possible_new)
                    except Exception as e:
                        print("Could not move", possible_old, e)
                # Now update DB image_filename
                c.execute("UPDATE persons SET image_filename = ? WHERE id = ?", (fname, pid))
        conn.commit()
        print("Migrated image_path -> image_filename")

    # If persons has a legacy encoding BLOB, move to encodings table
    cols = get_columns(c, "persons")
    if "encoding" in cols:
        rows = c.execute("SELECT id, encoding FROM persons WHERE encoding IS NOT NULL").fetchall()
        for pid, enc_blob in rows:
            if enc_blob:
                try:
                    arr = np.frombuffer(enc_blob, dtype=np.float64)
                    enc_json = json.dumps(arr.tolist())
                    c.execute("INSERT INTO encodings (person_id, encoding) VALUES (?,?)", (pid, enc_json))
                except Exception as e:
                    print("Failed migrating encoding for person", pid, e)
        conn.commit()
        print("Migrated persons.encoding to encodings table")

    # final
    cols_after = get_columns(c, "persons")
    print("persons columns after:", cols_after)
    conn.close()
    print("Migration finished.")

if __name__ == "__main__":
    run_migration()
