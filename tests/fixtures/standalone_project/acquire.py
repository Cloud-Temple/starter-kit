#!/usr/bin/env python3
"""Acquire the public witness at one SHA; never run or build downloaded code."""
import argparse
import hashlib
import io
from pathlib import Path, PurePosixPath
import tarfile
import urllib.request

SHA = "8674c204ea7020321bc94682265305380c4367aa"
ARCHIVE_SHA256 = "48b4061cbcea7525d837a7d1ddd2047fda1c085e125c5374ac73bc841bd6ef8e"
URL = "https://api.github.com/repos/Cloud-Temple/mcp-tools/tarball/" + SHA
MAX_ARCHIVE = 32 * 1024 * 1024
MAX_EXTRACTED = 128 * 1024 * 1024


def extract(data, destination):
    if len(data) > MAX_ARCHIVE or hashlib.sha256(data).hexdigest() != ARCHIVE_SHA256:
        raise ValueError("Tools archive mismatch; no fallback or automatic repin")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        if sum(m.size for m in members) > MAX_EXTRACTED or len(members) > 10000:
            raise ValueError("Archive budget exceeded")
        roots = set()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError("Unsafe archive path")
            if not member.isfile() and not member.isdir():
                raise ValueError("Only regular files/directories accepted")
            roots.add(path.parts[0])
        if len(roots) != 1:
            raise ValueError("One archive root required")
        destination.mkdir(mode=0o700, parents=False, exist_ok=False)
        for member in members:
            parts = PurePosixPath(member.name).parts[1:]
            if not parts:
                continue
            output = destination.joinpath(*parts)
            if member.isdir():
                output.mkdir(parents=True, exist_ok=True)
            else:
                output.parent.mkdir(parents=True, exist_ok=True)
                with output.open("xb") as f:
                    f.write(archive.extractfile(member).read())
                output.chmod(0o755 if member.mode & 0o111 else 0o644)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    with urllib.request.urlopen(URL, timeout=60) as response:
        data = response.read(MAX_ARCHIVE + 1)
    extract(data, args.destination)
    print("Public Tools source verified: " + SHA)
