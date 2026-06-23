import os
import sys
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Ensure we can import from tasks
sys.path.insert(0, os.getcwd())

load_dotenv()

# Setup database connection
_raw_url = os.getenv("SYNC_DATABASE_URL") or os.getenv("DATABASE_URL")
if not _raw_url:
    from urllib.parse import quote
    user = os.getenv("POSTGRES_USER", "postgres")
    password = quote(os.getenv("POSTGRES_PASSWORD", "postgres"), safe="")
    db = os.getenv("POSTGRES_DB", "domain_db")
    _raw_url = f"postgresql://{user}:{password}@localhost:5432/{db}"
DATABASE_URL = _raw_url.replace("postgresql+asyncpg://", "postgresql://")

engine = create_engine(DATABASE_URL)

doc_id = "6112631c-9309-419d-ac41-d1c09e8c154b"
domain_id = "11111111-1111-1111-1111-111111111111"
user_id = "user"
filename = "2025 Form W-4.pdf"
file_path = r"D:\Personal\Fixed Solutions\Project Files\v5\data\uploads\6112631c-9309-419d-ac41-d1c09e8c154b\2025 Form W-4.pdf"

# 1. Insert document metadata into database
with engine.begin() as conn:
    # Delete old chunks or document record if any (to make it repeatable)
    conn.execute(text("DELETE FROM document_chunks WHERE document_id = :id"), {"id": doc_id})
    conn.execute(text("DELETE FROM documents WHERE id = :id"), {"id": doc_id})
    
    # Insert fresh document record
    conn.execute(
        text("""
            INSERT INTO documents (id, domain_id, user_id, filename, file_path, status)
            VALUES (:id, :domain_id, :user_id, :filename, :file_path, 'pending')
        """),
        {
            "id": doc_id,
            "domain_id": domain_id,
            "user_id": user_id,
            "filename": filename,
            "file_path": file_path
        }
    )

print("Inserted document record into PostgreSQL.")

# 2. Run the process_document_sync task
from tasks.process import process_document_sync
res = process_document_sync(doc_id)
print("Processing result:", res)

# 3. Print the generated chunks from PostgreSQL
print("\n--- Chunks in PostgreSQL ---")
with engine.connect() as conn:
    rows = conn.execute(
        text("SELECT id, page_num, chunk_index, chunk_type, text FROM document_chunks WHERE document_id = :id ORDER BY chunk_index"),
        {"id": doc_id}
    ).fetchall()
    for row in rows:
        print(f"Index: {row.chunk_index} | Page: {row.page_num} | Type: {row.chunk_type or 'text'}")
        print(f"Content:\n{row.text}")
        print("-" * 50)
