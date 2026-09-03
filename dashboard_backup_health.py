"""Surface backup/restore verification status on System Health."""

import os

import dashboard_core as base


_previous_page = base.page


def page_with_backup_health(title, body, user=None):
    if title.startswith("System Health ·") and "Backup / Restore Readiness" not in body:
        verified = os.getenv("BACKUP_VERIFIED_AT", "").strip()
        if verified:
            card = f'''<div class="card health-card"><div class="health-row"><div><div class="health-name">Backup / Restore Readiness</div><div class="health-detail">Provider backup and restore drill recorded as verified at {base.esc(verified)}. Update BACKUP_VERIFIED_AT after each successful restore drill.</div></div><span class="health-state health-ok">Verified</span></div></div>'''
        else:
            card = '''<div class="card health-card"><div class="health-row"><div><div class="health-name">Backup / Restore Readiness</div><div class="health-detail">No restore verification timestamp is configured. Enable provider database backups and complete a staging restore drill, then set BACKUP_VERIFIED_AT.</div></div><span class="health-state health-warn">Unverified</span></div></div>'''
        marker = '</div><div class="health-note">'
        body = body.replace(marker, card + '</div><div class="health-note">', 1)
    return _previous_page(title, body, user)


base.page = page_with_backup_health
app = base.app
