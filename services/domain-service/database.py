from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
    pass


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def init_db() -> None:
    """Create tables on startup (use Alembic for production migrations)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed default users if table is empty
    from sqlalchemy import select
    from models import User
    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(select(User))
            if not result.scalars().first():
                default_users = [
                    User(id="admin", name="System Admin", role="system_admin"),
                    User(id="652ec45e-1b68-478c-9bd3-81cc46fb24a9", name="System Admin (UUID)", role="system_admin"),
                    
                    User(id="manager", name="Domain Manager", role="domain_admin"),
                    User(id="manager-id-123", name="Domain Manager (ID)", role="domain_admin"),
                    
                    User(id="user", name="Regular User", role="contributor"),
                    User(id="user-id-123", name="Regular User (ID)", role="contributor"),
                    User(id="a1111111-1111-1111-1111-111111111111", name="Regular User (UUID)", role="contributor"),
                    
                    User(id="viewer", name="Viewer User", role="reader"),
                    User(id="viewer-id-123", name="Viewer User (ID)", role="reader"),
                    User(id="d3794cbc-9bb9-4c06-95e5-33603c71b287", name="Viewer User (UUID)", role="reader"),
                    
                    User(id="unauth", name="Unauthorized User", role="unauthorized"),
                    User(id="unauth-id-123", name="Unauthorized User (ID)", role="unauthorized"),
                ]
                session.add_all(default_users)
                await session.commit()
                print("  Seeded database with default users.")
        except Exception as e:
            await session.rollback()
            print(f"Error seeding default users: {e}")


async def dispose_engine() -> None:
    await engine.dispose()

