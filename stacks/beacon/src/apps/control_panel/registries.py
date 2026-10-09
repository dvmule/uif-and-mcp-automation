# registries.py
# The four registries behind the panel (design doc §10.2). Sources, formats
# and channels are YAML seeds; destinations are author-owned records
# (UIFPE-241): persisted in the registries DynamoDB table when
# BEACON_TABLE_REGISTRIES is set (in-memory only for local dev/tests — a
# Lambda's memory is lost on every cold start), with the credential written
# to Secrets Manager at registration and NEVER stored or returned (D16).

from __future__ import annotations

import itertools
import json
import os
from pathlib import Path

import yaml

_DATA = Path(__file__).parent / "registry_data"
_dest_seq = itertools.count(1)
_destinations: list[dict] = []


def _load(name: str) -> list[dict]:
    with open(_DATA / f"{name}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _resolve_env_path(source: dict) -> dict:
    """One image serves every environment, but a source's physical location
    can differ per environment (sandbox: eds_dev_gold_product; dev: the echo
    stack's uif_echo_dev_gold_product). path_by_env carries the map; BEACON_ENV
    picks the entry, `path` is the fallback. The map itself never leaves the
    registry — callers and the UI see a single resolved path."""
    resolved = dict(source)
    by_env = resolved.pop("path_by_env", None)
    if by_env:
        env = os.environ.get("BEACON_ENV", "local")
        resolved["path"] = by_env.get(env, resolved["path"])
    return resolved


def sources(formats: list[str] | None = None) -> list[dict]:
    """Source registry, optionally filtered to sources eligible for ALL the
    requested formats (D17 — the ONIX rule)."""
    rows = [_resolve_env_path(s) for s in _load("sources")]
    if formats:
        rows = [s for s in rows
                if all(f in s.get("eligible_formats", []) for f in formats)]
    return rows


_glue = None
_s3 = None
_clients_initialized = False


def set_clients_for_testing(glue, s3) -> None:
    global _glue, _s3, _clients_initialized
    _glue, _s3 = glue, s3
    # (None, None) resets to lazy boto3 init; anything else is an injection
    _clients_initialized = not (glue is None and s3 is None)


def _aws():
    global _glue, _s3, _clients_initialized
    if not _clients_initialized:
        import boto3
        _glue, _s3 = boto3.client("glue"), boto3.client("s3")
        _clients_initialized = True
    return _glue, _s3


def source_columns(source_id: str) -> dict:
    """The source's attributes, LIVE from the source itself — so a new
    column appears in the mapping tab with no registry edit and no
    deployment. Registry `columns` is only the offline fallback.

    - iceberg sources: Glue catalog GetTable (path: glue_catalog.<db>.<table>)
    - csv sources: header row of the first object under the S3 prefix
    Framework metadata columns (leading underscore, e.g. _gold_updated_at)
    are maintained by the platform and excluded from authoring.

    Live iceberg sources also carry `types`: {column: <Glue type string>},
    raw as catalogued (string, bigint, decimal(10,2), date, timestamp,
    array<string>, array<struct<...>>) — the wizard's row-filter builder
    picks its operators from it. A CSV header carries no types and the
    registry fallback has none, so the key is ABSENT there: absent means
    unknown, one shape for every caller.
    """
    source = next((s for s in sources() if s["id"] == source_id), None)
    if source is None:
        # UIFPE-229: catalog-discovered gold tables have no registry row —
        # their id (<domain>.<table>) resolves against the configured
        # databases instead
        source = _gold_source(source_id)
    if source is None:
        return {"error": f"unknown source {source_id!r}"}

    try:
        glue, s3 = _aws()
        types: dict[str, str] = {}
        if source["kind"] == "iceberg":
            _, database, table = source["path"].split(".", 2)
            response = glue.get_table(DatabaseName=database, Name=table)
            described = response["Table"]["StorageDescriptor"]["Columns"]
            names = [c["Name"] for c in described]
            types = {c["Name"]: c["Type"] for c in described if c.get("Type")}
        elif source["kind"] == "csv":
            bucket, _, prefix = source["path"].removeprefix("s3://").partition("/")
            listing = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=5)
            key = next(o["Key"] for o in listing.get("Contents", [])
                       if not o["Key"].endswith("/"))
            head = s3.get_object(Bucket=bucket, Key=key,
                                 Range="bytes=0-8191")["Body"].read().decode("utf-8", "replace")
            names = [c.strip().strip('"') for c in head.splitlines()[0].split(",")]
        else:
            raise ValueError(f"no introspection for kind {source['kind']!r}")
        columns = [n for n in names if n and not n.startswith("_")]
        result = _apply_eligibility(source, {"source_id": source_id,
                                             "columns": columns,
                                             "provenance": "live"})
        if types:
            result["types"] = {n: types[n] for n in result["columns"] if n in types}
        return result
    except Exception as e:  # offline / no permissions / source missing
        fallback = [c for c in source.get("columns", []) if not c.startswith("_")]
        result = {"source_id": source_id,
                  "columns": fallback,
                  "provenance": "registry",
                  "reason": f"live introspection unavailable ({type(e).__name__})"}
        # UIFPE-569: a table (or database) the catalog does not have is not a
        # transient outage — a registry-listed Gold source whose first load
        # has not landed. Said apart so callers stop telling the author to
        # "try again shortly". Matched by exception NAME, like gold_tables().
        if type(e).__name__ == "EntityNotFoundException":
            result["missing"] = True
        return _apply_eligibility(source, result)


