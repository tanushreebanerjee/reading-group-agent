"""Share the display with everyone in the meeting through a free public tunnel (--share).

Instead of screen-sharing, paste the printed link in the Zoom chat: each person opens the live
display in their browser and can Reveal or Dismiss raised hands. The link carries a secret
key; without it the page is refused, and the control page is never reachable through it.

  cloudflared   Cloudflare quick tunnel, no account needed. Uses cloudflared from PATH, or
                downloads Cloudflare's official release to ~/.cache/rga/ on first use.
  localhost.run plain ssh, nothing to install (its TLS was unreliable in testing)
"""
from __future__ import annotations

import asyncio
import os
import platform
import re
import shutil
import tarfile
import urllib.request
from pathlib import Path

TOOLS = Path(os.path.expanduser("~/.cache/rga"))
CLOUDFLARED_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-{arch}.tgz"


def cloudflared_path() -> str:
    """cloudflared from PATH, else Cloudflare's release for this Mac, downloaded once."""
    found = shutil.which("cloudflared")
    if found:
        return found
    exe = TOOLS / "cloudflared"
    if not exe.exists():
        if platform.system() != "Darwin":
            raise RuntimeError("cloudflared not found; install it from https://github.com/cloudflare/cloudflared")
        arch = "arm64" if platform.machine() == "arm64" else "amd64"
        TOOLS.mkdir(parents=True, exist_ok=True)
        tgz = TOOLS / "cloudflared.tgz"
        print(f"[share] downloading cloudflared to {TOOLS} (one time)", flush=True)
        urllib.request.urlretrieve(CLOUDFLARED_URL.format(arch=arch), tgz)
        with tarfile.open(tgz) as t:
            member = next(m for m in t.getmembers() if m.name.endswith("cloudflared") and m.isfile())
            member.name = "cloudflared"
            t.extract(member, TOOLS)
        tgz.unlink()
        exe.chmod(0o755)
    return str(exe)

METHODS = {
    "cloudflared": (lambda port: [cloudflared_path(), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                    re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")),
    "localhost.run": (lambda port: ["ssh", "-T", "-o", "StrictHostKeyChecking=accept-new", "-o", "ServerAliveInterval=30",
                                    "-o", "ExitOnForwardFailure=yes", "-R", f"80:127.0.0.1:{port}", "nokey@localhost.run"],
                      re.compile(r"https://[a-z0-9-]+\.lhr\.life")),   # not the admin.localhost.run banner link
}


def pick_method(method: str = "auto") -> str:
    return "cloudflared" if method == "auto" else method


async def start_tunnel(port: int, method: str = "auto", timeout_s: float = 40) -> tuple[asyncio.subprocess.Process, str]:
    """Start the tunnel and return (process, public base URL). Raises RuntimeError on failure."""
    method = pick_method(method)
    if method not in METHODS:
        raise RuntimeError(f"unknown share method {method!r}; choose from {sorted(METHODS)}")
    cmd, url_re = METHODS[method]
    argv = await asyncio.to_thread(cmd, port)   # may download cloudflared the first time
    proc = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    seen: list[str] = []

    async def find_url() -> str:
        while True:
            line = await proc.stdout.readline()
            if not line:
                raise RuntimeError(f"{method} exited: " + " | ".join(seen[-5:]))
            text = line.decode(errors="replace").strip()
            seen.append(text)
            m = url_re.search(text)
            if m:
                return m.group(0)

    try:
        url = await asyncio.wait_for(find_url(), timeout_s)
    except (asyncio.TimeoutError, RuntimeError) as e:
        proc.kill()
        raise RuntimeError(f"could not start the {method} tunnel: {e}") from e

    async def drain():  # keep reading so the tunnel never blocks on a full pipe
        while await proc.stdout.readline():
            pass

    asyncio.get_running_loop().create_task(drain())
    return proc, url
