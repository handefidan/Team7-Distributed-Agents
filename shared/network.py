"""Bind services to this machine's Tailscale address instead of 0.0.0.0.

Keeping the API off 0.0.0.0 means it's only reachable over the tailnet,
not the local LAN or the open internet.
"""

import os
import shutil
import subprocess


class TailscaleIPError(RuntimeError):
    pass


def get_tailscale_ip(env_var: str = "TAILSCALE_IP") -> str:
    """Return the Tailscale IPv4 address to bind to.

    Honors `env_var` (a literal IP, for when the `tailscale` CLI isn't
    available or you want to pin a specific address) before falling back
    to `tailscale ip -4`. Raises rather than falling back to 0.0.0.0, so a
    misconfigured machine fails loudly instead of exposing the API beyond
    the tailnet.
    """
    override = os.getenv(env_var)
    if override:
        return override

    tailscale = shutil.which("tailscale")
    if tailscale is None:
        raise TailscaleIPError(
            f"tailscale CLI not found and {env_var} is not set; install Tailscale "
            f"or set {env_var} to this machine's tailnet address"
        )

    try:
        result = subprocess.run([tailscale, "ip", "-4"], capture_output=True, text=True, timeout=5)
    except subprocess.TimeoutExpired as exc:
        raise TailscaleIPError("'tailscale ip -4' timed out") from exc

    if result.returncode != 0 or not result.stdout.strip():
        raise TailscaleIPError(
            f"'tailscale ip -4' failed ({result.stderr.strip() or 'no output'}); "
            "is Tailscale running and logged in?"
        )
    return result.stdout.strip().splitlines()[0]