def _apply_eligibility(source: dict, result: dict) -> dict:
    """Drop columns the entity's gold master permits on NO channel (UIFPE-540).

    Beacon is the message channel, so a column marked `x-channels: []` — rule
    N7's source-system surrogates — must never be offered for authoring. The
    answer comes from the master, not from a list kept here.

    Applied to the registry fallback as well as the live list: both product
    rows in sources.yaml begin `inventory_item_id, organization_code`, so
    filtering only the live path would leave the offline picker leaking.

    `excluded` is reported rather than silently dropped so validation can say
    WHY a reference is refused. `eligibility: "unverified"` marks a configured
    bucket whose master could not be read — a broken deployment. An absent
    bucket adds nothing at all, so this is inert until Terraform sets it.
    """
    from control_panel import channel_eligibility

    entity = channel_eligibility.entity_for_path(source.get("path"))
    denied, status = channel_eligibility.excluded_columns(entity, result["columns"])
    if status == channel_eligibility.UNREADABLE:
        result["eligibility"] = "unverified"
    elif denied:
        result["columns"] = [c for c in result["columns"] if c not in denied]
        result["excluded"] = sorted(denied)
    # UIFPE-562: the same master's per-attribute `description`, for the
    # wizard to show — verbatim, never authored here; absent when unknown
    descriptions = channel_eligibility.column_descriptions(entity, result["columns"])
    if descriptions:
        result["descriptions"] = descriptions
    return result


# ---- gold catalog discovery (UIFPE-229) --------------------------------------
# The wizard's source picker reads the Glue catalog LIVE: pick a domain, see
# that domain's gold tables. The catalog is the truth — no registry file to
# go stale (the sources.yaml rot of UIFPE-220 cannot recur here). Which
# domains are offered is the ONE piece of config: BEACON_GOLD_DATABASES,
# set by Terraform from var.gold_databases — the same list that grants Lake
# Formation access, so the picker can never offer a domain the pipeline
# cannot actually read.

#: The platform's CDC watermark column (ADR-0003 D7): its presence is what
#: makes a table eligible for incremental extraction (UIFPE-168).
PLATFORM_WATERMARK = "_gold_updated_at"

#: ADR-0003 D5 names a gold entity's CDC change log "{entity}_changes"
#: (changes_table_name(), in the gold transform's gold_cdc.py).
CHANGE_LOG_SUFFIX = "_changes"


def _change_log_names(table_names: set[str]) -> set[str]:
    """The CDC change logs among a database's tables — kept OUT of the picker
    (UIFPE-369 has the full case). A change log is key + verdict with no
    entity attributes, so a feed driven by one delivers nothing a partner
    wants; and it carries PLATFORM_WATERMARK, so listed it would earn an
    "incremental ready" badge that is true of the table and wrong about the
    feed. Derived, not suffix-matched: "<x>_changes" counts only when "<x>"
    is in the same database, so an entity that happens to be named that way
    keeps its card. Hidden from the PICKER only — _gold_source() still
    resolves the id, so the designed full-CDC slice can read one."""
    return {n for n in table_names
            if n.endswith(CHANGE_LOG_SUFFIX)
            and n[:-len(CHANGE_LOG_SUFFIX)] in table_names}


def gold_databases() -> dict[str, str]:
    """{domain: database} from BEACON_GOLD_DATABASES (comma-separated, e.g.
    "uif_echo_dev_gold_product,uif_echo_dev_gold_program"). The domain name is the
    part after "gold_" — the platform's database naming convention."""
    out: dict[str, str] = {}
    for db in os.environ.get("BEACON_GOLD_DATABASES", "").split(","):
        db = db.strip()
        if db:
            out[db.rsplit("gold_", 1)[-1]] = db
    return out


def gold_domains() -> list[dict]:
    """The domains an author can pick from, with their gold databases."""
    return [{"domain": d, "database": db}
            for d, db in sorted(gold_databases().items())]


def gold_tables(domain: str) -> list[dict] | None:
    """The domain's gold tables, live from the Glue catalog — shaped like
    registry source rows so everything downstream (column introspection,
    mapping config, incremental gating) works unchanged. CDC change logs are
    left out (UIFPE-369 — see _change_log_names). None = unknown domain; a
    Glue failure raises (the API turns it into a 503 the UI can show, rather
    than silently offering nothing)."""
    database = gold_databases().get(domain)
    if database is None:
        return None
    glue, _ = _aws()
    listed, token = [], None
    while True:
        kwargs = {"DatabaseName": database}
        if token:
            kwargs["NextToken"] = token
        try:
            page = glue.get_tables(**kwargs)
        except Exception as e:
            # UIFPE-167: a configured database that the platform has not
            # created yet (a domain listed ahead of its first gold load) is an
            # empty picker entry, not a catalog outage — matched by exception
            # NAME so the fakes and boto3 behave identically. Anything else
            # still raises and the API answers 503.
            if type(e).__name__ == "EntityNotFoundException":
                return []
            raise
        listed.extend(page.get("TableList", []))
        token = page.get("NextToken")
        if not token:
            break

    # every page first: whether "<x>_changes" is a change log depends on
    # "<x>" being in the database, which a single page cannot answer
    change_logs = _change_log_names({t["Name"] for t in listed})
    grains = _load("tables") or {}   # UIFPE-607: what a row is, per table
    tables = []
    for t in listed:
        if t["Name"] in change_logs:
            continue
        cols = [c["Name"] for c in
                t.get("StorageDescriptor", {}).get("Columns", [])]
        tables.append({
            **grains.get(t["Name"], {}),
            "id": f"{domain}.{t['Name']}",
            "path": f"glue_catalog.{database}.{t['Name']}",
            "kind": "iceberg",
            "domain": domain,
            "columns_count": len([c for c in cols if not c.startswith("_")]),
            # platform-owned: presence of the CDC column IS the
            # incremental eligibility — no registry entry needed
            "watermark_column": (PLATFORM_WATERMARK
                                 if PLATFORM_WATERMARK in cols else None),
            "eligible_formats": ["csv", "json", "xml"],
            "sensitivity": "internal",
            "provenance": "catalog",
        })
    return tables


