"""
Refresh src/cloud_ranges.txt, the cloud data-center address list that src/scanners.py uses to tell
email link scanners from readers.

Why: GA (2026-10-14 to 10-17) showed about 40% of sessions coming from data centers (Council Bluffs
= Google Cloud, Moses Lake / Des Moines = Azure, Ashburn / Boardman = AWS, Singapore). Those are the
security gateways of corporate and ISP mailboxes opening every link in an email a few seconds after
delivery, in a real Chrome or Edge, so the user agent gives them away no better than a person. They
cast the yes/no feedback votes, clicked the debt-settlement offer on the /thank-you wall, and fired
GA events. A reader at home is on a residential ISP or a phone carrier, never on these ranges.

Sources (each provider's own published list; all four are public and need no key):
  Google Cloud  https://www.gstatic.com/ipranges/cloud.json      (customer ranges, not goog.json:
                Google's own consumer addresses stay out)
  AWS           https://ip-ranges.amazonaws.com/ip-ranges.json
  Azure         the "Azure IP Ranges and Service Tags - Public Cloud" download, tag AzureCloud
  Oracle Cloud  https://docs.oracle.com/en-us/iaas/tools/public_ip_ranges.json

Run monthly (providers add ranges; a stale list only misses new scanners, never blocks a reader):
  python3 tools/update_cloud_ranges.py
"""
import ipaddress, json, os, re, sys, urllib.request, datetime

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "cloud_ranges.txt")
UA = {"User-Agent": "Mozilla/5.0 (seniordailybenefits.com cloud-range refresh)"}
AZURE_PAGE = "https://www.microsoft.com/en-us/download/details.aspx?id=56519"


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read().decode("utf-8")


def gcp():
    return [p.get("ipv4Prefix") or p.get("ipv6Prefix") for p in json.loads(get("https://www.gstatic.com/ipranges/cloud.json"))["prefixes"]]


def aws():
    j = json.loads(get("https://ip-ranges.amazonaws.com/ip-ranges.json"))
    return [p["ip_prefix"] for p in j["prefixes"]] + [p["ipv6_prefix"] for p in j["ipv6_prefixes"]]


def azure():
    m = re.search(r'https://download\.microsoft\.com/[^"]+ServiceTags_Public[^"]*\.json', get(AZURE_PAGE))
    if not m:
        raise RuntimeError("Azure Service Tags download link not found on " + AZURE_PAGE)
    j = json.loads(get(m.group(0)))
    return [x for v in j["values"] if v["name"] == "AzureCloud" for x in v["properties"]["addressPrefixes"]]


def oracle():
    j = json.loads(get("https://docs.oracle.com/en-us/iaas/tools/public_ip_ranges.json"))
    return [c["cidr"] for r in j["regions"] for c in r["cidrs"]]


def main():
    lines, counts = [], {}
    for name, fn in (("gcp", gcp), ("aws", aws), ("azure", azure), ("oracle", oracle)):
        nets = [ipaddress.ip_network(x, strict=False) for x in fn() if x]
        v4 = ipaddress.collapse_addresses(n for n in nets if n.version == 4)
        v6 = ipaddress.collapse_addresses(n for n in nets if n.version == 6)
        merged = list(v4) + list(v6)
        if len(merged) < 50:  # a provider changed its format; keep the old file rather than drop it
            sys.exit(f"{name}: only {len(merged)} ranges, refusing to write {OUT}")
        counts[name] = len(merged)
        lines += [f"{name} {n}" for n in merged]
    with open(OUT, "w") as f:
        f.write(f"# generated {datetime.date.today().isoformat()} by tools/update_cloud_ranges.py: "
                + ", ".join(f"{k} {v}" for k, v in counts.items()) + "\n")
        f.write("\n".join(lines) + "\n")
    print(OUT, counts)


if __name__ == "__main__":
    main()
