"""Build demo/smokeos-demo.html: the real dashboard UI running on mock.js example data."""
import base64, os
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
css = open('server/static/style.css').read()
js = open('server/static/app.js').read()
mock = open('demo/mock.js').read()
html = open('server/static/index.html').read()
start = js.index('async function api(path, opts = {}) {'); end = js.index('function toast(msg)')
js = js[:start] + js[end:]
js = js.replace('location.origin', 'DEMO.serverUrl')
js = js.replace('''$("#logout").onclick = async () => { await api("/api/logout", { method: "POST" }).catch(() => {}); location.href = "/login"; };''',
                '''$("#logout").onclick = () => toast("Logging out is turned off in the demo");''')
js = js.replace('setTimeout(() => location.reload(), 10000);', 'setTimeout(refresh, 3500);')
js = js.replace('toast("Server updating – reloading in 10 s")', 'toast("Server updating – back in a few seconds")')
js = js.replace('if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});', '')
for a in ('toast("Copied")', 'toast("Farm key copied")'):
    js = js.replace(f'.then(() => {a})', f'.then(() => {a}).catch(() => toast("Copy is blocked here – select the text instead"))')
bad = [l for l in js.replace('location.hash', '').splitlines() if 'location.' in l]
assert not bad, bad
icon = 'data:image/svg+xml;base64,' + base64.b64encode(open('server/static/icon.svg').read().strip().encode()).decode()
body = html[html.index('<header>'):html.index('<script src="/static/chart.umd.js">')].replace('/static/icon.svg', icon)
libs = ''.join(f'<script>\n{open("server/static/" + f).read()}\n</script>\n' for f in ('chart.umd.js', 'xterm.js', 'xterm-addon-fit.js'))
xcss = open('server/static/xterm.css').read()
css_extra = '''
:root{color-scheme:dark}
body{background:var(--bg);color:var(--text)}
header{top:env(safe-area-inset-top,0px);padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
body::before{content:"";position:fixed;inset:0 0 auto;height:env(safe-area-inset-top,0px);z-index:10;background:var(--bg)}
main{padding-block:16px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
.demo{max-width:1200px;margin:12px auto 0;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
.demo div{border:1px dashed rgba(255,122,26,.45);background:rgba(255,122,26,.07);border-radius:10px;padding:8px 12px;font-size:13px;color:var(--muted)}
.demo b{color:var(--accent)}
.btn:focus-visible,input:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
'''
banner = '<div class="demo"><div><b>Demo</b> · example rigs and numbers, nothing here touches your real rigs. Open a rig for its tabs, try Flight sheets, and the Shell button (any 6 digits work as the 2FA code here).</div></div>\n'
body = body.replace('<main id="view">', banner + '<main id="view">')
out = f'''<title>SmokeOS Demo</title>
<meta name="claude:viewport-fit-sides" content="cover">
<style>
{xcss}
{css}
{css_extra}
</style>
{body}
{libs}<script>
{mock}
</script>
<script>
{js}
</script>
'''
open('demo/smokeos-demo.html', 'w').write(out)
print("built", len(out))