def _gold_source(source_id: str) -> dict | None:
    """Resolve a catalog-discovered source id (<domain>.<table>) without a
    registry row — the id carries everything needed to derive the path."""
    domain, _, table = source_id.partition(".")
    database = gold_databases().get(domain)
    if not database or not table:
        return None
    return {"id": source_id, "kind": "iceberg",
            "path": f"glue_catalog.{database}.{table}"}


def source_id_for_path(path: str | None) -> str | None:
    """UIFPE-167: the source id a mapping-config path resolves to — the
    reverse of _gold_source, for validation. A sources.yaml row wins (matched
    on its BEACON_ENV-resolved path, the same precedence as
    watermark_column_for_path); else a catalog path glue_catalog.<db>.<table>
    whose database is one of BEACON_GOLD_DATABASES resolves to
    <domain>.<table>, the id gold_tables() lists and source_columns() accepts.
    None = not a source this deployment knows, so callers must not guess."""
    if not path:
        return None
    row = next((r for r in sources() if r.get("path") == path), None)
    if row:
        return row["id"]
    try:
        catalog, database, table = path.split(".", 2)
    except ValueError:
        return None
    if catalog != "glue_catalog" or not table:
        return None
    domain = next((d for d, db in gold_databases().items() if db == database), None)
    return f"{domain}.{table}" if domain else None


def watermark_column_for_path(path: str) -> str | None:
    """The watermark column for a source path: the registry row's declared
    value when one exists (sources.yaml), else detected live from the Glue
    catalog for configured gold databases (the CDC column's presence is the
    platform convention). None = the source cannot run incrementally."""
    row = next((r for r in sources() if r.get("path") == path), None)
    if row and row.get("watermark_column"):
        return row["watermark_column"]
    try:
        _, database, table = path.split(".", 2)
    except ValueError:
        return None
    if database not in gold_databases().values():
        return None
    try:
        glue, _ = _aws()
        cols = [c["Name"] for c in glue.get_table(DatabaseName=database, Name=table)
                ["Table"]["StorageDescriptor"]["Columns"]]
        return PLATFORM_WATERMARK if PLATFORM_WATERMARK in cols else None
    except Exception:
        return None  # unknowable ≠ eligible: incremental stays refused


def formats() -> list[dict]:
    return _load("formats")


def relationships() -> list[dict]:
    """UIFPE-580: how one Gold table relates to another — the join (and any
    list spread) Beacon fills in when an author adds the related table to a
    feed. Keyed by Gold TABLE name; `{table}` placeholders in the SQL stand
    for the feed's aliases and are substituted by the wizard (UIFPE-581).
    A fixed, owner-maintained list by decision (2026-09-24): relationships
    are declared, never inferred from column names."""
    return _load("relationships")


def format_ids() -> set[str]:
    return {f["id"] for f in formats()}


def channels() -> list[dict]:
    return _load("channels")


# -- destinations (UIFPE-241) --------------------------------------------------
# DynamoDB row shape: {registry: "destinations", id: <dest_id>, record: <json>}
# — the whole record as one JSON document, so the per-channel shapes (s3 /
# email / sftp) evolve without table migrations. Same env-gated raw-client
# pattern as scheduler.py.

_dest_ddb = None
_dest_secrets = None
_dest_lambda = None
_dest_clients_initialized = False


def set_destination_clients_for_testing(ddb, secrets, lam=None) -> None:
    global _dest_ddb, _dest_secrets, _dest_lambda, _dest_clients_initialized
    _dest_ddb, _dest_secrets, _dest_lambda = ddb, secrets, lam
    _dest_clients_initialized = not (ddb is None and secrets is None)


def _dest_aws():
    global _dest_ddb, _dest_secrets, _dest_lambda, _dest_clients_initialized
    if not _dest_clients_initialized:
        import boto3
        session = boto3.Session()
        _dest_ddb = session.client("dynamodb")
        _dest_secrets = session.client("secretsmanager")
        _dest_lambda = session.client("lambda")
        _dest_clients_initialized = True
    return _dest_ddb, _dest_secrets, _dest_lambda


def _dest_table() -> str | None:
    return os.environ.get("BEACON_TABLE_REGISTRIES") or None


