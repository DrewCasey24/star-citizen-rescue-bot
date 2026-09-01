"""Add System Health to the shared dashboard sidebar without changing page routes."""

from fastapi.responses import HTMLResponse

import dashboard_core as base


_previous_page = base.page


def page_with_health_nav(title, body, user=None):
    response = _previous_page(title, body, user)
    markup = response.body.decode("utf-8")
    marker = '<a href="/guild/'
    if '⚙ Settings</a>' not in markup or 'System Health</a>' in markup:
        return response

    # The shell already contains the guild-specific Settings href. Reuse that guild
    # path so the health link remains correct for every installed server.
    settings_pos = markup.find('⚙ Settings</a>')
    href_start = markup.rfind(marker, 0, settings_pos)
    if href_start == -1:
        return response
    href_end = markup.find('">', href_start)
    if href_end == -1:
        return response
    settings_href = markup[href_start + len('<a href="'):href_end]
    guild_root = settings_href.rsplit('/settings', 1)[0]
    health_link = f'<a href="{guild_root}/health">● System Health</a>'
    markup = markup[:href_start] + health_link + markup[href_start:]
    return HTMLResponse(markup, status_code=response.status_code)


base.page = page_with_health_nav
app = base.app
