import os
from pathlib import Path

APP_TITLE = os.getenv("APP_TITLE", "GAM Traffic AI PoC")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://gam:gam_password@localhost:5432/gam_poc")
LOCAL_TIMEZONE = os.getenv("LOCAL_TIMEZONE", "Asia/Amman")
DATA_ROOT = Path(os.getenv("DATA_ROOT", "/app/data"))
FRONTEND_ROOT = Path("/app/frontend/static")
