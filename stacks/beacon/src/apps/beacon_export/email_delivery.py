# email_delivery.py
# Email delivery channel via the Pearson Notifications API (UIFPE-444 —
# owner decision: Pearson product, not AWS SES). One event per batch:
# eventType FileShare.StandardEmail, BodyOfEmail = the branded Beacon
# template. NO presigned links ride in the mail — the Download button opens
# the export's page in the Beacon panel, which mints fresh 15-minute links
# when the recipient clicks Files.
#
# The transport (token mint + cache, the event POST, the 401 re-mint) moved
# to the shared platform package at UIFPE-543 so gold's alert mail rides the
# same pipes; this module keeps what is Beacon's — the template, the panel
# link, and the adapter API the deliver Lambda and the API call.

from __future__ import annotations

import html
import time

from pearson_notifications import NotificationsClient, event_id
from pearson_notifications.client import _token_cache  # noqa: F401 — tests reset it here

from beacon_export.email_template import TEMPLATE
from beacon_export.packaging import is_manifest_name


def render_email(*, export_name: str, job_id: str, batch_id: str,
                 file_names: list[str], download_link: str) -> tuple[str, str]:
    """(subject, html body) for one batch. Pure — unit-tested. File names and
    the export name are author/partner-visible text, so they are HTML-escaped
    before they enter the template."""
    ordered = ([n for n in file_names if not is_manifest_name(n)]
               + [n for n in file_names if is_manifest_name(n)])
    files_html = "<br/>".join(html.escape(n) for n in ordered)
    body = (TEMPLATE
            .replace("${exportName}", f"{html.escape(export_name)} (#{html.escape(job_id)})")
            .replace("${batchId}", html.escape(batch_id))
            .replace("${filesLists}", files_html)
            .replace("${downloadLink}", html.escape(download_link, quote=True)))
    subject = f"[Beacon] {export_name} — batch {batch_id} is ready"
    return subject, body


def export_page_link(panel_url: str, job_id: str) -> str:
    """The export's page on the Beacon panel — where the Download button
    goes. The panel is hash-routed under the shared distribution."""
    return f"{panel_url.rstrip('/')}#/m/beacon/exports/{job_id}"


class PearsonNotificationsAdapter:
    """Send one FileShare.StandardEmail event per batch (or test)."""

    def __init__(self, secrets_client, *, secret_ref: str, api_url: str,
                 token_url: str, panel_url: str, post=None,
                 now=time.monotonic):
        self._client = NotificationsClient(
            secrets_client, secret_ref=secret_ref, api_url=api_url,
            token_url=token_url, post=post, now=now)
        self._panel_url = panel_url

    def _send_event(self, *, recipients: list[str], subject: str,
                    body_html: str) -> dict:
        return self._client.send_email(recipients=recipients, subject=subject,
                                       body_html=body_html)

    def execute(self, file_names: list[str], *, recipients: list[str],
                job_id: str, export_name: str, batch_id: str) -> list[dict]:
        subject, body_html = render_email(
            export_name=export_name, job_id=job_id, batch_id=batch_id,
            file_names=file_names,
            download_link=export_page_link(self._panel_url, job_id))
        response = self._send_event(recipients=recipients, subject=subject,
                                    body_html=body_html)
        return [{"step": "email",
                 "event_id": event_id(response),
                 "recipients": list(recipients),
                 "files_listed": len(file_names), "ok": True}]

    def send_test(self, recipients: list[str]) -> dict:
        """The destination's connection test: a real mail through the real
        pipes — 'verified' means the Notifications API accepted the event."""
        subject, body_html = render_email(
            export_name="Connection test", job_id="none", batch_id="none",
            file_names=["This is a test — no files were delivered."],
            download_link=export_page_link(self._panel_url, ""))
        try:
            response = self._send_event(recipients=recipients,
                                        subject=subject, body_html=body_html)
        except Exception as e:  # noqa: BLE001 — the error IS the result
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return {"ok": True, "event_id": event_id(response)}
