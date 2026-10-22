import os
from pathlib import Path
import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
from .models import Base, Target, InputType

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/presence.db")
# Render gives postgres://; pin the psycopg2 driver explicitly (SQLAlchemy 2.1 defaults to psycopg v3)
for prefix in ("postgres://", "postgresql://"):
    if DATABASE_URL.startswith(prefix):
        DATABASE_URL = "postgresql+psycopg2://" + DATABASE_URL[len(prefix):]
        break

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def init_db():
    if DATABASE_URL.startswith("sqlite"):
        Path("./data").mkdir(exist_ok=True)
    Base.metadata.create_all(engine)
    seed_from_config()


def seed_from_config():
    """Upsert targets and input types from YAML so the registry can be edited in git."""
    with SessionLocal() as s:
        targets = yaml.safe_load((CONFIG_DIR / "targets.yaml").read_text())
        for t in targets:
            row = s.get(Target, t["key"]) or Target(key=t["key"])
            for k, v in t.items():
                setattr(row, k, v)
            s.add(row)
        inputs = yaml.safe_load((CONFIG_DIR / "inputs.yaml").read_text())
        for i in inputs:
            row = s.get(InputType, i["key"]) or InputType(key=i["key"])
            for k, v in i.items():
                setattr(row, k, v)
            s.add(row)
        s.commit()


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