def _secret_ref_for(dest_id: str) -> str:
    # Terraform supplies the prefix that matches the IAM scoping — the UIF
    # convention's slash style, uif/beacon/{env}/destinations (UIFPE-266);
    # the fallback keeps local dev sane.
    #
    # UIFPE-606: keyed by the destination's ID, not its name. A name-keyed
    # secret let two destinations with the same name share — and overwrite —
    # one credential, and it would stop matching the name after a rename.
    # The id never changes and never repeats, which is also what Keeper's
    # exact-name sync wants. Destinations created before this keep their
    # name-keyed secret: every reader uses the STORED secret_ref, never this.
    prefix = os.environ.get("BEACON_DESTINATION_SECRET_PREFIX",
                            "uif/beacon/local/destinations")
    return f"{prefix}/{dest_id}"


def _name_key(name) -> str:
    return str(name or "").strip().casefold()


def _name_clash(name: str, exclude_id: str | None = None) -> dict | None:
    """UIFPE-606: another destination already called `name` (trimmed,
    case-insensitive), across ALL destinations whatever their visibility —
    a private row still owns its name. None when the name is free."""
    key = _name_key(name)
    return next((d for d in destinations()
                 if d.get("id") != exclude_id and _name_key(d.get("name")) == key),
                None)


def _name_taken(name: str) -> str:
    return (f"a destination named {name.strip()!r} already exists — "
            "pick another name")


# ---- destination visibility (UIFPE-546) --------------------------------------
# A destination is private (owner only) or public (everyone sees and uses it).
# The owner comes from the shell session's verified `sub` (UIFPE-487) and is
# stamped at creation. Rows written before this feature carry neither field
# and read as PUBLIC — the migration-free rollout: nothing disappears from
# anyone's screen on deploy day. With auth off there is no caller identity,
# so nothing is filtered and everything stays manageable (local dev, tests).

VISIBILITIES = ("private", "public")


def destination_visible(row: dict, username: str | None) -> bool:
    """May this caller SEE the row in lists and pickers?"""
    if not username:  # auth off — no identity to filter by
        return True
    if row.get("visibility", "public") == "public" or not row.get("owner"):
        return True
    return row["owner"] == username


def destination_manageable(row: dict, username: str | None) -> bool:
    """May this caller edit / delete / re-test the row? Public means usable
    by all, manageable by its owner; a legacy row without an owner stays
    manageable by everyone until someone edits it (which stamps them)."""
    if not username or not row.get("owner"):
        return True
    return row["owner"] == username


def _not_owner(dest: dict) -> tuple[None, str, int]:
    return None, (f"only its owner ({dest.get('owner')}) can change this "
                  "destination"), 403


def _put_destination(record: dict) -> None:
    if _dest_table() is None:
        _destinations.append(record)
        return
    ddb, _, _ = _dest_aws()
    ddb.put_item(TableName=_dest_table(),
                 Item={"registry": {"S": "destinations"},
                       "id": {"S": record["id"]},
                       "record": {"S": json.dumps(record)}})


def destinations() -> list[dict]:
    if _dest_table() is None:
        return list(_destinations)
    ddb, _, _ = _dest_aws()
    rows, key = [], None
    while True:
        kwargs = {"TableName": _dest_table(),
                  "KeyConditionExpression": "registry = :r",
                  "ExpressionAttributeValues": {":r": {"S": "destinations"}}}
        if key:
            kwargs["ExclusiveStartKey"] = key
        page = ddb.query(**kwargs)
        rows += [json.loads(item["record"]["S"]) for item in page.get("Items", [])]
        key = page.get("LastEvaluatedKey")
        if not key:
            return rows


def get_destination(dest_id: str) -> dict | None:
    if _dest_table() is None:
        return next((d for d in _destinations if d["id"] == dest_id), None)
    ddb, _, _ = _dest_aws()
    item = ddb.get_item(TableName=_dest_table(),
                        Key={"registry": {"S": "destinations"},
                             "id": {"S": dest_id}}).get("Item")
    return json.loads(item["record"]["S"]) if item else None


