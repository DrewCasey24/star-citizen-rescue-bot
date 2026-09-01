"""Modern visual theme for the rescue operations dashboard.

This module intentionally layers presentation on top of dashboard_core so the
existing routes and operational behavior stay untouched.
"""

from fastapi.responses import HTMLResponse

import dashboard_core as base


_original_page = base.page


MODERN_CSS = r'''
/* Modern operations-console theme */
:root{
  --bg:#070b12;
  --panel:#0d1420;
  --panel-2:#111b2a;
  --line:#1f3045;
  --line-soft:rgba(116,153,196,.16);
  --text:#edf5ff;
  --muted:#8fa2ba;
  --accent:#56a8ff;
  --accent-2:#7bc4ff;
  --success:#45d49b;
  --warning:#ffb45d;
  --danger:#ff6b78;
  --shadow:0 22px 55px rgba(0,0,0,.28);
}
html{color-scheme:dark;scroll-behavior:smooth}
body{
  background:
    radial-gradient(circle at 12% -10%,rgba(48,116,190,.18),transparent 34%),
    radial-gradient(circle at 88% 0%,rgba(38,92,154,.10),transparent 30%),
    linear-gradient(180deg,#070b12 0%,#09101a 42%,#070b12 100%);
  background-attachment:fixed;
  letter-spacing:.005em;
}
body:before{
  content:'';
  position:fixed;
  inset:0;
  pointer-events:none;
  opacity:.17;
  background-image:linear-gradient(rgba(255,255,255,.018) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.018) 1px,transparent 1px);
  background-size:42px 42px;
  mask-image:linear-gradient(to bottom,black,transparent 75%);
}
.wrap{max-width:1380px;padding:24px 28px 72px;position:relative}
header{
  position:sticky;
  top:14px;
  z-index:20;
  margin-bottom:28px;
  padding:13px 16px;
  border:1px solid rgba(116,153,196,.20);
  border-radius:16px;
  background:rgba(9,15,24,.78);
  backdrop-filter:blur(18px) saturate(135%);
  -webkit-backdrop-filter:blur(18px) saturate(135%);
  box-shadow:0 10px 35px rgba(0,0,0,.22);
}
.brand{display:flex;align-items:center;gap:12px;min-width:0}
.brand-mark{
  width:40px;height:40px;display:grid;place-items:center;flex:0 0 auto;
  border:1px solid rgba(86,168,255,.40);border-radius:12px;
  background:linear-gradient(145deg,rgba(86,168,255,.18),rgba(86,168,255,.05));
  box-shadow:inset 0 1px rgba(255,255,255,.08),0 0 24px rgba(86,168,255,.10);
  font-size:19px;
}
.brand-copy{min-width:0}
.brand h1{font-size:17px;font-weight:760;letter-spacing:.01em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.brand small{display:block;margin-top:2px;font-size:11px;text-transform:uppercase;letter-spacing:.13em;color:#6f89a7}
.user{display:flex;align-items:center;gap:8px;padding:7px 10px;border-radius:10px;background:rgba(255,255,255,.025);border:1px solid var(--line-soft)}
a{color:var(--accent-2);transition:color .16s ease,opacity .16s ease,border-color .16s ease,background .16s ease,transform .16s ease}a:hover{color:#b9ddff}
h1,h2{letter-spacing:-.015em}h2{font-size:18px;font-weight:720}.muted{line-height:1.55}
.grid{gap:14px}
.card{
  position:relative;
  overflow:hidden;
  background:linear-gradient(155deg,rgba(17,27,42,.90),rgba(12,20,32,.94));
  border:1px solid rgba(113,148,190,.17);
  border-radius:16px;
  padding:20px;
  box-shadow:var(--shadow);
}
.card:before{content:'';position:absolute;inset:0 0 auto;height:1px;background:linear-gradient(90deg,transparent,rgba(151,197,255,.16),transparent)}
.card:hover{border-color:rgba(113,164,222,.25)}
.metric{font-size:32px;font-weight:780;letter-spacing:-.035em;color:#f5f9ff}
.label{font-size:11px;font-weight:700;letter-spacing:.12em;color:#7890ab}
table{font-size:13.5px;border-collapse:separate;border-spacing:0}
th{font-size:10px;letter-spacing:.11em;color:#758ca7;background:rgba(5,10,17,.30);position:sticky;top:0;z-index:1}
th,td{padding:12px 11px;border-bottom:1px solid rgba(116,153,196,.11)}
tbody tr{transition:background .14s ease}tbody tr:hover{background:rgba(86,168,255,.045)}tbody tr:last-child td{border-bottom:0}
.incident-link{font-weight:700;color:#84c4ff}
.pill,.status,.ledger-badge{border:1px solid transparent;font-weight:650;letter-spacing:.01em}
.p1{background:rgba(166,54,68,.22);border-color:rgba(255,107,120,.18);color:#ffadb5}
.p2,.not-installed{background:rgba(166,91,24,.20);border-color:rgba(255,180,93,.18);color:#ffd09a}
.p3,.installed{background:rgba(35,122,87,.20);border-color:rgba(69,212,155,.16);color:#92efca}
.ledger-badge{background:rgba(54,116,184,.16);border-color:rgba(86,168,255,.16);color:#9ecfff}
label{font-weight:650;letter-spacing:.035em;color:#8196af}
select,input{
  background:rgba(5,11,19,.72);
  border:1px solid rgba(116,153,196,.22);
  border-radius:10px;
  padding:10px 12px;
  outline:none;
  transition:border-color .15s ease,box-shadow .15s ease,background .15s ease;
}
select:focus,input:focus{border-color:rgba(86,168,255,.64);box-shadow:0 0 0 3px rgba(86,168,255,.10);background:#0a1320}
.btn{
  border:1px solid rgba(255,255,255,.06);
  border-radius:10px;
  background:linear-gradient(180deg,#3388df,#286eb8);
  padding:10px 15px;
  font-size:13px;
  font-weight:720;
  box-shadow:inset 0 1px rgba(255,255,255,.10),0 7px 18px rgba(19,76,136,.18);
  transition:transform .14s ease,filter .14s ease,box-shadow .14s ease;
}
.btn:hover{transform:translateY(-1px);filter:brightness(1.08);color:white}.btn:active{transform:translateY(0)}
.btn.secondary{background:#182638;border-color:rgba(116,153,196,.16);box-shadow:none;color:#dcecff}
.btn.install,.btn.success{background:linear-gradient(180deg,#238a64,#1d6e51)}
.btn.warn{background:linear-gradient(180deg,#b86a22,#925217)}
.btn.danger{background:linear-gradient(180deg,#b94654,#963743)}
.notice{border-color:rgba(69,212,155,.25);background:rgba(25,88,66,.20);border-radius:12px;color:#a8efcf;box-shadow:inset 3px 0 var(--success)}
.guild{padding:17px 4px;border-color:rgba(116,153,196,.11)}
.section-nav{gap:9px;margin-bottom:18px}
.section-nav a{
  padding:8px 11px;
  background:rgba(15,25,39,.72);
  border:1px solid rgba(116,153,196,.17);
  border-radius:9px;
  color:#a7c9ea;
  font-size:12px;
  font-weight:650;
}
.section-nav a:hover{background:rgba(32,59,88,.58);border-color:rgba(86,168,255,.30);color:#d9ecff;transform:translateY(-1px)}
.kv{gap:10px 18px}.timeline{border-color:rgba(86,168,255,.20)}
.event:before{background:var(--accent);box-shadow:0 0 0 4px #101a29,0 0 16px rgba(86,168,255,.38)}
.event-title{font-weight:720}.event-meta{color:#738aa5}
.filter-grid{gap:14px}.pagination{padding-top:4px}
::-webkit-scrollbar{width:10px;height:10px}::-webkit-scrollbar-track{background:#08101a}::-webkit-scrollbar-thumb{background:#23364c;border:2px solid #08101a;border-radius:10px}::-webkit-scrollbar-thumb:hover{background:#2c4662}
@media(max-width:850px){
  .wrap{padding:14px 14px 46px}
  header{top:8px;padding:11px 12px;border-radius:14px}
  .brand h1{font-size:15px}
  .brand-mark{width:36px;height:36px;border-radius:10px}
  .card{padding:16px;border-radius:14px}
  .metric{font-size:28px}
}
'''


def modern_page(title, body, user=None):
    response = _original_page(title, body, user)
    markup = response.body.decode("utf-8")
    markup = markup.replace("</style>", MODERN_CSS + "\n</style>", 1)
    markup = markup.replace(
        '<div class="brand"><h1>🚨 Star Citizen Rescue Dispatch</h1><small>Operations Dashboard</small></div>',
        '<div class="brand"><div class="brand-mark">✦</div><div class="brand-copy"><h1>Star Citizen Rescue Dispatch</h1><small>Operations Command</small></div></div>',
        1,
    )
    return HTMLResponse(markup, status_code=response.status_code, headers=dict(response.headers))


base.page = modern_page
