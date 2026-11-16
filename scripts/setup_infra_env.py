"""Порт PostgreSQL на хосте и адрес подключения в .env.

Вызывается установщиками (install.bat / install.sh / install_offline.*) перед
`docker compose up postgres` и миграцией:

    venv/Scripts/python.exe scripts/setup_infra_env.py [--env-file PATH] [--dry-run] [--no-docker]

Для POSTGRES_PORT (5432):
- значение уже есть в .env — остаётся, если порт свободен или его держит свой
  контейнер. Занят чужим процессом: 5432 (старый .env.example)
  считается незаданным — подбирается запасной; иное значение — ошибка (код 1)
  с именем ключа и порта. Не число 1..65535 — тоже ошибка;
- иначе порт, который сейчас публикует контейнер ЭТОГО проекта (docker compose ps), —
  он свой, а не «занят»;
- иначе порт по умолчанию, если на 127.0.0.1 его никто не слушает;
- иначе первый свободный из фиксированного списка (5434, 5435, ...). 5433 пропущен: там тестовый PostgreSQL (TEST_DATABASE_URL).

Затем в .env пишется DATABASE_URL на localhost с этим портом и учётными данными
POSTGRES_* (экранированными). DATABASE_URL, указывающий на другую машину или unix-сокет, не трогаются; локальный
URL с другими учётными данными или портом заменяется (в выводе: replaced, was different).

Файл правится бережно: UTF-8, BOM (если был), концы строк, комментарии и порядок
строк сохраняются. Пароли и URL целиком не печатаются. Вывод — ASCII: его
показывает консоль cmd.exe в любой кодовой странице.

Код возврата: 0 — готово, 1 — порт из .env непригоден или свободного нет,
2 — нет .env или он не в UTF-8.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping, Optional
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core.service_urls import build_database_url  # noqa: E402

BOM = "\ufeff"

# ключ .env -> (порт по умолчанию, запасные порты по порядку)
PORTS: dict[str, tuple[int, list[int]]] = {
    "POSTGRES_PORT": (5432, list(range(5434, 5454))),
}

# (сервис compose, порт в контейнере) -> ключ .env
_PUBLISHED = {
    ("postgres", 5432): "POSTGRES_PORT",
}

# Пустой hostname (unix-сокет: postgresql:///db?host=/run/postgresql) — не TCP
# localhost: такой адрес не переписывается.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class NoFreePort(RuntimeError):
    pass


class PortError(ValueError):
    """Порт из .env непригоден: не число или занят чужим процессом."""


# ── Порты ───────────────────────────────────────────────────────────────────

def port_is_free(port: int) -> bool:
    """Свободен, если на 127.0.0.1:port никто не слушает и порт можно занять."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.settimeout(1.0)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return False
    except OSError:
        pass
    finally:
        probe.close()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def choose_port(current: str, default: int, fallbacks: list[int],
                is_free: Callable[[int], bool], ours: Optional[int]) -> tuple[int, str]:
    """(порт, пояснение). current — значение из .env (пусто — не задано).

    Значение из .env остаётся, если порт свободен или его держит свой контейнер.
    Занят чужим процессом: значение по умолчанию (5432 / 5672 / 15672 — так было
    в старом .env.example) считается незаданным: берётся порт своего контейнера,
    если он есть, иначе первый свободный запасной; любое другое — PortError
    (его задали осознанно, молча менять нельзя).
    """
    current = (current or "").strip()
    note = ""
    if current:
        if not current.isdigit() or not 1 <= int(current) <= 65535:
            raise PortError(f"{current!r} is not a port number (1-65535)")
        port = int(current)
        if port == ours or is_free(port):
            return port, "kept from .env"
        if port != default:
            raise PortError(f"port {port} is busy (another program listens on "
                            f"127.0.0.1:{port})")
        if ours:
            # свой контейнер уже публикует другой порт: берём его, а не запасной,
            # иначе compose пересоздал бы контейнер на новом порту
            return ours, f"{default} from .env is busy, using this project's container port"
        note = f"{default} from .env is busy, "
    elif ours:
        return ours, "published by this project's container"
    elif is_free(default):
        return default, "default, free"
    else:
        note = f"{default} is busy, "
    for port in fallbacks:
        if is_free(port):
            return port, f"{note}picked first free fallback {port}"
    raise NoFreePort(f"{default} and fallbacks {fallbacks[0]}..{fallbacks[-1]} are all busy")


def parse_compose_ps(output: str) -> dict[str, int]:
    """Порты хоста, которые публикуют postgres проекта (ключ .env -> порт)."""
    output = (output or "").strip()
    if not output:
        return {}
    try:
        data = json.loads(output)
        items = data if isinstance(data, list) else [data]
    except ValueError:
        items = []
        for line in output.splitlines():
            try:
                items.append(json.loads(line))
            except ValueError:
                return {}
    found: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        for pub in item.get("Publishers") or []:
            key = _PUBLISHED.get((item.get("Service"), pub.get("TargetPort")))
            published = pub.get("PublishedPort") or 0
            if key and published:
                found[key] = int(published)
    return found