def add_destination(body: dict, owner: str | None = None) -> tuple[dict | None, str | None]:
    """Create an author-owned destination (D16). Returns (record, error).
    The credential is accepted, forwarded (Secrets Manager in the deployed
    service) and NEVER stored or returned — only the secret ref is kept.
    UIFPE-546: `owner` is the verified shell username (None when auth is
    off); the author chooses private/public, defaulting to private when
    there is an owner to be private TO."""
    visibility = body.get("visibility") or ("private" if owner else "public")
    if visibility not in VISIBILITIES:
        return None, f"visibility must be one of {list(VISIBILITIES)}"
    known = {c["id"]: c for c in channels()}
    channel = known.get(body.get("channel"))
    if channel is None:
        return None, f"unknown channel {body.get('channel')!r} (registered: {sorted(known)})"
    if channel.get("status") != "available":
        return None, f"channel {body['channel']!r} is not available yet ({channel.get('status')})"

    needs_credential = channel.get("requires_credential", True)
    if channel["id"] == "s3":
        required = ("name", "destination_s3_prefix")
    elif channel["id"] == "email":
        required = ("name", "recipients")
    else:
        required = ("name", "host", "remote_dir") + (("credential",) if needs_credential else ())
    for key in required:
        if not body.get(key) or (key == "name" and not str(body[key]).strip()):
            return None, f"missing required field: {key}"
    name = str(body["name"]).strip()
    if _name_clash(name):  # UIFPE-606: names are unique
        return None, _name_taken(name)

    # random suffix, not a counter: ids must survive Lambda cold starts and
    # concurrent instances (the in-memory counter did neither)
    import uuid
    record = {
        "id": f"dest-{uuid.uuid4().hex[:8]}",
        "name": name,
        "channel": body["channel"],
        "domain": body.get("domain", "unscoped"),
        # pending until a connection/access test passes; the deployed
        # service runs it from inside the VPC and flips this to "verified"
        "status": "pending_connection_test",
        "visibility": visibility,
    }
    if owner:
        record["owner"] = owner
    if channel["id"] == "s3":
        dest = str(body["destination_s3_prefix"])
        if not dest.startswith("s3://"):
            return None, "destination_s3_prefix must be an s3:// URI"
        # IAM-authenticated: no secret is created for S3 destinations
        record["destination_s3_prefix"] = dest
    elif channel["id"] == "email":
        recipients = body["recipients"]
        if not isinstance(recipients, list) or not recipients:
            return None, "recipients must be a non-empty list"
        for r in recipients:
            if not isinstance(r, str) or "@" not in r or "." not in r.split("@")[-1]:
                return None, f"invalid recipient address: {r!r}"
        record["recipients"] = recipients
        # UIFPE-444: the test sends a real mail through the Pearson
        # Notifications API — same pending → verified flow as sftp and s3
    else:
        if not str(body["remote_dir"]).startswith("/"):
            return None, "remote_dir must be an absolute remote path"
        credential = body.get("credential") or {}
        if needs_credential:
            if not isinstance(credential, dict) or not credential.get("username"):
                return None, "credential.username is required"
            if not (credential.get("password") or credential.get("private_key_pem")):
                return None, "credential needs a password or a private_key_pem"
        record.update({
            "host": body["host"],
            "port": int(body.get("port", 22)),
            "remote_dir": body["remote_dir"],
            "secret_ref": _secret_ref_for(record["id"]),
        })
        # The credential goes to Secrets Manager NOW and nowhere else (D16) —
        # the destination's connector reads it from there at transfer time.
        # The host key is NOT accepted from the author — it is pinned by the
        # connection test on first contact, never typed in.
        secret_value = _sftp_secret_value(record["host"], record["port"], credential)
        if _dest_table() is not None:  # local mode: no AWS, no secret write
            _, secrets, _ = _dest_aws()
            try:
                # UIFPE-606: create, never overwrite. The old "re-registering
                # a name rotates its credential in place" fallback is what let
                # a second destination with the same name silently replace
                # the first one's password. A secret already sitting at a
                # fresh id's path is refused, not reused.
                stored = secrets.create_secret(
                    Name=record["secret_ref"],
                    SecretString=json.dumps(secret_value))
            except secrets.exceptions.ResourceExistsException:
                return None, ("a credential secret already exists at "
                              f"{record['secret_ref']} — destination not "
                              "created (nothing was overwritten)")
            except Exception as e:
                return None, ("could not store the credential in Secrets "
                              f"Manager ({type(e).__name__}) — destination "
                              "not created")
            # the full ARN rides on the record: CreateConnector's UserSecretId
            # accepts only the ARN form, never the friendly name (UIFPE-339)
            if isinstance(stored, dict) and stored.get("ARN"):
                record["secret_arn"] = stored["ARN"]
            # UIFPE-339: SFTP destinations ride Transfer Family — the API
            # creates the connector alongside the secret (runtime data, like
            # schedules). Since UIFPE-438 there is no paramiko fallback: a
            # deployment without the access role refuses sftp registration
            # honestly instead of minting a destination nothing can deliver.
            from control_panel import transfer_connectors
            if not transfer_connectors.enabled():
                try:
                    secrets.delete_secret(SecretId=record["secret_ref"],
                                          ForceDeleteWithoutRecovery=True)
                except Exception:  # noqa: BLE001 — best effort
                    pass
                return None, ("this deployment has no Transfer Family access "
                              "role (BEACON_TRANSFER_ACCESS_ROLE_ARN) — sftp "
                              "destinations cannot be registered")
            try:
                record["connector_id"] = transfer_connectors.create_connector(record)
                record["adapter"] = "transfer_family"
            except Exception as e:
                # never leave a credential behind for a destination that
                # was refused — same no-orphaned-secret rule as delete
                try:
                    secrets.delete_secret(SecretId=record["secret_ref"],
                                          ForceDeleteWithoutRecovery=True)
                except Exception:  # noqa: BLE001 — best effort
                    pass
                return None, ("could not create the Transfer Family "
                              f"connector ({type(e).__name__}: {e}) — "
                              "destination not created")
    _put_destination(record)
    return record, None


def _sftp_secret_value(host: str, port: int, credential: dict) -> dict:
    """The Transfer Family connector reads the capitalized Username +
    Password/PrivateKey keys; host/port ride along for humans reading the
    secret. The lowercase credential duplication went with the Paramiko
    adapter (UIFPE-438) — secrets written before that keep both shapes
    harmlessly. One secret per destination, D16 unchanged."""
    value = {"host": host, "port": port,
             "Username": credential.get("username")}
    if credential.get("password"):
        value["Password"] = credential["password"]
    if credential.get("private_key_pem"):
        value["PrivateKey"] = credential["private_key_pem"]
    return value


