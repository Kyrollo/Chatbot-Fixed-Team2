#!/usr/bin/env python3
"""Start local infrastructure (Redis + Keycloak) without Docker."""

from __future__ import annotations

import os
import shutil
import ssl
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from urllib.request import urlretrieve

try:
    import truststore

    truststore.inject_into_ssl()
except Exception:
    ssl._create_default_https_context = ssl._create_unverified_context  # type: ignore[misc]

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
REDIS_DIR = TOOLS / "redis"
KEYCLOAK_HOME = TOOLS / "keycloak"
REALM_SOURCE = ROOT / "services" / "auth" / "realm-export.json"

REDIS_ZIP_URL = (
    "https://github.com/tporadowski/redis/releases/download/v5.0.14.1/Redis-x64-5.0.14.1.zip"
)
KEYCLOAK_ZIP_URL = (
    "https://github.com/keycloak/keycloak/releases/download/26.5.0/keycloak-26.5.0.zip"
)


def get_keycloak_port() -> str:
    kc_port = os.getenv("KEYCLOAK_PORT")
    if kc_port:
        return kc_port
    dotenv_path = ROOT / ".env"
    if dotenv_path.exists():
        try:
            for line in dotenv_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("KEYCLOAK_PORT="):
                    return line.split("=", 1)[1].strip()
        except Exception:
            pass
    return "8180"


def get_keycloak_gateway_port() -> str:
    kc_g_port = os.getenv("KEYCLOAK_GATEWAY_PORT")
    if kc_g_port:
        return kc_g_port
    dotenv_path = ROOT / ".env"
    if dotenv_path.exists():
        try:
            for line in dotenv_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("KEYCLOAK_GATEWAY_PORT="):
                    return line.split("=", 1)[1].strip()
        except Exception:
            pass
    return "8443"


def get_redis_port() -> int:
    r_port = os.getenv("REDIS_PORT")
    if r_port:
        try:
            return int(r_port)
        except ValueError:
            pass
    dotenv_path = ROOT / ".env"
    if dotenv_path.exists():
        try:
            for line in dotenv_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("REDIS_PORT="):
                    return int(line.split("=", 1)[1].strip())
        except Exception:
            pass
    return 6379


KEYCLOAK_REALM_URL = f"http://localhost:{get_keycloak_port()}/realms/rag-system"


def _download_zip(url: str, dest_zip: Path) -> None:
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    if dest_zip.exists() and dest_zip.stat().st_size > 0:
        try:
            with zipfile.ZipFile(dest_zip, "r") as archive:
                if archive.testzip() is None:
                    print(f"  Using cached {dest_zip.name}")
                    return
        except (zipfile.BadZipFile, PermissionError, OSError):
            pass
        try:
            print(f"  Removing invalid {dest_zip.name}")
            dest_zip.unlink(missing_ok=True)
        except PermissionError:
            dest_zip = dest_zip.with_name(f"{dest_zip.stem}-new{dest_zip.suffix}")

    import requests

    tmp_zip = dest_zip.with_suffix(".part")
    if tmp_zip.exists():
        tmp_zip.unlink()

    print(f"  Downloading {dest_zip.name} ...", flush=True)
    with requests.get(url, stream=True, timeout=600) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0))
        downloaded = 0
        with tmp_zip.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                handle.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    print(f"\r  Downloading {dest_zip.name}: {pct}%", end="", flush=True)
    if dest_zip.exists():
        dest_zip.unlink(missing_ok=True)
    tmp_zip.replace(dest_zip)
    print(f"\r  Downloaded {dest_zip.name} ({downloaded // (1024 * 1024)} MB)   ", flush=True)


def _extract_zip(zip_path: Path, dest_dir: Path) -> None:
    if dest_dir.exists() and any(dest_dir.iterdir()):
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as archive:
        archive.extractall(dest_dir)


def _install_from_zip(zip_path: Path, extract_dir: Path, install_dir: Path, marker: str) -> None:
    if (install_dir / marker).exists():
        return
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    _extract_zip(zip_path, extract_dir)
    if (extract_dir / marker).exists():
        install_dir.mkdir(parents=True, exist_ok=True)
        for item in extract_dir.iterdir():
            dest = install_dir / item.name
            if dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
            shutil.move(str(item), dest)
        return
    subdirs = [p for p in extract_dir.iterdir() if p.is_dir()]
    if not subdirs:
        raise RuntimeError(f"Install failed: no files found in {extract_dir}")
    if install_dir.exists():
        shutil.rmtree(install_dir)
    shutil.move(str(subdirs[0]), install_dir)


def ensure_redis() -> Path:
    """Return path to redis-server executable, downloading portable Redis on Windows if needed."""
    for candidate in (
        shutil.which("redis-server"),
        str(REDIS_DIR / "redis-server.exe") if os.name == "nt" else None,
        "/usr/bin/redis-server",
        "/opt/homebrew/bin/redis-server",
    ):
        if candidate and Path(candidate).exists():
            return Path(candidate)

    if os.name != "nt":
        raise RuntimeError("redis-server not found. Install Redis and ensure it is on PATH.")

    zip_path = TOOLS / "redis.zip"
    _download_zip(REDIS_ZIP_URL, zip_path)
    _install_from_zip(zip_path, TOOLS / "redis-extract", REDIS_DIR, "redis-server.exe")
    server = REDIS_DIR / "redis-server.exe"
    if not server.exists():
        raise RuntimeError(f"Redis install failed: {server} not found")
    return server


