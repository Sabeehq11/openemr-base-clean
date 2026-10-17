"""Stand-in for `docker compose -f <file> config --format json`, for the deploy-script tests only.

Reproduces how Compose 2.38 normalises a published port, as captured from the real tool (the last test in
test_w2_deploy_script_unit.py re-checks this against real Compose when it is installed):

    "127.0.0.1:8400:8400" -> {"host_ip": "127.0.0.1", "target": 8400, "published": "8400"}
    "0.0.0.0:8400:8400"   -> {"host_ip": "0.0.0.0", ...}
    "[::]:8400:8400"      -> {"host_ip": "::", ...}
    "8400:8400"           -> {"target": 8400, "published": "8400"}   (no host_ip: bound to every interface)
    "8400"                -> {"target": 8400}                          (no host_ip, random host port)
"""
import json
import re
import sys


def normalise(spec: str) -> dict:
    port: dict = {"mode": "ingress"}
    if spec.startswith("["):
        host, _, rest = spec[1:].partition("]:")
        port["host_ip"], parts = host, rest.split(":")
    else:
        parts = spec.split(":")
        if len(parts) == 3:
            port["host_ip"], parts = parts[0], parts[1:]
    port["target"] = int(parts[-1])
    if len(parts) == 2:
        port["published"] = parts[0]
    return port


def main(path: str) -> None:
    text = open(path).read()
    # The quoted entries of the one-line `ports: [...]` list. Quoting is required so an IPv6 "[::1]:..." is not
    # mistaken for the end of the list.
    found = re.search(r"ports:\s*\[(.*)\]\s*$", text, re.MULTILINE)
    specs = re.findall(r"\"([^\"]+)\"|'([^']+)'", found.group(1)) if found else []
    ports = [normalise(a or b) for a, b in specs]
    print(json.dumps({"name": "agent", "services": {"agent": {"ports": ports}}}))


if __name__ == "__main__":
    main(sys.argv[1])