def test_destination(dest_id: str,
                     username: str | None = None) -> tuple[dict | None, str | None, int]:
    """Run the connection test for an sftp destination (UIFPE-339): through
    the destination's Transfer Family connector, so it originates from the
    connector's own egress IPs — the addresses the partner allowlists. On
    first contact the server's host key is captured and pinned (record +
    connector TrustedHostKeys); subsequent tests and every delivery refuse
    any other key.

    Returns (result, error, http_status)."""
    dest = get_destination(dest_id)
    if dest is None:
        return None, f"unknown destination {dest_id!r}", 404
    if not destination_manageable(dest, username):  # UIFPE-546
        return _not_owner(dest)
    if dest.get("channel") == "s3":
        return _test_deliver_lambda(dest, {
            "action": "connection_test", "channel": "s3",
            "destination_s3_prefix": dest["destination_s3_prefix"]})
    if dest.get("channel") == "email":
        # UIFPE-444: a real mail to the destination's recipients — "verified"
        # means the Notifications API accepted the event
        return _test_deliver_lambda(dest, {
            "action": "connection_test", "channel": "email",
            "recipients": dest["recipients"]})
    if dest.get("channel") != "sftp":
        return None, "this channel has no connection test", 422

    # UIFPE-339: a connector destination tests through the SERVICE — the test
    # then originates from the connector's own egress IPs (what the partner
    # allowlists now), and first contact pins the host key into BOTH the
    # record (display/audit/config embed) and the connector's TrustedHostKeys
    # (the enforcement point — transfers are refused until a key is pinned).
    if dest.get("connector_id"):
        from control_panel import transfer_connectors
        try:
            result = transfer_connectors.test_connection(dest["connector_id"])
        except Exception as e:  # noqa: BLE001 — surfaced, never a bare 500
            return None, f"connection tester failed: {type(e).__name__}: {e}", 502
        # First contact (live finding): the service REFUSES to test an SFTP
        # connector with no TrustedHostKeys, but that refusal carries the
        # server's host key in the response — pin it (record + connector) and
        # test again, now against the pinned identity. Later tests never pin:
        # a changed server key must fail, not silently re-pin.
        pinned = False
        if result.get("host_key") and not dest.get("host_key"):
            try:
                transfer_connectors.pin_host_key(dest["connector_id"],
                                                 result["host_key"])
            except Exception as e:  # noqa: BLE001
                return None, ("the host key could not be pinned "
                              f"({type(e).__name__}) — re-test"), 502
            dest["host_key"] = result["host_key"]
            dest["host_key_type"] = result.get("host_key_type", "ssh-rsa")
            _replace_destination(dest)  # the pin survives a failed retest
            pinned = True
            if not result.get("ok"):
                try:
                    result = transfer_connectors.test_connection(
                        dest["connector_id"])
                except Exception as e:  # noqa: BLE001
                    return None, (f"connection tester failed after pinning: "
                                  f"{type(e).__name__}: {e}"), 502
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error"),
                    "status": dest.get("status")}, None, 200
        dest["status"] = "verified"
        _replace_destination(dest)
        return {"ok": True, "status": "verified",
                "host_key_pinned": pinned}, None, 200

    # UIFPE-438: the in-Lambda Paramiko tester is retired with its adapter.
    # An sftp destination without a connector predates UIFPE-339 — it cannot
    # be tested or delivered to anymore; re-registering it mints a connector.
    return None, ("this destination predates Transfer Family connectors and "
                  "its Paramiko path was retired (UIFPE-438) — register it "
                  "again to give it a connector"), 409


def _test_deliver_lambda(dest: dict, payload: dict) -> tuple[dict | None, str | None, int]:
    """UIFPE-440/444: run a destination's connection test INSIDE the deliver
    Lambda, so it executes under the DELIVER role and the deployment's real
    wiring (the identity partners grant for s3; the Notifications credentials
    for email) — running it here under the API role would prove nothing."""
    function = os.environ.get("BEACON_DELIVER_FUNCTION")
    if not function:
        return None, ("this deployment has no deliver-Lambda wiring "
                      "(BEACON_DELIVER_FUNCTION) — the connection test is "
                      "unavailable"), 503
    _, _, lam = _dest_aws()
    try:
        response = lam.invoke(FunctionName=function,
                              Payload=json.dumps(payload).encode())
        result = json.loads(response["Payload"].read())
    except Exception as e:  # noqa: BLE001 — surfaced, never a bare 500
        return None, f"connection tester failed: {type(e).__name__}: {e}", 502
    if response.get("FunctionError"):
        return None, ("connection tester failed: "
                      f"{result.get('errorType')}: "
                      f"{result.get('errorMessage')}"), 502
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error"),
                "status": dest.get("status")}, None, 200
    dest["status"] = "verified"
    _replace_destination(dest)
    return {"ok": True, "status": "verified"}, None, 200


