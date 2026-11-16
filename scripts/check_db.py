"""Адрес базы: проверка связи и запись в .env (машина «только воркеры»),
адрес PostgreSQL главного сервера (start.* all/api/migrate).

    python scripts/check_db.py resolve
        Адрес, который возьмут API, воркеры и миграция (core.config): DATABASE_URL,
        а пустой — собранный из POSTGRES_USER/PASSWORD/DB/PORT на localhost
        (с %-кодированием, пароль может содержать любые символы). Печатает
        host:port; DATABASE_URL и POSTGRES_PORT заданы оба, а порты расходятся —
        ошибка (код 2). Без подключения: PostgreSQL ещё может не работать.
    python scripts/check_db.py check
        DATABASE_URL из окружения или .env (через core.config) → подключение за 5 с.
    python scripts/check_db.py ask [--env-file .env] [--non-interactive]
        DATABASE_URL из окружения, иначе вопрос: адрес без пароля + пароль скрыто
        (getpass); запись в .env как есть (!, %, \\, @, : сохраняются).

Вывод — только host:port и фиксированная причина по классу ошибки; ни URL, ни
текст исключения (libpq вставляет в него введённую строку с паролем) не
печатаются. Сообщения — ASCII: их показывают и cmd.exe (start.cmd, install.bat).

Коды выхода: 0 — в порядке; 1 — база не отвечает / неверный пароль / нет базы;
2 — DATABASE_URL не задан или неверен (resolve: или порт расходится с POSTGRES_PORT).
"""
from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
from pathlib import Path
from typing import Callable, Mapping, Optional
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
SCHEMES = ("postgresql", "postgres")
CONNECT_TIMEOUT_SEC = 5

OK, FAILED, BAD_URL = 0, 1, 2


class BadUrl(ValueError):
    """Неверный DATABASE_URL; текст — фиксированный, без значения URL."""


def say(message: str) -> None:
    print("   " + message, flush=True)


