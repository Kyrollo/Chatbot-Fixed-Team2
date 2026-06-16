import sys
from pathlib import Path
from sqlalchemy import text

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "services" / "worker-service"))

from tasks.index import _engine, _ensure_chunk_table

if __name__ == "__main__":
    print("Running database migrations...")
    
    # 1. Bootstraps document_chunks table if not exists
    _ensure_chunk_table()
    
    # 2. Add columns to documents and document_chunks if they don't exist
    with _engine.connect() as conn:
        print("  Checking and adding documents.task_id...")
        conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS task_id TEXT;"))
        
        print("  Checking and adding document_chunks.chunk_type...")
        conn.execute(text("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS chunk_type TEXT DEFAULT 'text';"))
        
        print("  Checking and adding document_chunks.filename...")
        conn.execute(text("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS filename TEXT DEFAULT '';"))
        
        conn.commit()
        
    print("Migration complete.")
