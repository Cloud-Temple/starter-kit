#!/usr/bin/env python3
"""Infra-only verified TLS/startup rejection; never sends a password or SASL data.

PostgreSQL protocol: protocol-flow.html (SSL negotiation/startup),
protocol-message-formats.html (SSLRequest, StartupMessage, ErrorResponse).
This reports an observed startup code, not a general diagnosis of server policy.
"""
import argparse
import configparser
import json
import os
from pathlib import Path
import socket
import ssl
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import profiles


def read_exact(stream, size):
    result = bytearray()
    while len(result) < size:
        chunk = stream.recv(size - len(result))
        if not chunk:
            raise ValueError("Truncated protocol response")
        result.extend(chunk)
    return bytes(result)


def startup(role, database):
    import re
    for value in (role, database):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value):
            raise ValueError("Literal startup identity required")
    data = struct.pack("!I", 196608) + b"user\0" + role.encode() + b"\0database\0" + database.encode() + b"\0\0"
    return struct.pack("!I", len(data) + 4) + data


def error_code(stream):
    header = read_exact(stream, 5)
    length = struct.unpack("!I", header[1:])[0]
    if header[:1] != b"E" or not 6 <= length <= 16384:
        raise ValueError("Expected one bounded ErrorResponse")
    payload = read_exact(stream, length - 4)
    fields = {}
    cursor = 0
    while cursor < len(payload) - 1:
        tag = payload[cursor:cursor + 1]
        end = payload.find(b"\0", cursor + 1)
        if tag not in b"SVCMDHPpqWstcdnFLR" or tag in fields or end == -1:
            raise ValueError("Malformed ErrorResponse")
        fields[tag] = payload[cursor + 1:end]
        cursor = end + 1
    if cursor != len(payload) - 1 or payload[cursor:] != b"\0" or fields.get(b"C") != b"28000":
        raise ValueError("Startup rejection is not SQLSTATE28000")
    return "28000"


def profile(service_file, service):
    path = Path(service_file)
    if path.is_symlink() or not path.is_absolute() or path.stat().st_mode & 0o077:
        raise ValueError("Private service file required")
    config = configparser.ConfigParser(interpolation=None, strict=True)
    config.read(path)
    if config.defaults() or service not in config:
        raise ValueError("Explicit service required")
    values = dict(config[service])
    required = {"host", "port", "dbname", "user", "sslmode", "sslrootcert", "passfile", "connect_timeout"}
    if not required <= set(values) or set(values) - required - {"hostaddr"}:
        # Includes sslcert/sslkey: do not silently project an unsupported client identity.
        raise ValueError("Unsupported service profile; no partial TLS projection")
    profiles.endpoint(values["host"], int(values["port"]), values.get("hostaddr"))
    if values["sslmode"] != "verify-full" or not 1 <= int(values["connect_timeout"]) <= 10:
        raise ValueError("Verified bounded TLS required")
    for key in ("sslrootcert", "passfile"):
        candidate = Path(values[key])
        if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_file():
            raise ValueError("Explicit existing profile paths required")
    if Path(values["passfile"]).stat().st_mode & 0o077:
        raise ValueError("Private passfile required")
    return values


def rejected(values, database):
    context = ssl.create_default_context(cafile=values["sslrootcert"])
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    address = values.get("hostaddr", values["host"])
    with socket.create_connection((address, int(values["port"])), timeout=int(values["connect_timeout"])) as raw:
        raw.sendall(struct.pack("!II", 8, 80877103))
        if read_exact(raw, 1) != b"S":
            raise ValueError("Server did not accept TLS")
        with context.wrap_socket(raw, server_hostname=values["host"]) as verified:
            verified.sendall(startup(values["user"], database))
            code = error_code(verified)
            if verified.recv(1) != b"":
                raise ValueError("Expected server close after one ErrorResponse")
            return code


def check(service_file, passfile, service, database, role, neighbor):
    values = profile(service_file, service)
    if values["user"] != role or values["dbname"] != database or values["passfile"] != passfile:
        raise ValueError("Connection identity mismatch")
    if neighbor in (database, "postgres", "template1"):
        raise ValueError("Distinct neighbor required")
    if any((Path.home() / ".postgresql" / name).exists() for name in ("postgresql.crt", "postgresql.key")):
        raise ValueError("Implicit client certificate cannot be projected")
    # libpq authenticates once under the exact real profile before denial probes.
    import psycopg
    for key in tuple(os.environ):
        if key.startswith("PG"):
            del os.environ[key]
    os.environ.update(PGSERVICEFILE=service_file, PGPASSFILE=passfile)
    with psycopg.connect(service=service, passfile=passfile,
                        sslmode="verify-full", sslrootcert=values["sslrootcert"],
                        connect_timeout=int(values["connect_timeout"]), autocommit=True) as connection:
        row = connection.execute("SELECT current_user,current_database(),ssl,current_setting('server_version_num')::int FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone()
        if row is None or row[0:3] != (role, database, True) or not 170000 <= row[3] < 180000:
            raise ValueError("Wrong authenticated identity/TLS/server major")
    for target in (neighbor, "postgres", "template1"):
        rejected(values, target)
    return {"status": "connection_context_checked", "tls": True, "denials": ["28000"] * 3}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("service-file", "passfile", "service", "database", "role", "neighbor"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(check(args.service_file, args.passfile, args.service, args.database, args.role, args.neighbor)))
    except Exception:
        # Never publish a server message, DSN, path content or password.
        print('{"status":"connection_context_unconfirmed"}', file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