def host_port(url: str) -> str:
    """host:port для вывода; пароль сюда не попадает. Ошибку разбора не раскрываем."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or "?"
        port = parts.port or 5432
    except ValueError:
        return "?"
    return f"{host}:{port}"


def validate(url: str) -> str:
    """Проверяет DATABASE_URL до подключения; возвращает host:port или BadUrl."""
    if not url:
        raise BadUrl("DATABASE_URL is not set (environment or .env)")
    if "${" in url:
        raise BadUrl("DATABASE_URL must not contain ${...} (.env would expand it); "
                     "percent-encode $ as %24")
    try:
        parts = urlsplit(url)
        scheme_ok = parts.scheme in SCHEMES and url.split("://", 1)[0] in SCHEMES
        port = parts.port  # ValueError на нечисловом порте
    except ValueError:
        raise BadUrl("DATABASE_URL cannot be parsed (check the port)") from None
    if not scheme_ok:
        raise BadUrl("DATABASE_URL must start with postgresql://")
    if not parts.hostname:
        raise BadUrl("DATABASE_URL has no host (postgresql://user:password@host:port/db)")
    del port
    try:
        from psycopg import conninfo

        conninfo.conninfo_to_dict(url)
    except Exception:  # noqa: BLE001 — текст ошибки содержит URL
        raise BadUrl("DATABASE_URL is malformed (special characters in the password "
                     "must be percent-encoded, e.g. @ as %40, % as %25)") from None
    return host_port(url)


def _reason(exc: Exception) -> str:
    """Фиксированная причина по классу/коду ошибки; текст ошибки не выводится."""
    sqlstate = getattr(exc, "sqlstate", None) or ""
    text = str(exc).lower()
    if sqlstate in ("28P01", "28000") or "authentication failed" in text:
        return "authentication failed (user or password in DATABASE_URL)"
    if sqlstate == "3D000" or ("database" in text and "does not exist" in text):
        return "database missing (database name in DATABASE_URL)"
    return f"unreachable or no answer within {CONNECT_TIMEOUT_SEC} s"


def check(url: str) -> int:
    try:
        hp = validate(url)
    except BadUrl as exc:
        say(f"ERROR: {exc}")
        return BAD_URL
    try:
        import psycopg

        with psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT_SEC):
            pass
    except Exception as exc:  # noqa: BLE001
        try:
            import psycopg

            if isinstance(exc, psycopg.ProgrammingError):
                say("ERROR: DATABASE_URL is malformed")
                return BAD_URL
            reason = _reason(exc)
        except Exception:  # noqa: BLE001
            reason = f"connection failed ({type(exc).__name__})"
        say(f"ERROR: PostgreSQL {hp}: {reason}")
        say("Main server: POSTGRES_BIND=0.0.0.0, an open port, the password from its .env.")
        return FAILED
    say(f"OK: PostgreSQL {hp} answers")
    return OK


def resolve(env: Mapping[str, str]) -> int:
    """Адрес PostgreSQL главного сервера тем же построителем, что и core.config
    (service_urls.database_url); печатает host:port, сверяет порт с POSTGRES_PORT."""
    sys.path.insert(0, str(ROOT / "src"))
    from core import service_urls

    url = service_urls.database_url(env)
    try:
        hp = validate(url)
    except BadUrl as exc:
        say(f"ERROR: {exc}")
        return BAD_URL
    pg_port = (env.get("POSTGRES_PORT") or "").strip()
    url_port = hp.rsplit(":", 1)[-1]
    if pg_port and url_port != pg_port:
        say(f"ERROR: DATABASE_URL points to port {url_port}, but POSTGRES_PORT={pg_port}.")
        say("Fix one of them in .env - otherwise another PostgreSQL would be used.")
        return BAD_URL
    say(f"PostgreSQL: {hp}")
    return OK


def load_project_env() -> None:
    """.env проекта в os.environ (как в API и воркерах: окружение важнее)."""
    sys.path.insert(0, str(ROOT / "src"))
    from core import config  # noqa: F401 — load_dotenv(.env проекта)


def configured_url() -> str:
    """DATABASE_URL, заданный явно (окружение или .env через core.config).
    Сборка из POSTGRES_* (как в config.DATABASE_URL) здесь не нужна: машине
    воркеров нужен адрес главного сервера."""
    load_project_env()
    return os.environ.get("DATABASE_URL", "").strip()


# ── Запись в .env ────────────────────────────────────────────────────────────

_NEEDS_QUOTES = re.compile(r"[\s#'\"\\]")


def env_line(value: str) -> str:
    """Строка KEY=... , которую python-dotenv (и compose) прочтут как value.
    Без пробелов, #, кавычек и \\ — как есть; иначе в одинарных кавычках
    (внутри них dotenv раскрывает только \\\\ и \\')."""
    if not _NEEDS_QUOTES.search(value):
        return f"DATABASE_URL={value}"
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"DATABASE_URL='{escaped}'"


def read_env_url(env_file: Path) -> str:
    if not env_file.exists():
        return ""
    from dotenv import dotenv_values

    return (dotenv_values(env_file, interpolate=False).get("DATABASE_URL") or "").strip()


def write_env_url(env_file: Path, url: str) -> None:
    """Заменяет активную строку DATABASE_URL= (или дописывает); остальное — как было."""
    raw = env_file.read_bytes() if env_file.exists() else b""
    text = raw.decode("utf-8-sig")  # без перевода \r\n: стиль строк файла сохраняется
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    line = env_line(url)
    for i, current in enumerate(lines):
        if current.startswith("DATABASE_URL="):
            lines[i] = line
            break
    else:
        lines.append(line)
    env_file.write_text(newline.join(lines) + newline, encoding="utf-8", newline="")


def add_password(base: str, password: str) -> str:
    """postgresql://user@host:port/db + пароль → URL с паролем в %-кодировке."""
    if not password:
        return base
    scheme, sep, rest = base.partition("://")
    if not sep or "@" not in rest.split("/", 1)[0]:
        raise BadUrl("give the address as postgresql://user@host:port/db")
    userinfo, _, tail = rest.partition("@")
    user = userinfo.split(":", 1)[0]
    return f"{scheme}://{user}:{quote(password, safe='')}@{tail}"


def ask_url(env_value: str, current: str, interactive: bool,
            input_fn: Callable[[str], str] = input,
            getpass_fn: Callable[[str], str] = getpass.getpass) -> str:
    if env_value:
        say("DATABASE_URL: from the environment")
        return env_value
    if not interactive:
        return current
    say("Database of the main server. There: POSTGRES_BIND=0.0.0.0, an open port")
    say("and a password other than classifier.")
    keep = f" [Enter = keep current, {host_port(current)}]" if current else ""
    base = input_fn("   Address without the password, postgresql://user@host:port/db"
                    f"{keep}: ").strip()
    if not base:
        return current
    password = getpass_fn("   Password (hidden): ")
    return add_password(base, password)


def run_ask(env_file: Path, interactive: bool,
            input_fn: Callable[[str], str] = input,
            getpass_fn: Callable[[str], str] = getpass.getpass) -> int:
    try:
        url = ask_url(os.environ.get("DATABASE_URL", "").strip(), read_env_url(env_file),
                      interactive, input_fn, getpass_fn)
        hp = validate(url)
    except BadUrl as exc:
        say(f"ERROR: {exc}")
        return BAD_URL
    write_env_url(env_file, url)
    say(f"OK: DATABASE_URL saved to {env_file.name} (database {hp})")
    return OK


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="check_db.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("resolve", help="print host:port of the main server database "
                                   "(DATABASE_URL or POSTGRES_*), check POSTGRES_PORT")
    sub.add_parser("check", help="connect to DATABASE_URL within 5 s")
    ask = sub.add_parser("ask", help="ask DATABASE_URL and write it to .env")
    ask.add_argument("--env-file", default=str(ROOT / ".env"))
    ask.add_argument("--non-interactive", action="store_true")
    args = parser.parse_args(argv)
    if args.cmd == "resolve":
        load_project_env()
        return resolve(os.environ)
    if args.cmd == "check":
        return check(configured_url())
    return run_ask(Path(args.env_file), interactive=not args.non_interactive)


if __name__ == "__main__":
    sys.exit(main())
