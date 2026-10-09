"""Pearson Notifications API client, shared by every UIF stack.

One transport (token mint + cache, FileShare.StandardEmail send) that beacon's
export mail and echo's gold alert mail both ride. Stdlib only: consumers copy
this package into their Lambda zips unchanged.
"""

from pearson_notifications.client import (  # noqa: F401
    NotificationsClient,
    event_id,
    post_json,
)