def ensure_keycloak_home() -> Path:
    """Return Keycloak home directory, downloading the distribution if needed."""
    if os.name == "nt":
        kc_bin = KEYCLOAK_HOME / "bin" / "kc.bat"
    else:
        kc_bin = KEYCLOAK_HOME / "bin" / "kc.sh"

    if kc_bin.exists():
        return KEYCLOAK_HOME

    zip_path = TOOLS / "keycloak.zip"
    _download_zip(KEYCLOAK_ZIP_URL, zip_path)
    kc_marker = "bin/kc.bat" if os.name == "nt" else "bin/kc.sh"
    _install_from_zip(zip_path, TOOLS / "keycloak-extract", KEYCLOAK_HOME, kc_marker)
    if not kc_bin.exists():
        raise RuntimeError(f"Keycloak install failed: {kc_bin} not found")
    return KEYCLOAK_HOME


def redis_ping(host: str = "localhost", port: int | None = None) -> bool:
    if port is None:
        port = get_redis_port()
    try:
        import redis

        return bool(
            redis.Redis(host=host, port=port, socket_connect_timeout=1, protocol=2).ping()
        )
    except Exception:
        return False


def keycloak_ready(timeout: float = 2) -> bool:
    try:
        import urllib.request

        with urllib.request.urlopen(KEYCLOAK_REALM_URL, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def wait_for_redis(timeout: int = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if redis_ping():
            return True
        time.sleep(1)
    return False


def wait_for_keycloak(timeout: int = 180) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if keycloak_ready():
            return True
        time.sleep(2)
    return False


def start_redis() -> subprocess.Popen:
    server = ensure_redis()
    port = get_redis_port()
    if redis_ping(port=port):
        print(f"  Redis already running on localhost:{port}")
        return subprocess.Popen(["cmd", "/c", "echo", "redis-already-running"], stdout=subprocess.DEVNULL)

    conf = server.parent / "redis.windows.conf"
    cmd = [str(server), str(conf), "--port", str(port)] if conf.exists() else [str(server), "--port", str(port)]
    print(f"  Starting Redis ({server}) on port {port}...")
    if os.name == "nt":
        return subprocess.Popen(
            cmd,
            cwd=server.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    return subprocess.Popen([str(server), "--port", str(port)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def start_keycloak() -> subprocess.Popen:
    home = ensure_keycloak_home()
    kc_port = get_keycloak_port()
    kc_g_port = get_keycloak_gateway_port()

    if keycloak_ready():
        print(f"  Keycloak already running on http://localhost:{kc_port}")
        return subprocess.Popen(
            ["cmd", "/c", "echo", "keycloak-already-running"],
            stdout=subprocess.DEVNULL,
        )

    import_dir = home / "data" / "import"
    import_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REALM_SOURCE, import_dir / "realm-export.json")

    if os.name == "nt":
        kc = home / "bin" / "kc.bat"
        cmd = [
            str(kc),
            "start-dev",
            f"--http-port={kc_port}",
            f"--hostname=https://localhost:{kc_g_port}",
            "--proxy-headers=xforwarded",
            "--http-enabled=true",
            "--import-realm",
        ]
    else:
        kc = home / "bin" / "kc.sh"
        cmd = [
            str(kc),
            "start-dev",
            f"--http-port={kc_port}",
            f"--hostname=https://localhost:{kc_g_port}",
            "--proxy-headers=xforwarded",
            "--http-enabled=true",
            "--import-realm",
        ]

    env = os.environ.copy()
    env["KC_BOOTSTRAP_ADMIN_USERNAME"] = "admin"
    env["KC_BOOTSTRAP_ADMIN_PASSWORD"] = "admin"

    print(f"  Starting Keycloak ({kc})...")

    return subprocess.Popen(
        cmd,
        cwd=home,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )


def start_all_infra() -> tuple[list[tuple[str, subprocess.Popen]], bool, bool]:
    """Start Keycloak then Redis. Returns (processes, redis_ok, keycloak_ok)."""
    processes: list[tuple[str, subprocess.Popen]] = []

    kc_proc = start_keycloak()
    processes.append(("keycloak", kc_proc))
    print("  Waiting for Keycloak realm rag-system...")
    keycloak_ok = wait_for_keycloak(timeout=240)
    if keycloak_ok:
        print("  Keycloak is ready")
    else:
        print("  WARNING: Keycloak did not become ready in time")

    redis_proc = start_redis()
    if redis_proc.args and redis_proc.args[-1] != "redis-already-running":
        processes.append(("redis", redis_proc))
    print("  Waiting for Redis...")
    redis_ok = wait_for_redis(timeout=30)
    if redis_ok:
        print("  Redis is ready")
    else:
        print("  WARNING: Redis did not become ready in time")

    return processes, redis_ok, keycloak_ok


if __name__ == "__main__":
    procs, redis_ok, kc_ok = start_all_infra()
    print(f"redis={redis_ok} keycloak={kc_ok}")
    if not redis_ok or not kc_ok:
        sys.exit(1)
