"""Gold alert mail (UIFPE-543).

A CloudWatch alarm state change reaches this Lambda - directly as an alarm
action, or wrapped in an SNS record - and leaves as one email per alarm to
every matching, enabled address in the alert-subscribers table, through the
Pearson Notifications API using the credential beacon's export mail already
uses. Who is told is a table row (self-service from the console), never a
Terraform change.

Alarms are per gold entity: the alarm's GoldId dimension says which table,
the subject names it, and a subscriber row may carry `gold_ids` to hear about
only those tables. A row without `gold_ids` hears about everything, including
the fleet-level alarms (dispatcher, dead-letter queues) that carry no entity.

Flat zip like the dispatcher's: gold_resources.py sits beside this file and
the pearson_notifications package directory sits at the zip root.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
from string import Template
from urllib.parse import quote

import boto3

from gold_resources import gold_alert_subscribers_table
from pearson_notifications import NotificationsClient, event_id

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get("REGION", "eu-west-1")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

dynamo = boto3.resource("dynamodb", region_name=REGION)
secrets = boto3.client("secretsmanager", region_name=REGION)


# -- what arrived ------------------------------------------------------------

def _gold_id_from_dimensions(dimensions) -> str | None:
    """GoldId from either dimension shape: the direct event's {name: value}
    map, or SNS's [{name, value}] list."""
    if isinstance(dimensions, dict):
        value = dimensions.get("GoldId")
    elif isinstance(dimensions, list):
        value = next((d.get("value") for d in dimensions
                      if isinstance(d, dict) and d.get("name") == "GoldId"), None)
    else:
        value = None
    return str(value) if value else None


def _gold_id_direct(config: dict) -> str | None:
    for metric in config.get("metrics") or []:
        stat = (metric or {}).get("metricStat") or {}
        found = _gold_id_from_dimensions((stat.get("metric") or {}).get("dimensions"))
        if found:
            return found
    return None


