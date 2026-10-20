"""Closed fixture HBA precondition, not a general PostgreSQL HBA interpreter."""
import ipaddress
import re


def validate(snapshot, database, roles, neighbor):
    """Only simple literal rules; uncertainty before a reject barrier is refusal."""
    if set(snapshot) != {"roles", "databases", "hba", "hba_text", "hba_file", "acl", "superuser", "server_version_num"}:
        raise ValueError("Incomplete external preflight")
    if type(snapshot["server_version_num"]) is not int or not 170000 <= snapshot["server_version_num"] < 180000:
        raise ValueError("This copied recipe requires PostgreSQL17")
    if snapshot["superuser"] is not True:
        raise ValueError("This fixture requires an explicitly authorized superuser")
    if set(roles.values()) & set(snapshot["roles"]) or database in snapshot["databases"]:
        raise ValueError("Existing role or database: no adoption")
    if neighbor not in snapshot["databases"] or neighbor in (database, "postgres", "template1"):
        raise ValueError("Distinct existing neighbor required")
    if not isinstance(snapshot["acl"], list) or not isinstance(snapshot["hba"], list):
        raise ValueError("Incomplete external observations")
    lines = snapshot["hba_text"].splitlines()
    parsed = []
    previous = 0
    for row in snapshot["hba"]:
        # One source file only. Include/quote/group/hostname semantics are not guessed.
        number = row["line_number"]
        if (row.get("error") is not None or row["file_name"] != snapshot["hba_file"]
                or type(number) is not int or not previous < number <= len(lines)):
            raise ValueError("Unreadable or unsupported HBA source")
        previous = number
        text = lines[number - 1].split("#", 1)[0].strip()
        if not re.fullmatch(r"[a-zA-Z0-9_.,:/ \t-]+", text):
            raise ValueError("Quoted, group or meta HBA token unsupported")
        fields = text.split()
        kind = fields[0]
        if kind == "local" and len(fields) == 4:
            _, databases, users, method = fields
            family = "local"
            expected_address, expected_netmask = None, None
        elif kind in ("host", "hostssl", "hostnossl") and len(fields) == 5:
            _, databases, users, address, method = fields
            try:
                network = ipaddress.ip_network(address, strict=True)
            except ValueError as exc:
                raise ValueError("Literal universal HBA network required") from exc
            if network.prefixlen != 0:
                raise ValueError("Only universal fixture HBA networks supported")
            family = network.version
            expected_address, expected_netmask = str(network.network_address), str(network.netmask)
        else:
            raise ValueError("Unsupported HBA rule")
        dbs, names = databases.split(","), users.split(",")
        for token in (*dbs, *names):
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", token) or token in (
                    "sameuser", "samerole", "samegroup", "replication"):
                raise ValueError("Unsupported HBA token")
        if method not in ("reject", "scram-sha-256"):
            raise ValueError("Only reject/SCRAM fixture rules supported")
        # Match the public view against its exact unquoted source line.
        if (row["type"] != kind or row["database"] != dbs or row["user_name"] != names
                or row["auth_method"] != method or row["address"] != expected_address
                or row["netmask"] != expected_netmask):
            raise ValueError("HBA source/view mismatch")
        parsed.append((kind, family, dbs, names, method))
    if not parsed:
        raise ValueError("Missing HBA rules")
    # all requires evaluation for every existing non-domain database and a fresh name.
    others = set(snapshot["databases"])
    index = 0
    while "starter49_unrelated_" + str(index) in others | {database}:
        index += 1
    others.add("starter49_unrelated_" + str(index))
    others.discard(database)
    for role in (roles[key] for key in ("app", "install", "backup", "restore")):
        for family, tls in (("local", False), (4, True), (4, False), (6, True), (6, False)):
            for target in others:
                if first_match(parsed, role, target, family, tls) != "reject":
                    raise ValueError("First HBA match must reject every non-domain database")
        for family in (4, 6):
            if first_match(parsed, role, database, family, True) != "scram-sha-256":
                raise ValueError("Authorized domain TLS connection has no SCRAM rule")


def first_match(rules, role, database, family, tls):
    for kind, rule_family, databases, users, method in rules:
        if rule_family != family or not (role in users or "all" in users):
            continue
        if database not in databases and "all" not in databases:
            continue
        if kind == "hostssl" and not tls or kind == "hostnossl" and tls:
            continue
        return method
    return None
