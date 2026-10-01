"""Tiny forward-only SQL migration runner. Usage: python db/migrate.py [DATABASE_URL]"""
import os, sys, pathlib, psycopg

def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else os.environ["DATABASE_URL"]
    here = pathlib.Path(__file__).parent / "migrations"
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migration (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now())")
        done = {r[0] for r in conn.execute("SELECT name FROM schema_migration")}
        for f in sorted(here.glob("*.sql")):
            if f.name in done:
                continue
            print(f"applying {f.name}")
            conn.execute(f.read_text())
            conn.execute("INSERT INTO schema_migration(name) VALUES (%s)", (f.name,))
    print("migrations up to date")

if __name__ == "__main__":
    main()