def partner_policy(dest: dict) -> dict | None:
    """UIFPE-440: the exact bucket-policy statement a partner applies so
    Beacon can deliver into their bucket — shown on the panel with a copy
    button. Only for s3 destinations on buckets that are not our own data
    bucket (that one is covered by IAM, no grant needed); None when the
    deliver role ARN is not wired (local dev)."""
    if dest.get("channel") != "s3" or not dest.get("destination_s3_prefix"):
        return None
    role_arn = os.environ.get("BEACON_DELIVER_ROLE_ARN")
    if not role_arn:
        return None
    without_scheme = dest["destination_s3_prefix"].removeprefix("s3://")
    bucket, _, prefix = without_scheme.partition("/")
    if not bucket or bucket == os.environ.get("BEACON_DATA_BUCKET"):
        return None
    prefix = prefix.strip("/")
    resource = f"arn:aws:s3:::{bucket}/{prefix + '/' if prefix else ''}*"
    return {
        # GetObject is the delivery verify (the adapter heads the copied file
        # to prove the sizes match); DeleteObject is the connection test's
        # marker cleanup. Both scoped to the agreed prefix.
        "statement": {
            "Sid": "AllowBeaconDelivery",
            "Effect": "Allow",
            "Principal": {"AWS": role_arn},
            "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
            "Resource": resource,
        },
        "note": ("Also set the bucket's Object Ownership to \"Bucket owner "
                 "enforced\" (S3 console → bucket → Permissions). Without it, "
                 "delivered files can be owned by the sending account and "
                 "unreadable in yours."),
    }


def update_destination(dest_id: str, body: dict,
                       username: str | None = None) -> tuple[dict | None, str | None, int]:
    """Edit a destination (UIFPE-337). The channel is IMMUTABLE — a channel
    change is a different destination. The NAME is editable since UIFPE-606:
    it is a label. The secret was located once at creation and is read from
    the stored secret_ref ever after, exports point here by destination_id,
    and a rename must not collide with another destination's name. Editable
    per channel:
      s3    — destination_s3_prefix
      email — recipients, link_expiry_hours
      sftp  — host, port, remote_dir, credential (rotates the secret)

    sftp safety semantics: ANY sftp edit drops the status back to
    pending_connection_test (the change is unproven until re-tested), and a
    host/port change also CLEARS the pinned host key — the pin belongs to the
    old server's identity. Credential rotation applies to deliveries
    immediately (the secret is read live at delivery time); host/remote-dir
    changes reach published exports only via re-publish (D22).

    Returns (record, error, http_status)."""
    dest = get_destination(dest_id)
    if dest is None:
        return None, f"unknown destination {dest_id!r}", 404
    if not destination_manageable(dest, username):  # UIFPE-546
        return _not_owner(dest)
    if "channel" in body and body["channel"] != dest.get("channel"):
        return None, ("channel is immutable — create a new destination "
                      "instead"), 422
    # UIFPE-606: rename. Checked only when the name actually changes, so a
    # legacy pair that already shares a name can still edit other fields.
    new_name = dest.get("name")
    if "name" in body and str(body["name"] or "").strip() != dest.get("name"):
        new_name = str(body["name"] or "").strip()
        if not new_name:
            return None, "name cannot be blank", 422
        if _name_clash(new_name, exclude_id=dest_id):
            return None, _name_taken(new_name), 422

    # UIFPE-546: visibility is channel-independent and owner-editable. Editing
    # a legacy ownerless row also stamps the editor as its owner, so every
    # touched row gains one.
    if "visibility" in body:
        if body["visibility"] not in VISIBILITIES:
            return None, f"visibility must be one of {list(VISIBILITIES)}", 422
        dest["visibility"] = body["visibility"]
    if username and not dest.get("owner"):
        dest["owner"] = username

    channel = dest.get("channel")
    if channel == "s3":
        prefix = body.get("destination_s3_prefix", dest["destination_s3_prefix"])
        if not str(prefix).startswith("s3://"):
            return None, "destination_s3_prefix must be an s3:// URI", 422
        dest["destination_s3_prefix"] = str(prefix)
    elif channel == "email":
        recipients = body.get("recipients", dest.get("recipients"))
        if not isinstance(recipients, list) or not recipients:
            return None, "recipients must be a non-empty list", 422
        for r in recipients:
            if not isinstance(r, str) or "@" not in r or "." not in r.split("@")[-1]:
                return None, f"invalid recipient address: {r!r}", 422
        dest["recipients"] = recipients
        dest["link_expiry_hours"] = int(body.get("link_expiry_hours",
                                                 dest.get("link_expiry_hours", 24)))
    else:  # sftp
        new_host = str(body.get("host", dest["host"]))
        new_port = int(body.get("port", dest.get("port", 22)))
        new_dir = str(body.get("remote_dir", dest["remote_dir"]))
        if not new_dir.startswith("/"):
            return None, "remote_dir must be an absolute remote path", 422
        credential = body.get("credential")
        if credential is not None:
            if not isinstance(credential, dict) or not credential.get("username"):
                return None, "credential.username is required", 422
            if not (credential.get("password") or credential.get("private_key_pem")):
                return None, "credential needs a password or a private_key_pem", 422

        changed = (new_host != dest["host"] or new_port != dest.get("port", 22)
                   or new_dir != dest["remote_dir"] or credential is not None)
        server_changed = (new_host != dest["host"]
                          or new_port != dest.get("port", 22))

        if _dest_table() is not None and (credential is not None or server_changed):
            _, secrets, _ = _dest_aws()
            try:
                if credential is not None:
                    secret_value = _sftp_secret_value(new_host, new_port, credential)
                else:  # host/port moved, credential unchanged: merge into the secret
                    secret_value = json.loads(secrets.get_secret_value(
                        SecretId=dest["secret_ref"])["SecretString"])
                    secret_value["host"] = new_host
                    secret_value["port"] = new_port
                secrets.put_secret_value(SecretId=dest["secret_ref"],
                                         SecretString=json.dumps(secret_value))
            except Exception as e:
                return None, ("could not update the credential secret "
                              f"({type(e).__name__}) — destination unchanged"), 502

        # UIFPE-339: a moved server means repointing the connector too — and
        # clearing its trusted keys, since the pin belongs to the OLD server.
        # Transfers through the connector stay refused until the next test
        # re-pins, which is the same unproven-until-retested rule the status
        # flip below enforces on the record.
        if server_changed and dest.get("connector_id"):
            from control_panel import transfer_connectors
            try:
                transfer_connectors.update_connector_url(
                    dest["connector_id"],
                    {"host": new_host, "port": new_port,
                     "secret_ref": dest["secret_ref"]},
                    clear_host_keys=True)
            except Exception as e:  # noqa: BLE001
                return None, ("could not repoint the Transfer Family connector "
                              f"({type(e).__name__}) — destination unchanged"), 502

        dest["host"], dest["port"], dest["remote_dir"] = new_host, new_port, new_dir
        if server_changed:  # the pin belongs to the OLD server's identity
            dest.pop("host_key", None)
            dest.pop("host_key_type", None)
        if changed:  # unproven until the connection test passes again
            dest["status"] = "pending_connection_test"

    # applied last: every refusal above leaves the record untouched. A rename
    # alone changes no endpoint, so it never re-opens the connection test.
    dest["name"] = new_name
    _replace_destination(dest)
    return dest, None, 200


