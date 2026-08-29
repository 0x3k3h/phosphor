"""Generate the DNS records a domain needs to send/receive mail through
Phosphor, and verify them live."""
from __future__ import annotations

import dns.exception
import dns.resolver

from ..config import settings
from ..crypto.dkim import dkim_txt_value, public_b64_from_txt
from ..models import Domain

_resolver = dns.resolver.Resolver()
_resolver.timeout = 4
_resolver.lifetime = 6


def _txt_values(name: str) -> list[str]:
    try:
        answers = _resolver.resolve(name, "TXT")
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.exception.DNSException):
        return []
    out = []
    for rdata in answers:
        out.append(b"".join(rdata.strings).decode("utf-8", "replace"))
    return out


def _mx_hosts(name: str) -> list[tuple[int, str]]:
    try:
        answers = _resolver.resolve(name, "MX")
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.exception.DNSException):
        return []
    return sorted((r.preference, str(r.exchange).rstrip(".").lower()) for r in answers)


def _a_records(name: str) -> list[str]:
    out: list[str] = []
    for rtype in ("A", "AAAA"):
        try:
            out += [str(r) for r in _resolver.resolve(name, rtype)]
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.exception.DNSException):
            continue
    return out


def build_records(domain: Domain) -> list[dict]:
    host = settings.server_hostname
    selector = domain.dkim_selector or settings.dkim_selector
    dkim_val = dkim_txt_value(domain.dkim_public_key) if domain.dkim_public_key else "(generate a DKIM key first)"
    dmarc_rua = f"mailto:postmaster@{domain.name}"

    return [
        {
            "kind": "mx",
            "host": f"{domain.name}.",
            "type": "MX",
            "priority": 10,
            "value": f"{host}.",
            "required": True,
            "help": "Routes inbound mail for this domain to your Phosphor server.",
        },
        {
            "kind": "a",
            "host": f"{host}.",
            "type": "A",
            "value": "<this server's public IPv4>",
            "required": True,
            "help": "The mail hostname must resolve to this box. Add AAAA too if you have IPv6.",
        },
        {
            "kind": "spf",
            "host": f"{domain.name}.",
            "type": "TXT",
            "value": f"v=spf1 mx a:{host} -all",
            "required": True,
            "help": "Authorises this server to send for the domain. Use ~all while testing.",
        },
        {
            "kind": "dkim",
            "host": f"{selector}._domainkey.{domain.name}.",
            "type": "TXT",
            "value": dkim_val,
            "required": True,
            "help": "Publishes the public half of the domain's DKIM key so receivers can verify signatures.",
        },
        {
            "kind": "dmarc",
            "host": f"_dmarc.{domain.name}.",
            "type": "TXT",
            "value": f"v=DMARC1; p=quarantine; rua={dmarc_rua}; adkim=s; aspf=s; fo=1",
            "required": True,
            "help": "Tells receivers what to do with mail that fails SPF/DKIM, and where to send reports.",
        },
        {
            "kind": "ptr",
            "host": "<reverse zone of your public IP>",
            "type": "PTR",
            "value": f"{host}.",
            "required": True,
            "help": "Set at your hosting/VPS provider. Reverse DNS must match the mail hostname or most inboxes reject you.",
        },
        {
            "kind": "autoconfig",
            "host": f"autoconfig.{domain.name}.",
            "type": "CNAME",
            "value": f"{host}.",
            "required": False,
            "help": "Optional: lets Thunderbird/Apple Mail auto-discover IMAP/SMTP once you add a real IMAP daemon.",
        },
    ]


def check_domain(domain: Domain) -> dict:
    """Return {kind: {"ok": bool, "detail": str, "found": ...}} for each record."""
    host = settings.server_hostname.lower()
    selector = domain.dkim_selector or settings.dkim_selector
    result: dict[str, dict] = {}

    # MX
    mx = _mx_hosts(domain.name)
    mx_hosts = [h for _p, h in mx]
    result["mx"] = {
        "ok": host in mx_hosts,
        "detail": f"found: {', '.join(mx_hosts) or 'none'}",
    }

    # A record of the mail host
    a = _a_records(host)
    result["a"] = {"ok": bool(a), "detail": f"{host} -> {', '.join(a) or 'no A/AAAA record'}"}

    # SPF
    spf = [v for v in _txt_values(domain.name) if v.lower().startswith("v=spf1")]
    spf_ok = any(("mx" in v or host in v) for v in spf)
    result["spf"] = {"ok": spf_ok and len(spf) == 1, "detail": spf[0] if spf else "no SPF record"}

    # DKIM
    dkim_name = f"{selector}._domainkey.{domain.name}"
    dkim_txts = [v for v in _txt_values(dkim_name) if "DKIM1" in v or v.startswith("v=DKIM1") or "p=" in v]
    published = public_b64_from_txt(dkim_txts[0]) if dkim_txts else None
    result["dkim"] = {
        "ok": bool(published) and published == domain.dkim_public_key,
        "detail": "matches" if published == domain.dkim_public_key else
                  ("published key differs from stored key" if published else "no DKIM record"),
    }

    # DMARC
    dmarc = [v for v in _txt_values(f"_dmarc.{domain.name}") if v.lower().startswith("v=dmarc1")]
    result["dmarc"] = {"ok": bool(dmarc), "detail": dmarc[0] if dmarc else "no DMARC record"}

    # PTR
    ptr_ok = False
    ptr_detail = "could not resolve this server's public IP"
    try:
        my_ips = _a_records(host)
        for ip in my_ips:
            try:
                rev = dns.resolver.resolve_address(ip)
                names = [str(r).rstrip(".").lower() for r in rev]
                ptr_detail = f"{ip} -> {', '.join(names)}"
                if host in names:
                    ptr_ok = True
                    break
            except dns.exception.DNSException:
                ptr_detail = f"{ip} has no PTR record"
    except Exception as exc:  # noqa: BLE001
        ptr_detail = str(exc)
    result["ptr"] = {"ok": ptr_ok, "detail": ptr_detail}

    result["_summary"] = {
        "ok": all(result[k]["ok"] for k in ("mx", "spf", "dkim", "dmarc")),
    }
    return result
