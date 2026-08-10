#!/usr/bin/env python3
"""
Script para ejecutar migraciones SQL en Supabase.
Requiere una función RPC `exec_sql` en la base de datos:

  create or replace function exec_sql(sql text) returns void
  language plpgsql as $$
  begin
    execute sql;
  end;
  $$;

Uso: python scripts/run_migration.py <numero_migracion>
Ej: python scripts/run_migration.py 005
"""

import sys
import os
from pathlib import Path
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

supabase_url = os.environ.get("SUPABASE_URL")
supabase_service_key = os.environ.get("SUPABASE_SERVICE_KEY")

if not supabase_url or not supabase_service_key:
    print("Error: SUPABASE_URL y SUPABASE_SERVICE_KEY deben estar en .env")
    sys.exit(1)

db = create_client(supabase_url, supabase_service_key)

def run_migration(migration_num: str):
    """Ejecuta una migración SQL"""
    migrations_dir = Path(__file__).parent.parent / "migrations"

    migration_files = list(migrations_dir.glob(f"{migration_num}_*.sql"))

    if not migration_files:
        print(f"Error: No se encontró migración {migration_num}")
        sys.exit(1)

    migration_file = migration_files[0]
    print(f"Ejecutando: {migration_file.name}")

    sql = migration_file.read_text()

    try:
        db.rpc("exec_sql", {"sql": sql}).execute()
        print(f"✅ Migración {migration_num} completada")
    except Exception as e:
        print(f"✗ Error ejecutando migración: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python scripts/run_migration.py <numero_migracion>")
        print("Ej: python scripts/run_migration.py 005")
        sys.exit(1)

    migration_num = sys.argv[1]
    run_migration(migration_num)
