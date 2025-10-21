# migrate_images.py  -- run once from project folder
import os, sqlite3, shutil
DB = "database.db"
SRC_DIR = "uploads"
DST_DIR = os.path.join("static","uploads")
os.makedirs(DST_DIR, exist_ok=True)

conn = sqlite3.connect(DB)
c = conn.cursor()

# Move files from old uploads to static/uploads and update DB
if os.path.isdir(SRC_DIR):
    for f in os.listdir(SRC_DIR):
        oldp = os.path.join(SRC_DIR, f)
        newp = os.path.join(DST_DIR, f)
        if os.path.isfile(oldp):
            if not os.path.exists(newp):
                shutil.move(oldp, newp)
            # update any persons image_path or image_filename column
            c.execute("UPDATE persons SET image_filename = ? WHERE image_filename = ? OR image_filename = ?",
                      (f, os.path.join(SRC_DIR,f), f))
conn.commit()
conn.close()
print("Migration done. Check static/uploads/ and database.db")
