     python - <<'PY'
     import asyncio
     import os
     from sqlalchemy import text
     from sqlalchemy.ext.asyncio import create_async_engine

     from app.core.config import settings
     from app.services.security import hash_password

     async def main():
         admin_password = os.getenv("TEST_ADMIN_PASSWORD", "")
         if not admin_password:
             raise RuntimeError("Ustaw TEST_ADMIN_PASSWORD przed utworzeniem konta.")
         engine = create_async_engine(settings.database_url, future=True)
         async with engine.begin() as conn:
             await conn.execute(
                 text("""
                     INSERT INTO ctip.admin_user (email, first_name, last_name, role, password_hash, is_active)
                     VALUES (:email, :first_name, :last_name, :role, :password_hash, true)
                     ON CONFLICT (email) DO NOTHING
                 """),
                 {
                     "email": "marcin@ksero-partner.com.pl",
                     "first_name": "Marcin",
                     "last_name": "Jarmuszkiewicz",
                     "role": "admin",
                     "password_hash": hash_password(admin_password)
                 },
             )
         await engine.dispose()

     asyncio.run(main())
     PY
