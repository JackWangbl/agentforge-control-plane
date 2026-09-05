import os
from pathlib import Path

TEST_DB = Path(__file__).resolve().parent / "agentforge-test.db"
TEST_WS = Path(__file__).resolve().parent / "workspaces-test"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["WORKSPACES_DIR"] = str(TEST_WS)
os.environ["EVAL_WORKER"] = "0"
os.environ["AUTH_DEV_USER"] = "linmo"
if TEST_DB.exists():
    TEST_DB.unlink()
if TEST_WS.exists():
    import shutil
    shutil.rmtree(TEST_WS, ignore_errors=True)
TEST_WS.mkdir(parents=True, exist_ok=True)
