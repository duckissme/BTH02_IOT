"""Đọc config/config.ini; cho phép ghi đè bằng biến môi trường IOT_<SECTION>_<KEY>."""
import configparser
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv():
    """Đọc file .env (KEY=VALUE) ở thư mục gốc vào os.environ, không ghi đè biến đã có."""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def load(path=None):
    _load_dotenv()
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(path or os.environ.get("IOT_CONFIG", ROOT / "config" / "config.ini"), encoding="utf-8")
    for section in cp.sections():
        for key in cp[section]:
            env = os.environ.get(f"IOT_{section}_{key}".upper())
            if env is not None:
                cp[section][key] = env
    return cp
