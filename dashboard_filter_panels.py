"""Compact collapsible filter panels for dashboard data views."""

from fastapi.responses import HTMLResponse

import dashboard_core as base


_previous_page = base.page

FILTER_CSS = r'''
.filter-panel{
  margin-top:14px;
  border:1px solid rgba(116,153,196,.16);
  border-radius:12px;
  background:rgba(7,13,22,.42);
  overflow:hidden;
}
.filter-panel>summary{
  list-style:none;
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:12px;
  min-height:44px;
  padding:10px 12px;
  cursor:pointer;
  color:#a9bdd3;
  font-size:12px;
  font-weight:720;
  letter-spacing:.025em;
  user-select:none;
}
.filter-panel>summary::-webkit-details-marker{display:none}
.filter-panel>summary:hover{background:rgba(86,168,255,.045);color:#dcecff}
.filter-panel[open]>summary{border-bottom:1px solid rgba(116,153,196,.13);background:rgba(86,168,255,.035)}
.filter-panel .filter-summary-right{display:flex;align-items:center;gap:8px;color:#7189a5;font-weight:600}
.filter-panel .filter-count{
  min-width:22px;height:22px;display:inline-grid;place-items:center;
  padding:0 7px;border-radius:999px;
  background:rgba(86,168,255,.13);border:1px solid rgba(86,168,255,.18);color:#9ed0ff;
  font-size:11px;font-weight:760;
}
.filter-panel .filter-chevron{font-size:13px;transition:transform .16s ease}
.filter-panel[open] .filter-chevron{transform:rotate(180deg)}
.filter-panel form{padding:2px 14px 14px}
.filter-panel .filter-grid{padding-top:2px}
.filter-panel .compact-date-grid{display:grid;grid-template-columns:minmax(160px,1fr) minmax(160px,1fr);gap:12px;align-items:end}
.filter-panel .compact-date-actions{grid-column:1/-1;display:flex;gap:9px;justify-content:flex-end;flex-wrap:wrap}
@media(max-width:700px){
  .filter-panel .compact-date-grid{grid-template-columns:1fr}
  .filter-panel .compact-date-actions{grid-column:auto;justify-content:stretch}
  .filter-panel .compact-date-actions .btn{flex:1;text-align:center}
}
'''

FILTER_JS = r'''
<script>
(function(){
  const params = new URLSearchParams(window.location.search);
  const configs = [
    {
      suffix: '/history',
      keys: ['q','service','priority','status','responder','date_from','date_to'],
      label: 'Filters',
      normalize: false
    },
    {
      suffix: '/performance',
      keys: ['date_from','date_to'],
      label: 'Date Filters',
      normalize: true
    },
    {
      suffix: '/performance/services',
      keys: ['date_from','date_to'],
      label: 'Date Filters',
      normalize: true
    }
  ];

  configs.forEach((cfg) => {
    const form = Array.from(document.querySelectorAll('form[method="get"]')).find((node) => {
      const action = node.getAttribute('action') || '';
      return action.endsWith(cfg.suffix);
    });
    if (!form || form.closest('.filter-panel')) return;

    const active = cfg.keys.filter((key) => {
      const value = (params.get(key) || '').trim();
      return value !== '';
    }).length;

    if (cfg.normalize && !form.querySelector('.compact-date-grid')) {
      form.classList.add('compact-date-form');
      const children = Array.from(form.children);
      if (children.length >= 3) {
        const grid = document.createElement('div');
        grid.className = 'compact-date-grid';
        children.slice(0,2).forEach((child) => grid.appendChild(child));
        const actions = document.createElement('div');
        actions.className = 'compact-date-actions';
        children.slice(2).forEach((child) => actions.appendChild(child));
        grid.appendChild(actions);
        form.appendChild(grid);
      }
    }

    const details = document.createElement('details');
    details.className = 'filter-panel';
    if (active > 0) details.open = true;

    const summary = document.createElement('summary');
    const left = document.createElement('span');
    left.textContent = cfg.label;
    const right = document.createElement('span');
    right.className = 'filter-summary-right';
    if (active > 0) {
      const count = document.createElement('span');
      count.className = 'filter-count';
      count.textContent = String(active);
      right.appendChild(count);
      const applied = document.createElement('span');
      applied.textContent = active === 1 ? 'active' : 'active';
      right.appendChild(applied);
    } else {
      const hint = document.createElement('span');
      hint.textContent = 'Optional';
      right.appendChild(hint);
    }
    const chevron = document.createElement('span');
    chevron.className = 'filter-chevron';
    chevron.textContent = '⌄';
    right.appendChild(chevron);
    summary.appendChild(left);
    summary.appendChild(right);

    form.parentNode.insertBefore(details, form);
    details.appendChild(summary);
    details.appendChild(form);
  });
})();
</script>
'''


def page_with_collapsible_filters(title, body, user=None):
    response = _previous_page(title, body, user)
    markup = response.body.decode("utf-8")
    if '/history' not in markup and '/performance' not in markup:
        return response
    markup = markup.replace("</style>", FILTER_CSS + "\n</style>", 1)
    markup = markup.replace("</body>", FILTER_JS + "\n</body>", 1)
    return HTMLResponse(markup, status_code=response.status_code)


base.page = page_with_collapsible_filters
app = base.app
