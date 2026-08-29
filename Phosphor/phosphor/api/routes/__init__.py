from . import (
    auth,
    campaigns,
    contacts,
    dashboard,
    domains,
    mailboxes,
    messages,
    suppressions,
    templates,
    tracking,
)

ROUTERS = [
    auth.router,
    dashboard.router,
    domains.router,
    mailboxes.router,
    messages.router,
    contacts.router,
    templates.router,
    campaigns.router,
    suppressions.router,
    tracking.router,
]

__all__ = ["ROUTERS"]
