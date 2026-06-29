import os
import sys
from pathlib import Path
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent

def split_sql_statements(sql: str) -> list[str]:
    """Split SQL script into individual statements, respecting dollar-quoted blocks."""
    statements = []
    current = []
    in_dollar_quote = False
    for line in sql.splitlines():
        stripped = line.strip()
        if not in_dollar_quote and (not stripped or stripped.startswith("--")):
            continue
        
        if "$$" in line:
            in_dollar_quote = not in_dollar_quote
            
        current.append(line)
        if not in_dollar_quote and stripped.endswith(";"):
            statements.append("\n".join(current))
            current = []
            
    if current:
        stmt = "\n".join(current).strip()
        if stmt:
            statements.append(stmt)
    return statements

def get_engine(url: str):
    DATABASE_URL = url.replace("postgresql+asyncpg://", "postgresql://")
    return create_engine(DATABASE_URL)

def is_age_statement(stmt_lower: str) -> bool:
    """Check if a statement is specifically related to Apache AGE and not just containing the substring 'age'."""
    if "ag_catalog" in stmt_lower or "rag_graph" in stmt_lower:
        return True
    if "extension" in stmt_lower and "age" in stmt_lower:
        return True
    if "load" in stmt_lower and "'age'" in stmt_lower:
        return True
    return False

def run_sql_on_db(url: str, sql_content: str, label: str):
    print(f"Connecting to {label} database: {url.partition('@')[-1]}")  # Hide credentials
    try:
        engine = get_engine(url)
        with engine.begin() as conn:
            # Check if Apache AGE is available
            has_age = False
            try:
                has_age = conn.execute(text("SELECT EXISTS(SELECT 1 FROM pg_available_extensions WHERE name = 'age')")).scalar()
            except Exception:
                pass
            
            print(f"  AGE extension available: {has_age}")
            
            statements = split_sql_statements(sql_content)
            executed_count = 0
            
            for stmt in statements:
                stmt_lower = stmt.lower()
                if not has_age and is_age_statement(stmt_lower):
                    continue
                
                conn.execute(text(stmt))
                executed_count += 1
            print(f"  {label} migration complete ({executed_count} statements executed).")
    except Exception as e:
        print(f"  Skipping {label} database or handled error: {e}")
        if label == "Relational":
            sys.exit(1)

if __name__ == "__main__":
    setup_sql_path = ROOT / "migrations" / "setup_all.sql"
    if not setup_sql_path.exists():
        print(f"Error: {setup_sql_path} does not exist.")
        sys.exit(1)
        
    sql_content = setup_sql_path.read_text(encoding="utf-8")
    
    clear_sql_path = ROOT / "migrations" / "clear_db.sql"
    clear_content = None
    if clear_sql_path.exists():
        print(f"Found reset script {clear_sql_path}. Clearing databases first...")
        clear_content = clear_sql_path.read_text(encoding="utf-8")
    
    # Get database URLs
    rel_url = os.getenv("SYNC_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not rel_url:
        from urllib.parse import quote
        user = os.getenv("POSTGRES_USER", "postgres")
        password = quote(os.getenv("POSTGRES_PASSWORD", "postgres"), safe="")
        db = os.getenv("POSTGRES_DB", "domain_db")
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_PORT", "5434")
        rel_url = f"postgresql://{user}:{password}@{host}:{port}/{db}"
        
    age_url = os.getenv("AGE_DATABASE_DSN")
    
    # 1. Clear databases if reset script exists
    if clear_content:
        run_sql_on_db(rel_url, clear_content, "Relational_Clear")
        if age_url:
            rel_normalized = rel_url.replace("postgresql+asyncpg://", "postgresql://")
            age_normalized = age_url.replace("postgresql+asyncpg://", "postgresql://")
            if rel_normalized != age_normalized:
                run_sql_on_db(age_url, clear_content, "Graph_Clear")
    
    # 2. Main Relational DB Migration
    run_sql_on_db(rel_url, sql_content, "Relational")
    
    # 3. Graph DB Migration (if separate)
    if age_url:
        rel_normalized = rel_url.replace("postgresql+asyncpg://", "postgresql://")
        age_normalized = age_url.replace("postgresql+asyncpg://", "postgresql://")
        if rel_normalized != age_normalized:
            run_sql_on_db(age_url, sql_content, "Graph")
