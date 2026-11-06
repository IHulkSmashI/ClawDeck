#!/usr/bin/env python3
"""Build step: put nitec's FSR 4.1.2 XMX files into the plugin zip (out/ClawDeck/bin/fsr4-xmx), from where
assets/versions.json "fsr4_xmx" says they are hosted, each sha256 checked. A file that doesn't match stops the build.
Usage: tools/fetch_fsr4.py <destination folder>"""
import hashlib, json, os, sys, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(dest: str) -> int:
    entry = json.load(open(os.path.join(ROOT, "assets", "versions.json")))["fsr4_xmx"]
    os.makedirs(dest, exist_ok=True)
    for name, sha in entry["files"].items():
        url = entry["base"].rstrip("/") + "/" + name
        path = os.path.join(dest, name)
        with urllib.request.urlopen(url, timeout=300) as r, open(path, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        got = hashlib.sha256(open(path, "rb").read()).hexdigest()
        if got != sha:
            print(f"{name}: sha256 {got} doesn't match {sha}", file=sys.stderr)
            return 1
        print(f"{name}: ok ({os.path.getsize(path)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