def compose_own_ports() -> dict[str, int]:
    """docker compose ps в корне проекта; Docker нет или не запущен — пусто."""
    try:
        proc = subprocess.run(["docker", "compose", "ps", "--format", "json"], cwd=str(ROOT),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return {}
    if proc.returncode != 0:
        return {}
    return parse_compose_ps(proc.stdout)


# ── .env ────────────────────────────────────────────────────────────────────

_KEY_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def _clean_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    # Комментарий — «#» после пробела; по исходной строке, чтобы «KEY= # текст»
    # давало пустое значение, а «KEY=#secret» (без пробела) оставалось значением.
    return re.sub(r"\s+#.*$", "", raw).strip()


def parse_env(text: str) -> dict[str, str]:
    """Активные строки KEY=value (первое вхождение), без хвостовых комментариев."""
    values: dict[str, str] = {}
    for line in text.lstrip(BOM).splitlines():
        match = _KEY_LINE.match(line.strip())
        if match and match.group(1) not in values:
            values[match.group(1)] = _clean_value(match.group(2))
    return values


def set_keys(text: str, updates: Mapping[str, str], newline: str) -> str:
    """Заменяет активные строки KEY=..., недостающие дописывает в конец."""
    pending = dict(updates)
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        match = _KEY_LINE.match(line.rstrip("\r\n"))
        if match and match.group(1) in updates:
            ending = line[len(line.rstrip("\r\n")):]
            # хвостовой комментарий («POSTGRES_PORT=5434  # порт хоста») сохраняется
            comment = re.search(r"\s+#.*$", match.group(2))
            tail = comment.group(0) if comment and match.group(2).strip()[:1] not in "\"'" else ""
            lines[i] = f"{match.group(1)}={updates[match.group(1)]}{tail}{ending}"
            pending.pop(match.group(1), None)
    out = "".join(lines)
    if pending:
        if out and not out.endswith(("\n", "\r")):
            out += newline
        out += "".join(f"{key}={value}{newline}" for key, value in pending.items())
    return out


def _is_local(url: str) -> bool:
    try:
        return (urlsplit(url).hostname or "") in _LOCAL_HOSTS
    except ValueError:
        return False


def _hostport(url: str) -> str:
    """host:port для сообщений (без учётных данных); кривой порт — «?»."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return "?"
    try:
        port = parts.port
    except ValueError:
        port = "?"
    return f"{host}:{port}"


# ── main ────────────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None, *,
         is_free: Callable[[int], bool] = port_is_free,
         own_ports: Optional[Callable[[], dict[str, int]]] = None) -> int:
    parser = argparse.ArgumentParser(description="Pick the host port for PostgreSQL "
                                                 "and write it with DATABASE_URL to .env")
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--dry-run", action="store_true", help="print the plan, do not write")
    parser.add_argument("--no-docker", action="store_true",
                        help="do not ask docker compose which ports are ours")
    args = parser.parse_args(argv)

    path = Path(args.env_file)
    if not path.is_file():
        print(f"ERROR: {path} not found - create it from .env.example first", file=sys.stderr)
        return 2

    try:
        raw = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        print(f"ERROR: {path.name} is not UTF-8 text - save it as UTF-8 and rerun",
              file=sys.stderr)
        return 2
    has_bom = raw.startswith(BOM)
    text = raw[1:] if has_bom else raw
    newline = "\r\n" if "\r\n" in text else "\n"
    env = parse_env(text)

    if own_ports is None:
        own_ports = (lambda: {}) if args.no_docker else compose_own_ports
    ours = own_ports()

    updates: dict[str, str] = {}
    for key, (default, fallbacks) in PORTS.items():
        try:
            port, why = choose_port(env.get(key, ""), default, fallbacks, is_free, ours.get(key))
        except PortError as exc:
            print(f"ERROR: {key} in {path.name}: {exc}. Change it, or clear the value "
                  f"({key}=) to let the installer pick a free port.", file=sys.stderr)
            return 1
        except NoFreePort as exc:
            print(f"ERROR: {key}: no free port: {exc}", file=sys.stderr)
            return 1
        updates[key] = str(port)
        print(f"  {key}={port}  ({why})")

    merged = {**env, **updates}
    key = "DATABASE_URL"
    current = env.get(key, "")
    if current and not _is_local(current):
        print(f"  {key}: not a localhost TCP address - kept as is")
    else:
        updates[key] = build_database_url(merged)
        note = "credentials from POSTGRES_*"
        if current and current != updates[key]:
            # Только host:port: в старом URL могут быть чужие учётные данные.
            note += f"; replaced, was different ({_hostport(current)})"
        print(f"  {key} -> {_hostport(updates[key])}  ({note})")

    if args.dry_run:
        print("  dry run: .env not changed")
        return 0
    new_text = set_keys(text, updates, newline)
    if new_text != text:
        path.write_bytes(((BOM if has_bom else "") + new_text).encode("utf-8"))
        print(f"  OK: ports and URLs written to {path.name}")
    else:
        print(f"  OK: {path.name} already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
