import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root / "src"))

from bot import main as run_bot  # noqa: E402

if __name__ == "__main__":
    asyncio.run(run_bot())