def delete_destination(dest_id: str,
                       username: str | None = None) -> tuple[dict | None, str | None, int]:
    """Delete a destination (UIFPE-337): the record AND, for sftp, its
    credential secret (7-day recovery window — a mistake is undoable).

    Order matters: the secret goes FIRST, and a secret-deletion failure keeps
    the record — the one outcome this feature exists to prevent is an
    orphaned credential nobody can see. Refused while any export's LATEST
    published version delivers here (deleting would break its next run);
    historic immutable versions never block.

    Returns (result, error, http_status)."""
    dest = get_destination(dest_id)
    if dest is None:
        return None, f"unknown destination {dest_id!r}", 404
    if not destination_manageable(dest, username):  # UIFPE-546
        return _not_owner(dest)

    from control_panel import lifecycle  # local: avoids an import cycle

    def _delivers_here(settings: dict) -> bool:
        # UIFPE-170: an export may carry several targets — the guard matches
        # the destination in ANY of them (settings.deliveries list), in the
        # single settings.delivery block, or via the top-level destination_id
        # the single-target wizard sends.
        targets = list(settings.get("deliveries") or [])
        if settings.get("delivery"):
            targets.append(settings["delivery"])
        if settings.get("destination_id") == dest_id:
            return True
        return any(
            t.get("destination_id") == dest_id
            or (dest.get("secret_ref")
                and t.get("secret_ref") == dest["secret_ref"])
            for t in targets if isinstance(t, dict))

    used_by = sorted(
        str(latest.get("name") or latest.get("job_id"))
        for latest in lifecycle.catalog()
        if _delivers_here(latest.get("settings") or {}))
    if used_by:
        return None, ("destination is delivering for published export(s) "
                      f"{used_by} — repoint or retire them first"), 409

    # UIFPE-339: the connector goes FIRST — like the secret, an orphaned
    # connector (still holding a valid credential reference) is the failure
    # mode to prevent, so a delete failure keeps the record visible.
    if dest.get("connector_id") and _dest_table() is not None:
        from control_panel import transfer_connectors
        try:
            transfer_connectors.delete_connector(dest["connector_id"])
        except Exception as e:  # noqa: BLE001
            return None, ("could not delete the Transfer Family connector "
                          f"({type(e).__name__}) — destination kept so the "
                          "connector cannot be orphaned"), 502

    secret_scheduled = False
    if (dest.get("channel") == "sftp" and dest.get("secret_ref")
            and _dest_table() is not None):  # local mode has no AWS to clean
        _, secrets, _ = _dest_aws()
        try:
            secrets.delete_secret(SecretId=dest["secret_ref"],
                                  RecoveryWindowInDays=7)
            secret_scheduled = True
        except Exception as e:
            # Two shapes of "the secret needs no deleting": already gone
            # (ResourceNotFound) or already scheduled for deletion
            # (InvalidRequest — e.g. a retry after a partial failure). Matched
            # by exception NAME so the fakes and boto3 behave identically.
            if type(e).__name__ not in ("ResourceNotFoundException",
                                        "InvalidRequestException"):
                return None, ("could not delete the credential secret "
                              f"({type(e).__name__}) — destination kept so "
                              "the secret cannot be orphaned"), 502

    if _dest_table() is None:
        _destinations[:] = [d for d in _destinations if d["id"] != dest_id]
    else:
        ddb, _, _ = _dest_aws()
        ddb.delete_item(TableName=_dest_table(),
                        Key={"registry": {"S": "destinations"},
                             "id": {"S": dest_id}})
    return {"deleted": True, "id": dest_id,
            "secret_scheduled_for_deletion": secret_scheduled}, None, 200


def _replace_destination(record: dict) -> None:
    if _dest_table() is None:
        for i, d in enumerate(_destinations):
            if d["id"] == record["id"]:
                _destinations[i] = record
                return
        _destinations.append(record)
        return
    _put_destination(record)


def reset_destinations() -> None:
    """Test hook."""
    _destinations.clear()