def parse_alarms(event) -> list[dict]:
    """Normalise every alarm transition in the event. Two shapes: the alarm
    invoking this function directly (source aws.cloudwatch + alarmData) and
    the classic SNS notification (Records[].Sns.Message JSON). Anything else
    yields nothing, never an exception."""
    if not isinstance(event, dict):
        return []
    if event.get("source") == "aws.cloudwatch" and isinstance(event.get("alarmData"), dict):
        data = event["alarmData"]
        state = data.get("state") or {}
        config = data.get("configuration") or {}
        return [{
            "name": data.get("alarmName") or "(unnamed alarm)",
            "gold_id": _gold_id_direct(config),
            "state": state.get("value") or "UNKNOWN",
            "previous": (data.get("previousState") or {}).get("value"),
            "reason": state.get("reason") or "",
            "time": state.get("timestamp") or event.get("time") or "",
            "description": config.get("description") or "",
            "region": event.get("region") or REGION,
        }]
    alarms = []
    for record in event.get("Records") or []:
        try:
            message = json.loads((record.get("Sns") or {}).get("Message") or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(message, dict) or "AlarmName" not in message:
            continue
        alarms.append({
            "name": message["AlarmName"],
            "gold_id": _gold_id_from_dimensions((message.get("Trigger") or {}).get("Dimensions")),
            "state": message.get("NewStateValue") or "UNKNOWN",
            "previous": message.get("OldStateValue"),
            "reason": message.get("NewStateReason") or "",
            "time": message.get("StateChangeTime") or "",
            "description": message.get("AlarmDescription") or "",
            "region": message.get("Region") or REGION,
        })
    return alarms


# -- who is told -------------------------------------------------------------

def _wants(item: dict, gold_id: str | None) -> bool:
    """A row without gold_ids hears everything. A row with gold_ids hears only
    those entities - and nothing fleet-level, which has no entity."""
    chosen = item.get("gold_ids")
    if not chosen:
        return True
    if gold_id is None:
        return False
    return gold_id in {str(g) for g in chosen}


def subscribers(table, gold_id: str | None = None) -> list[str]:
    """Every enabled, well-formed address whose entity filter admits this
    alarm. A row with no `enabled` attribute counts as enabled; a malformed
    address is logged and skipped rather than failing the whole mail."""
    items, kwargs = [], {}
    while True:
        page = table.scan(**kwargs)
        items.extend(page.get("Items") or [])
        if not page.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    out = []
    for item in items:
        if item.get("enabled") is False:
            continue
        address = str(item.get("email") or "").strip().lower()
        if not _EMAIL.match(address):
            logger.warning(f"[SUBSCRIBER SKIPPED] malformed address {address!r}")
            continue
        if not _wants(item, gold_id):
            continue
        if address not in out:
            out.append(address)
    return out


# -- what is said -------------------------------------------------------------

def condition(alarm: dict, env: str) -> str:
    """The alarm's condition in words: the name minus the estate prefix and
    the entity token. uif-echo-dev-gold-marketing-copy-run-failed -> 'run failed'."""
    rest = alarm["name"]
    prefix = f"uif-echo-{env}-gold-"
    if rest.startswith(prefix):
        rest = rest[len(prefix):]
    gold_id = alarm.get("gold_id")
    if gold_id:
        token = gold_id.replace("_", "-") + "-"
        if rest.startswith(token):
            rest = rest[len(token):]
    return rest.replace("-", " ").strip() or alarm["name"]


def console_link(alarm: dict) -> str:
    region = alarm["region"]
    return (f"https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}"
            f"#alarmsV2:alarm/{quote(alarm['name'], safe='')}")


# -- the Pearson brand shell --------------------------------------------------
#
# Adapted from stacks/beacon/src/apps/beacon_export/email_template.py, itself
# adapted from the SFS FileShare template. Kept as a Python string for the same
# reason beacon gives: this Lambda's zip ships *.py only, so an .html file would
# silently not deploy -- and here it is in THIS module rather than its own,
# because both zip assembly paths name their files explicitly (see the module
# docstring of the patch that introduced this).

_ROW = Template("""
                                        <tr>
                                            <td style="padding: 12px 0; ${border}">
                                                <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                                                    <tr>
                                                        <td width="150"
                                                            style="font-size: 15px; font-weight: 600; color: #0d004d; vertical-align: top; font-family: 'Plus Jakarta Sans', arial, sans-serif;">
                                                            ${label}
                                                        </td>
                                                        <td style="font-size: 15px; color: #0D004D; font-weight: 400; font-family: 'Plus Jakarta Sans', arial, sans-serif; line-height: 21px;">
                                                            ${value}
                                                        </td>
                                                    </tr>
                                                </table>
                                            </td>
                                        </tr>""")

_TEMPLATE = Template("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>${title}</title>
</head>
<body style="margin: 0; padding: 0; font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; background-color: #EDECF6;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color: #EDECF6;">
    <tr>
        <td align="center" style="padding: 40px 20px;">
            <table role="presentation" width="600" cellpadding="0" cellspacing="0"
                   style="background-color: #FFFFFF; border-radius: 20px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); max-width: 600px;">

                <tr>
                    <td style="background-color: #C1BFFF; padding: 6px 0; border-radius: 20px 20px 0 0;"></td>
                </tr>

                <tr>
                    <td align="center" style="padding: 30px 40px 10px 40px;">
                        <a href="https://www.pearson.com/en-gb.html">
                            <img src="https://ukschpgs.pearson.com/l/749453/2025-04-09/57jszq/749453/1744191320vnZjKPDO/PEARSON_LOGO_PURPLE_RGB.png"
                                 alt="Pearson"
                                 style="max-width: 200px; outline: none; text-decoration: none; width: 200px;"
                                 width="200"/>
                        </a>
                    </td>
                </tr>

                <tr>
                    <td style="padding: 10px 40px 4px 40px;">
                        <p style="margin: 0; font-size: 16px; line-height: 21px; color: #0D004D; font-family: 'Plus Jakarta Sans', arial, sans-serif;">
                            ${headline}
                        </p>
                    </td>
                </tr>

                <tr>
                    <td style="padding: 12px 40px 16px 40px;">
                        <span style="display: inline-block; background-color: ${state_bg}; color: ${state_fg}; border-radius: 30px; padding: 4px 16px; font-size: 14px; font-weight: 600; font-family: 'Plus Jakarta Sans', arial, sans-serif;">
                            ${state_label}
                        </span>
                    </td>
                </tr>

                <tr>
                    <td style="padding: 0 40px 30px 40px;">
                        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                               style="background-color: #C1BFFF; border-radius: 20px;">
                            <tr>
                                <td style="padding: 20px;">
                                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">${rows}
                                    </table>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>

                <tr>
                    <td align="center" style="padding: 0 40px 30px 40px;">
                        <a href="${cta_url}"
                           style="display: inline-block; background-color: #0D004D; color: #ffffff; text-decoration: none; padding: 10px 32px; border-radius: 30px; font-size: 16px; font-weight: 600; text-align: center; font-family: 'Plus Jakarta Sans', arial, sans-serif; border: 2px solid #0D004D; line-height: 30px;">
                            ${cta_label}
                        </a>
                    </td>
                </tr>

                <tr>
                    <td style="padding: 0 40px 30px 40px;">
                        <p style="margin: 0; font-size: 13px; line-height: 18px; color: #512EAB; font-family: 'Plus Jakarta Sans', arial, sans-serif;">
                            ${footnote}
                        </p>
                    </td>
                </tr>

            </table>

            <table role="presentation" width="600" cellpadding="0" cellspacing="0"
                   style="background-color: #0d004d; max-width: 600px; margin-top: 0; border-radius: 0 0 20px 20px;">
                <tr>
                    <td align="center"
                        style="padding: 20px 20px 10px 20px; color: #ffffff; font-family: 'Plus Jakarta Sans', arial, sans-serif; font-size: 15px; line-height: 15px; text-align: center;">
                        Copyright &copy; 2026
                    </td>
                </tr>
                <tr>
                    <td align="center"
                        style="padding: 10px 20px 20px 20px; color: #ffffff; font-family: 'Plus Jakarta Sans', arial, sans-serif; font-size: 15px; line-height: 15px; text-align: center;">
                        Pearson Education Ltd&nbsp;|&nbsp;Registered No: 872828&nbsp;|&nbsp;80 Strand London WC2R 0RL
                    </td>
                </tr>
                <tr>
                    <td align="center"
                        style="padding: 0 20px 20px 20px; color: #ffffff; font-family: 'Plus Jakarta Sans', arial, sans-serif; font-size: 15px; line-height: 115%; text-align: center;">
                        <a href="https://www.pearson.com/privacy-center/privacy-notices.html"
                           style="color: #ffffff; text-decoration: none;">Privacy notice</a>
                        <span style="color: #ffffff;">&nbsp;|&nbsp;</span>
                        <a href="https://www.pearson.com/en-gb/legal-information/cookie-policy.html"
                           style="color: #ffffff; text-decoration: none;">Cookies</a>
                    </td>
                </tr>
            </table>
        </td>
    </tr>
</table>
</body>
</html>""")

#: State -> (pill background, pill text, headline, human label). Red is not in
#: the Pearson palette, so severity reads from the pill rather than from the
#: shell -- the brand top bar stays Light Purple in every state.
_STATES = {
    "ALARM": ("#B00020", "#FFFFFF", "A UIF gold job needs attention.", "Needs attention"),
    "OK": ("#1B7F3B", "#FFFFFF", "A UIF gold job has recovered.", "Recovered"),
    "INSUFFICIENT_DATA": ("#FFCE00", "#0D004D",
                          "A UIF gold job has stopped reporting data.", "No data"),
}


def _rows_html(rows: list[tuple[str, str]]) -> str:
    """The label/value rows, last one without a divider (beacon's card shape)."""
    out = []
    present = [(k, v) for k, v in rows if v]
    for i, (label, value) in enumerate(present):
        last = i == len(present) - 1
        out.append(_ROW.substitute(
            border="" if last else "border-bottom: 1px solid #FFFFFF;",
            label=html.escape(label),
            value=html.escape(value),
        ))
    return "".join(out)


def render_email(subject: str, state: str, rows: list[tuple[str, str]],
                 cta_url: str, footnote: str) -> str:
    """The branded body. Every dynamic value is escaped -- alarm reason strings
    quote metric values verbatim and descriptions are operator-authored."""
    bg, fg, headline, label = _STATES.get(
        state, ("#512EAB", "#FFFFFF", "A UIF gold job changed state.", state or "Unknown"))
    return _TEMPLATE.substitute(
        title=html.escape(subject),
        headline=html.escape(headline),
        state_bg=bg,
        state_fg=fg,
        state_label=html.escape(label),
        rows=_rows_html(rows),
        cta_url=html.escape(cta_url, quote=True),
        cta_label="Open in CloudWatch",
        footnote=html.escape(footnote),
    )


def render(alarm: dict, env: str) -> tuple[str, str]:
    """(subject, html body). The subject names the entity first, because that
    is what the reader scans for. Alarm text is operator-authored but still
    escaped: a reason string quotes metric values verbatim."""
    state = alarm["state"]
    what = condition(alarm, env)
    who = alarm.get("gold_id") or "gold"
    suffix = {"ALARM": "", "OK": " - recovered", "INSUFFICIENT_DATA": " - no data"}.get(state, f" - {state}")
    subject = f"[UIF gold {env}] {who}: {what}{suffix}"
    rows = [
        ("Entity", alarm.get("gold_id") or "(fleet-level, no entity)"),
        ("Condition", what),
        ("State", f"{state}" + (f" (was {alarm['previous']})" if alarm.get("previous") else "")),
        ("When", alarm["time"]),
        ("Why", alarm["reason"]),
        ("What it means", alarm["description"]),
        ("Alarm", alarm["name"]),
    ]
    body = render_email(
        subject=subject,
        state=state,
        rows=rows,
        cta_url=console_link(alarm),
        footnote=("You receive this because your address is subscribed to this gold table "
                  "(or to everything) in this environment. Change or remove it on the "
                  "Notifications page in the console."),
    )
    return subject, body


# -- entry point ---------------------------------------------------------------

def _client() -> NotificationsClient:
    for var in ("ALERT_NOTIFICATIONS_SECRET", "ALERT_NOTIFICATIONS_API_URL",
                "ALERT_NOTIFICATIONS_TOKEN_URL"):
        if not os.environ.get(var):
            raise RuntimeError(f"alert mail is not configured on this deployment ({var} is unset)")
    return NotificationsClient(
        secrets,
        secret_ref=os.environ["ALERT_NOTIFICATIONS_SECRET"],
        api_url=os.environ["ALERT_NOTIFICATIONS_API_URL"],
        token_url=os.environ["ALERT_NOTIFICATIONS_TOKEN_URL"])


def handler(event, context=None):
    env = os.environ.get("ENV") or "unknown"
    alarms = parse_alarms(event)
    if not alarms:
        logger.warning(f"[NO ALARM IN EVENT] keys={sorted((event or {}).keys()) if isinstance(event, dict) else type(event).__name__}")
        return {"sent": 0, "reason": "no alarm in event"}

    table = dynamo.Table(os.environ.get("ALERT_SUBSCRIBERS_TABLE") or gold_alert_subscribers_table())
    client = None
    sent, unheard = [], []
    for alarm in alarms:
        recipients = subscribers(table, alarm.get("gold_id"))
        if not recipients:
            # Loud on purpose: an alarm fired and nobody is listed for it. This
            # string is what the dashboard's log widget surfaces.
            logger.warning(f"[NO SUBSCRIBERS] alarm={alarm['name']} gold_id={alarm.get('gold_id')} "
                           f"state={alarm['state']} - not mailed")
            unheard.append(alarm["name"])
            continue
        client = client or _client()
        subject, body = render(alarm, env)
        response = client.send_email(recipients=recipients, subject=subject, body_html=body)
        sent.append({"alarm": alarm["name"], "gold_id": alarm.get("gold_id"), "state": alarm["state"],
                     "recipients": len(recipients), "event_id": event_id(response)})
        logger.info(f"[ALERT MAILED] alarm={alarm['name']} gold_id={alarm.get('gold_id')} "
                    f"state={alarm['state']} recipients={len(recipients)} event_id={event_id(response)}")
    out = {"sent": len(sent), "alarms": sent}
    if unheard:
        out["reason"] = "no subscribers"
        out["unheard"] = unheard
    return out
