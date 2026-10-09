"""Which devices are on your home network: "what's on my WiFi?", "call 192.168.1.23 the
printer", "tell me when a new device shows up".

It reads the PC's neighbour table (the devices it has recently talked to) and, if you ask,
pings every address of the PC's own home network (/24) first so quiet devices answer too.
Only the private network this PC is in is ever looked at - there is no way to point it at
other addresses. Names you give devices are kept in data/modules/lan/devices.json.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import subprocess
import sys
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.jsonfile import load_json, save_json

MAC = re.compile(r"([0-9a-f]{2}[:-]){5}[0-9a-f]{2}", re.IGNORECASE)
IPV4 = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
SWEEP_LIMIT = 64  # pings at once


class Config(BaseModel):
    watch: bool = False
    watch_minutes: float = Field(default=10, ge=1)
    sweep_on_watch: bool = True


def own_networks() -> list[ipaddress.IPv4Network]:
    """The private /24 networks this PC has an address in."""
    addresses: set[str] = set()
    try:
        import psutil

        for entries in psutil.net_if_addrs().values():
            addresses.update(e.address for e in entries if e.family == socket.AF_INET)
    except ImportError:
        pass
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.168.0.1", 9))  # no packet is sent; picks the LAN interface
            addresses.add(probe.getsockname()[0])
        except OSError:
            pass
    networks = []
    for address in addresses:
        try:
            ip = ipaddress.IPv4Address(address)
        except ValueError:
            continue
        if ip.is_private and not ip.is_loopback and not ip.is_link_local:
            networks.append(ipaddress.IPv4Network(f"{ip}/24", strict=False))
    return sorted(set(networks))


def parse_neighbours(text: str) -> dict[str, str]:
    """ip -> MAC from `arp -a` (Windows) or `ip neigh` / /proc/net/arp (Linux)."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        ip, mac = IPV4.search(line), MAC.search(line)
        if not ip or not mac:
            continue
        address = mac.group(0).lower().replace("-", ":")
        if address in ("ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00"):
            continue
        found[ip.group(1)] = address
    return found


class LanModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.file = ctx.data_dir / "devices.json"
        if self.config.watch:
            ctx.every(self.config.watch_minutes * 60, self.check, "watch", first_delay=60)

    # -- storage -------------------------------------------------------------------------

    def _known(self) -> dict[str, dict[str, Any]]:
        return load_json(self.file, {})

    def _save(self, known: dict[str, dict[str, Any]]) -> None:
        save_json(self.file, known)

    # -- looking at the network (replaced in tests) -------------------------------------------

    def networks(self) -> list[ipaddress.IPv4Network]:
        return own_networks()

    def neighbours(self) -> dict[str, str]:
        if sys.platform == "win32":
            command = ["arp", "-a"]
        else:
            try:
                with open("/proc/net/arp", encoding="utf-8") as table:
                    return parse_neighbours(table.read())
            except OSError:
                command = ["ip", "neigh"]
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ToolError(f"could not read the network table: {exc}") from exc
        return parse_neighbours(done.stdout)

    async def ping(self, address: str) -> bool:
        once = ["-n", "1", "-w", "700"] if sys.platform == "win32" else ["-c", "1", "-W", "1"]
        command = ["ping", *once, address]
        flags = 0x08000000 if sys.platform == "win32" else 0
        try:
            process = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                creationflags=flags,
            )  # fmt: skip
            return await asyncio.wait_for(process.wait(), 5) == 0
        except (OSError, TimeoutError):
            return False

    async def _sweep(self) -> int:
        """Ping every address of the PC's own home network(s), politely."""
        networks = self.networks()
        if not networks:
            raise ToolError("this PC is not on a private home network")
        gate = asyncio.Semaphore(SWEEP_LIMIT)

        async def one(address: str) -> bool:
            async with gate:
                self.ctx.kill_switch.check()
                return await self.ping(address)

        hosts = [str(h) for n in networks for h in n.hosts()]
        answers = await asyncio.gather(*(one(h) for h in hosts))
        return sum(answers)

    # -- tools ---------------------------------------------------------------------------

    @tool
    async def devices(self, sweep: bool = False) -> dict[str, Any]:
        """The devices on your home network (address, hardware id, the name you gave them,
        when first seen). `sweep` first pings the whole home network so quiet devices show
        up too (takes a few seconds)."""
        answered = await self._sweep() if sweep else None
        networks = self.networks()
        seen = {
            ip: mac
            for ip, mac in (await asyncio.to_thread(self.neighbours)).items()
            if any(ipaddress.IPv4Address(ip) in n for n in networks)
        }
        known = self._known()
        now = datetime.now().isoformat(timespec="minutes")
        listing = []
        for ip, mac in sorted(seen.items(), key=lambda pair: ipaddress.IPv4Address(pair[0])):
            entry = known.setdefault(mac, {"first_seen": now})
            entry.update({"ip": ip, "last_seen": now})
            listing.append(
                {
                    "ip": ip,
                    "mac": mac,
                    "name": entry.get("name", ""),
                    "first_seen": entry["first_seen"],
                }
            )
        self._save(known)
        result: dict[str, Any] = {
            "network": ", ".join(str(n) for n in networks),
            "count": len(listing),
            "devices": listing,
        }
        if answered is not None:
            result["answered_ping"] = answered
        return result

    @tool
    def name_device(self, device: str, name: str) -> dict[str, Any]:
        """Give a device a name, by its address (192.168.1.23) or hardware id."""
        known = self._known()
        key = device.strip().lower()
        mac = (
            key if key in known else next((m for m, e in known.items() if e.get("ip") == key), None)
        )
        if mac is None:
            raise ToolError(f"no device '{device}' seen yet; run lan.devices first")
        known[mac]["name"] = " ".join(name.split())[:60]
        self._save(known)
        return {"named": mac, "name": known[mac]["name"]}

    @tool
    def forget(self, device: str) -> dict[str, Any]:
        """Forget a device (its name and first-seen date)."""
        known = self._known()
        key = device.strip().lower()
        mac = (
            key if key in known else next((m for m, e in known.items() if e.get("ip") == key), None)
        )
        if mac is None:
            raise ToolError(f"no device '{device}'")
        del known[mac]
        self._save(known)
        return {"forgotten": mac}

    async def check(self) -> list[dict[str, Any]]:
        """One round of the watcher: warn about devices never seen before."""
        before = set(self._known())
        if not before:  # the first round only learns what is there
            await self.devices(sweep=self.config.sweep_on_watch)
            return []
        result = await self.devices(sweep=self.config.sweep_on_watch)
        new = [d for d in result["devices"] if d["mac"] not in before]
        for device in new:
            await self.ctx.publish("lan.new_device", device)
            await self.ctx.deliver(
                f"A new device joined your network: {device['ip']} ({device['mac']}). "
                "If you don't know it, check your WiFi.",
                title="Network",
                urgent=True,
            )
        return new
