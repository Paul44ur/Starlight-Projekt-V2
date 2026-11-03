"""The security tools the Absolution module knows (modules/absolution).

Only defensive, reconnaissance, scanning and reverse-engineering tools are here - the kind
used to test your OWN systems, systems you are authorized to test, files you already have,
or to watch your own network. Each entry is data, not code: a key, the program names to
look for, how its command line is built, what its one argument is, and a plain note.

What is deliberately NOT here (see ``BLOCKED``): credential theft, lateral movement, command
and control, poisoning and evasion. Starlight will not drive those; ``run`` refuses them by
name with a short message. Leaving them out is the point - a confirmation prompt does not
make building an attack platform acceptable.
"""

from __future__ import annotations

from dataclasses import dataclass

# The kind of the one argument a tool takes:
#   "host"   a host, IP, CIDR or URL on the network -> must be in your authorized scope
#   "path"   a file or folder on this PC (a pcap, a memory image, a source tree) -> must exist
#   "image"  a container image reference (trivy)
#   "query"  a search or a query string (searchsploit, osquery)
#   "none"   no argument of its own (extra flags only)
Primary = str  # one of the five above


@dataclass(frozen=True)
class Tool:
    key: str
    name: str  # as people write it
    category: str  # recon, web, vuln, cloud, code, dfir, re
    exes: tuple[str, ...]  # program names to look for on PATH (any one is enough)
    primary: Primary = "host"
    # The command line, as a list; "{t}" is where the one argument goes, "{w}" a wordlist.
    run: tuple[str, ...] = ("{t}",)
    allow_extra: bool = True  # may the model add extra flags (each a safe single token)?
    need_extra: bool = False  # the tool is useless without them (searchsploit terms)
    note: str = ""


# GUI programs Starlight only opens for you (like opening Notepad); it does not drive them.
@dataclass(frozen=True)
class Launch:
    key: str
    name: str
    category: str
    exes: tuple[str, ...]
    note: str = ""


