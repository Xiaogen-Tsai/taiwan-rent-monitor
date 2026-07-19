from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rent_bot.config import get_settings  # noqa: E402
from rent_bot.db import init_db  # noqa: E402


def main() -> None:
    settings = get_settings()
    init_db(settings.database_path)
    print(f"Initialized database: {settings.database_path}")


if __name__ == "__main__":
    main()