RUN: tuple[Tool, ...] = (
    # -- reconnaissance ------------------------------------------------------------------
    Tool(
        "nmap", "Nmap", "recon", ("nmap",), run=("-sV", "-Pn", "{t}"), note="port and service scan"
    ),
    Tool(
        "masscan",
        "Masscan",
        "recon",
        ("masscan",),
        run=("-p1-1000", "{t}"),
        note="fast port scan (often needs admin rights)",
    ),
    Tool(
        "netdiscover",
        "Netdiscover",
        "recon",
        ("netdiscover",),
        run=("-P", "-r", "{t}"),
        note="find hosts on a local network (give the range)",
    ),
    Tool(
        "theharvester",
        "theHarvester",
        "recon",
        ("theHarvester", "theharvester"),
        run=("-b", "duckduckgo", "-d", "{t}"),
        note="open-source info about a domain",
    ),
    Tool(
        "amass",
        "Amass",
        "recon",
        ("amass",),
        run=("enum", "-passive", "-d", "{t}"),
        note="subdomains of a domain (passive)",
    ),
    Tool(
        "subfinder",
        "Subfinder",
        "recon",
        ("subfinder",),
        run=("-silent", "-d", "{t}"),
        note="subdomains of a domain",
    ),
    Tool(
        "assetfinder",
        "Assetfinder",
        "recon",
        ("assetfinder",),
        run=("--subs-only", "{t}"),
        note="subdomains of a domain",
    ),
    # -- web -----------------------------------------------------------------------------
    Tool(
        "httpx",
        "httpx",
        "web",
        ("httpx",),
        run=("-silent", "-u", "{t}"),
        note="which web servers answer, and how",
    ),
    Tool(
        "whatweb", "WhatWeb", "web", ("whatweb",), run=("{t}",), note="what a website is built with"
    ),
    Tool(
        "nikto",
        "Nikto",
        "web",
        ("nikto",),
        run=("-h", "{t}"),
        note="known problems on a web server",
    ),
    Tool(
        "nuclei",
        "Nuclei",
        "web",
        ("nuclei",),
        run=("-silent", "-u", "{t}"),
        note="checks a URL against known-issue templates",
    ),
    Tool(
        "gobuster",
        "Gobuster",
        "web",
        ("gobuster",),
        run=("dir", "-q", "-u", "{t}", "-w", "{w}"),
        note="find hidden paths on a site (needs a wordlist)",
    ),
    Tool(
        "dirsearch",
        "Dirsearch",
        "web",
        ("dirsearch",),
        run=("-u", "{t}"),
        note="find hidden paths on a site",
    ),
    Tool(
        "ffuf",
        "Ffuf",
        "web",
        ("ffuf",),
        run=("-u", "{t}", "-w", "{w}"),
        note="fuzz a URL (put FUZZ in it; needs a wordlist)",
    ),
    Tool(
        "feroxbuster",
        "Feroxbuster",
        "web",
        ("feroxbuster",),
        run=("-u", "{t}"),
        note="find hidden paths on a site",
    ),
    Tool(
        "sqlmap",
        "SQLmap",
        "web",
        ("sqlmap",),
        run=("-u", "{t}", "--batch"),
        note="test a URL for SQL injection (authorized web apps only)",
    ),
    # -- known vulnerabilities (local lookup) --------------------------------------------
    Tool(
        "searchsploit",
        "SearchSploit",
        "vuln",
        ("searchsploit",),
        primary="none",
        run=(),
        need_extra=True,
        note="search the local Exploit-DB copy (give search terms)",
    ),
    # -- cloud and code ------------------------------------------------------------------
    Tool(
        "trivy",
        "Trivy",
        "cloud",
        ("trivy",),
        primary="image",
        run=("image", "{t}"),
        note="scan a container image for known issues",
    ),
    Tool(
        "checkov",
        "Checkov",
        "cloud",
        ("checkov",),
        primary="path",
        run=("-q", "-d", "{t}"),
        note="check infrastructure-as-code in a folder",
    ),
    Tool(
        "kube-bench",
        "kube-bench",
        "cloud",
        ("kube-bench",),
        primary="none",
        run=("run",),
        note="check a Kubernetes node against the CIS benchmark",
    ),
    Tool(
        "prowler",
        "Prowler",
        "cloud",
        ("prowler",),
        primary="none",
        run=(),
        need_extra=True,
        note="cloud security checks (give the provider, e.g. aws)",
    ),
    Tool(
        "scoutsuite",
        "ScoutSuite",
        "cloud",
        ("scout", "scoutsuite"),
        primary="none",
        run=(),
        need_extra=True,
        note="cloud security review (give the provider, e.g. aws)",
    ),
    Tool(
        "semgrep",
        "Semgrep",
        "code",
        ("semgrep",),
        primary="path",
        run=("--error", "--config", "auto", "{t}"),
        note="find bugs and risks in source code",
    ),
    Tool(
        "bandit",
        "Bandit",
        "code",
        ("bandit",),
        primary="path",
        run=("-r", "{t}"),
        note="find security issues in Python code",
    ),
    Tool(
        "dependency-check",
        "OWASP Dependency-Check",
        "code",
        ("dependency-check", "dependency-check.sh"),
        primary="path",
        run=("--scan", "{t}"),
        note="find known-vulnerable libraries a project uses",
    ),
    # -- forensics and incident response (files you already have) ------------------------
    Tool(
        "tshark",
        "tshark (Wireshark)",
        "dfir",
        ("tshark",),
        primary="path",
        run=("-r", "{t}"),
        note="read a capture file (.pcap)",
    ),
    Tool(
        "zeek",
        "Zeek",
        "dfir",
        ("zeek",),
        primary="path",
        run=("-r", "{t}"),
        note="turn a capture file into connection logs",
    ),
    Tool(
        "suricata",
        "Suricata",
        "dfir",
        ("suricata",),
        primary="path",
        run=("-r", "{t}"),
        note="run detection rules over a capture file",
    ),
    Tool(
        "volatility",
        "Volatility",
        "dfir",
        ("vol", "vol.py", "volatility3", "volatility"),
        primary="path",
        run=("-f", "{t}", "windows.info"),
        note="read a memory image (change the plugin with extra)",
    ),
    Tool(
        "osquery",
        "osquery",
        "dfir",
        ("osqueryi",),
        primary="query",
        run=("--json", "{t}"),
        note="ask this PC a question in SQL (e.g. 'select * from logged_in_users')",
    ),
    Tool(
        "yara",
        "YARA",
        "dfir",
        ("yara",),
        primary="path",
        run=("{t}",),
        need_extra=True,
        note="match rules (the first argument) against a file or folder (extra)",
    ),
    Tool(
        "sigma",
        "Sigma",
        "dfir",
        ("sigma",),
        primary="path",
        run=("convert", "{t}"),
        note="convert a Sigma rule for your SIEM",
    ),
)

LAUNCH: tuple[Launch, ...] = (
    Launch(
        "burpsuite",
        "Burp Suite",
        "web",
        ("burpsuite", "BurpSuiteCommunity", "BurpSuitePro"),
        note="web proxy; you drive it",
    ),
    Launch(
        "zaproxy", "OWASP ZAP", "web", ("zaproxy", "zap.sh", "ZAP"), note="web proxy; you drive it"
    ),
    Launch("wireshark", "Wireshark", "dfir", ("wireshark",), note="packet capture; you drive it"),
    Launch(
        "ghidra", "Ghidra", "re", ("ghidraRun", "ghidra"), note="reverse engineering; you drive it"
    ),
    Launch(
        "ida",
        "IDA",
        "re",
        ("ida64", "ida", "ida64.exe", "ida.exe"),
        note="reverse engineering; you drive it",
    ),
    Launch(
        "x64dbg", "x64dbg", "re", ("x64dbg", "x32dbg", "x64dbg.exe"), note="debugger; you drive it"
    ),
)

# Named in the request but not wired, with the reason (so ``tools`` can say why, instead of
# "not installed"). These are whole operating systems or long-running services/agents, not
# one-shot tools Starlight runs; you run them yourself.
NOT_WIRED: dict[str, str] = {
    "kali": "an operating system, not a tool",
    "parrot": "an operating system, not a tool",
    "blackarch": "an operating system, not a tool",
    "remnux": "an operating system, not a tool",
    "security onion": "a monitoring platform you run as a service",
    "wazuh": "a monitoring platform you run as a service",
    "splunk": "a log platform you run as a service",
    "snort": "an intrusion-detection service (for a capture file, use Suricata or Zeek)",
    "zeek-ids": "run Zeek as a service yourself; Absolution only reads a capture file",
    "falco": "a runtime-security agent you run as a service",
    "osquery-fleet": "a service; Absolution uses osqueryi for one-off questions",
    "sysmon": "a Windows logging driver you install yourself",
    "velociraptor": "an incident-response server you run yourself",
    "grr": "an incident-response server you run yourself",
    "cuckoo": "a malware sandbox you run as a service",
    "misp": "a threat-intel server you run yourself",
    "thehive": "a case-management server you run yourself",
    "cortex": "an analysis server you run yourself",
    "sonarqube": "a code-quality server you run yourself",
    "openvas": "a scanner you run as a service (web UI)",
    "greenbone": "a scanner you run as a service (web UI)",
    "nessus": "a scanner you run as a service (web UI)",
    "qualys": "a hosted scanner (web UI)",
    "acunetix": "a scanner you run as a service (web UI)",
    "invicti": "a scanner you run as a service (web UI)",
    "crowdstrike falcon": "a commercial agent you manage in its own console",
    "shodan": "use the shodan CLI or website with your own API key",
    "fingerprintjs": "a browser library, not a command",
    "wappalyzer": "a browser add-on; for the command line use WhatWeb or httpx",
    "kismet": "a wireless service you run yourself",
}

# Offensive tooling Starlight will not drive at all. Trying to run one of these (by key or
# a close name) is refused in code, with this list as the reason. Not a safety by omission:
# a deliberate, stated boundary.
BLOCKED: dict[str, str] = {
    name: "credential theft, lateral movement, command-and-control or evasion - Starlight "
    "does not drive offensive tooling"
    for name in (
        "mimikatz",
        "responder",
        "rubeus",
        "certipy",
        "crackmapexec",
        "cme",
        "netexec",
        "nxc",
        "evil-winrm",
        "evilwinrm",
        "impacket",
        "secretsdump",
        "psexec",
        "wmiexec",
        "smbexec",
        "metasploit",
        "msfconsole",
        "msfvenom",
        "meterpreter",
        "cobaltstrike",
        "cobalt-strike",
        "beef",
        "bloodhound",
        "sharphound",
        "hashcat",
        "john",
        "johntheripper",
        "hydra",
        "medusa",
        "cewl",
        "commix",
        "bettercap",
        "ettercap",
        "wifite",
        "wifipumpkin",
        "wifi-pumpkin",
        "aircrack",
        "aircrack-ng",
        "chisel",
        "ligolo",
        "ligolo-ng",
        "proxychains",
        "tor",
        "sliver",
        "havoc",
        "mythic",
        "empire",
        "pupy",
        "koadic",
        "nishang",
        "powersploit",
        "seatbelt",
        "winpeas",
        "linpeas",
        "kerbrute",
    )
}


def by_key() -> dict[str, Tool]:
    return {t.key: t for t in RUN}


def launch_by_key() -> dict[str, Launch]:
    return {g.key: g for g in LAUNCH}


_ALL_KEYS: set[str] = {t.key for t in RUN} | {g.key for g in LAUNCH}
assert len(_ALL_KEYS) == len(RUN) + len(LAUNCH), "a tool key is used twice"
