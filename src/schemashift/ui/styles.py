"""SchemaShift's adapted earth-tone Gradio theme."""

CSS = r"""
/* Keep native Gradio controls and custom HTML on the same palette. Gradio
   toggles .dark for both system preference and its explicit theme setting. */
.gradio-container {
    --ss-workspace-height:max(320px, calc(100dvh - 280px));
    --layout-gap:6px;
    --block-padding:8px;
    --block-radius:10px;
    --ss-bg:#f4efe6;
    --ss-surface:#fffdf9;
    --ss-inset:#f1efec;
    --ss-text:#3f342c;
    --ss-muted:#6d625a;
    --ss-border:#ded3c2;
    --ss-control-border:#948778;
    --ss-brand:#5f7356;
    --ss-brand-deep:#455640;
    --ss-link:#455640;
    --ss-active:#9ed48f;
    --ss-done:#477db3;
    --ss-accent:#b66a4b;
    --ss-warning:#b68a3a;
    --ss-error:#a44a3f;
    --ss-idle:#9c968f;
    --ss-icon:#66625d;
    --ss-active-bg:#edf8e9;
    --ss-active-border:#b8dcae;
    --ss-active-icon:#35552d;
    --ss-active-icon-bg:#ddf2d7;
    --ss-done-bg:#edf4fb;
    --ss-done-border:#bfd3e8;
    --ss-done-icon:#285f96;
    --ss-done-icon-bg:#dceaf7;
    --ss-error-bg:#fbefeb;
    --ss-error-border:#e9c3b9;
    --ss-error-icon:#a44a3f;
    --ss-error-icon-bg:#f7dfd8;
    --ss-review-bg:#fffaf1;
    --ss-risk-text:#805d1f;
    --ss-risk-bg:#f5ead2;
    --ss-focus:#477db3;
    color-scheme:light;

    --body-background-fill:var(--ss-bg);
    --background-fill-primary:var(--ss-surface);
    --background-fill-secondary:var(--ss-inset);
    --body-text-color:var(--ss-text);
    --body-text-color-subdued:var(--ss-muted);
    --border-color-primary:var(--ss-border);
    --border-color-accent:var(--ss-control-border);
    --border-color-accent-subdued:var(--ss-border);
    --color-accent:var(--ss-link);
    --color-accent-soft:var(--ss-active-bg);
    --block-background-fill:var(--ss-surface);
    --block-border-color:var(--ss-border);
    --block-title-text-color:var(--ss-text);
    --block-title-background-fill:var(--ss-surface);
    --block-label-text-color:var(--ss-text);
    --block-label-background-fill:var(--ss-surface);
    --block-label-border-color:var(--ss-border);
    --block-info-text-color:var(--ss-muted);
    --input-background-fill:var(--ss-inset);
    --input-text-color:var(--ss-text);
    --input-placeholder-color:var(--ss-muted);
    --input-border-color:var(--ss-control-border);
    --input-border-color-focus:var(--ss-focus);
    --panel-background-fill:var(--ss-surface);
    --panel-border-color:var(--ss-border);
    --table-text-color:var(--ss-text);
    --table-border-color:var(--ss-border);
    --table-even-background-fill:var(--ss-inset);
    --table-odd-background-fill:var(--ss-surface);
    --table-row-focus:var(--ss-active-bg);
    --link-text-color:var(--ss-link);
    --link-text-color-hover:var(--ss-link);
    --link-text-color-active:var(--ss-link);
    --link-text-color-visited:var(--ss-link);
    --button-secondary-background-fill:var(--ss-inset);
    --button-secondary-background-fill-hover:var(--ss-active-bg);
    --button-secondary-text-color:var(--ss-text);
    --button-secondary-text-color-hover:var(--ss-text);
    --button-secondary-border-color:var(--ss-control-border);
}
.dark .gradio-container, .gradio-container.dark {
    --ss-bg:#181c19;
    --ss-surface:#252b25;
    --ss-inset:#1e231e;
    --ss-text:#f1eee7;
    --ss-muted:#bec6b9;
    --ss-border:#485345;
    --ss-control-border:#7e8c77;
    --ss-link:#c1deb2;
    --ss-icon:#d2d8cd;
    --ss-active-bg:#293d27;
    --ss-active-border:#638859;
    --ss-active-icon:#c1ecb5;
    --ss-active-icon-bg:#344d2e;
    --ss-done-bg:#24364b;
    --ss-done-border:#547ba4;
    --ss-done-icon:#bddcff;
    --ss-done-icon-bg:#2d4662;
    --ss-error-bg:#422b27;
    --ss-error-border:#a36a5d;
    --ss-error-icon:#ffc1b4;
    --ss-error-icon-bg:#583830;
    --ss-review-bg:#302d24;
    --ss-risk-text:#f0d69d;
    --ss-risk-bg:#4b3d25;
    --ss-focus:#a5cfff;
    color-scheme:dark;
}
.gradio-container{width:100%!important;max-width:none!important;min-height:100dvh;box-sizing:border-box;margin:0!important;padding:12px 20px!important;background:radial-gradient(circle at 12% 4%,rgba(118,147,107,.12),transparent 28%),radial-gradient(circle at 92% 2%,rgba(182,106,75,.10),transparent 24%),var(--ss-bg)!important;color:var(--ss-text)!important;font-family:Aptos,Inter,"Segoe UI",system-ui,sans-serif!important}
.ss-hero{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:24px 28px;margin-bottom:8px;border:1px solid rgba(95,115,86,.24);background:linear-gradient(120deg,#566a4f,#667c5c);color:#fffdf8;border-radius:28px;box-shadow:0 18px 50px rgba(72,56,43,.10)}.ss-hero h1{margin:5px 0;font-size:clamp(25px,3vw,40px);letter-spacing:-.03em}.ss-hero p{margin:0;color:#eee7dc}.ss-eyebrow{text-transform:uppercase;letter-spacing:.12em;font-size:12px;font-weight:800;opacity:.82}.ss-badge{display:inline-block;border:1px solid rgba(255,255,255,.30);background:rgba(255,255,255,.10);padding:8px 11px;border-radius:999px;font-size:13px;margin-left:7px}
.ss-pipeline{display:grid;grid-template-columns:repeat(7,1fr);gap:12px;padding:15px;margin:10px 0 18px;border-radius:24px;border:1px solid var(--ss-border);background:var(--ss-surface);box-shadow:0 10px 30px rgba(72,56,43,.06)}.ss-stage{position:relative;display:grid;grid-template-columns:44px 1fr;align-items:center;gap:10px;min-height:76px;padding:12px;border:1px solid transparent;border-radius:17px}.ss-stage:after{content:"";position:absolute;width:12px;height:12px;border-radius:50%;right:10px;top:10px;background:var(--ss-idle);box-shadow:0 0 0 4px rgba(156,150,143,.18)}.ss-icon{width:44px;height:44px;display:grid;place-items:center;border-radius:13px;border:1px solid var(--ss-border);background:var(--ss-inset);color:var(--ss-icon)}.ss-icon svg{width:26px;height:26px;stroke:currentColor;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}.ss-stage strong{display:block;font-size:14px}.ss-stage span{display:block;color:var(--ss-muted);font-size:12px}.ss-stage.active{background:var(--ss-active-bg);border-color:var(--ss-active-border);box-shadow:0 10px 28px rgba(95,115,86,.13)}.ss-stage.active .ss-icon{color:var(--ss-active-icon);background:var(--ss-active-icon-bg);border-color:var(--ss-active-border);box-shadow:0 0 0 5px rgba(158,212,143,.18)}.ss-stage.active:after{background:var(--ss-active);box-shadow:0 0 0 5px rgba(158,212,143,.22);animation:sspulse 1.2s infinite}.ss-stage.done{background:var(--ss-done-bg);border-color:var(--ss-done-border)}.ss-stage.done .ss-icon{color:var(--ss-done-icon);background:var(--ss-done-icon-bg);border-color:var(--ss-done-border)}.ss-stage.done:after{background:var(--ss-done);box-shadow:0 0 0 4px rgba(71,125,179,.16)}.ss-stage.error{background:var(--ss-error-bg);border-color:var(--ss-error-border)}.ss-stage.error .ss-icon{color:var(--ss-error-icon);background:var(--ss-error-icon-bg);border-color:var(--ss-error-border)}.ss-stage.error:after{background:var(--ss-error)}@keyframes sspulse{50%{transform:scale(1.18);opacity:.64}}
.ss-panel{border:1px solid var(--ss-border)!important;border-radius:20px!important;overflow:hidden!important;background:var(--ss-surface)!important;box-shadow:0 12px 35px rgba(72,56,43,.07)!important;padding:4px 10px}.ss-activity{font-family:Aptos,Inter,"Segoe UI",system-ui,sans-serif;color:var(--ss-text);max-height:390px;overflow:auto}.ss-event{display:grid;grid-template-columns:10px 72px 1fr;gap:9px;align-items:start;padding:10px 0;border-bottom:1px solid var(--ss-border);font-size:13px}.ss-dot{width:8px;height:8px;border-radius:50%;margin-top:6px;background:var(--ss-brand)}.ss-event.warning .ss-dot{background:var(--ss-warning)}.ss-event.error .ss-dot{background:var(--ss-error)}.ss-event time{color:var(--ss-muted)}.ss-event p{margin:2px 0 0;color:var(--ss-muted)}.ss-empty{padding:20px;color:var(--ss-muted)}.ss-decision{border:1px solid var(--ss-border);background:var(--ss-review-bg);border-radius:16px;padding:16px}.ss-decision-empty{background:var(--ss-inset);border-color:var(--ss-border)}.ss-risk{display:inline-block;color:var(--ss-risk-text);background:var(--ss-risk-bg);border:1px solid var(--ss-border);padding:6px 9px;border-radius:999px;font-size:12px;font-weight:750}.ss-decision h3{margin:13px 0 6px}.ss-decision p{color:var(--ss-muted);line-height:1.5}.ss-decision pre{white-space:pre-wrap;max-height:260px;overflow:auto;background:var(--ss-inset);border-radius:10px;padding:10px}.ss-review-id{font-family:monospace;font-size:12px}.ss-note{color:var(--ss-muted);font-size:12px;text-align:center;margin:12px 0 4px}
/* Gradio's HTML typography applies colors directly to descendants. Set the
   foreground on the actual labels and SVG shapes, not just their parents. */
.gradio-container .ss-stage strong,
.gradio-container .ss-panel :is(h1,h2,h3,h4,strong),
.gradio-container .ss-activity strong,
.gradio-container .ss-decision :is(h3,h4,pre,code) {
    color:var(--ss-text)!important;
}
.gradio-container .ss-stage span,
.gradio-container .ss-event :is(time,p),
.gradio-container .ss-empty,
.gradio-container .ss-decision p,
.gradio-container .ss-note {
    color:var(--ss-muted)!important;
}
.gradio-container .ss-icon svg,
.gradio-container .ss-icon svg * {
    color:inherit!important;
    stroke:currentColor;
}
.gradio-container .ss-risk { color:var(--ss-risk-text)!important; }
.gradio-container .ss-hero :is(h1,.ss-eyebrow,.ss-badge) { color:#fffdf8!important; }
.gradio-container .ss-hero p { color:#eee7dc!important; }
.gradio-container [role="tab"] { color:var(--ss-muted)!important; }
.gradio-container [role="tab"]:hover,
.gradio-container [role="tab"][aria-selected="true"] {
    color:var(--ss-link)!important;
    border-color:var(--ss-link)!important;
}
.gradio-container input::placeholder,
.gradio-container textarea::placeholder { color:var(--ss-muted)!important;opacity:1; }
#approve_btn,#send_btn,#confirm_files_btn{background:var(--ss-brand)!important;color:#fff!important;border:0!important}
#approve_btn:hover,#send_btn:hover,#confirm_files_btn:hover{background:var(--ss-brand-deep)!important}
#reject_btn{background:var(--ss-error-bg)!important;color:var(--ss-error-icon)!important;border-color:var(--ss-error-border)!important}
button:focus-visible,input:focus-visible,textarea:focus-visible{outline:3px solid #477db3!important;outline-offset:2px!important}
.gradio-container :is(button,input,textarea,[tabindex]):focus-visible{outline-color:var(--ss-focus)!important}
@media(max-width:1180px){.ss-pipeline{grid-template-columns:repeat(4,1fr)}}@media(max-width:940px){.ss-pipeline{grid-template-columns:repeat(2,1fr)}.ss-hero{align-items:flex-start;flex-direction:column}}@media(max-width:560px){.ss-pipeline{grid-template-columns:1fr}}@media(prefers-reduced-motion:reduce){.ss-stage.active:after{animation:none!important}}

/* Use the desktop width for three working columns instead of stacking the
   composer below a tall chat. Long conversations and activity scroll inside
   their panels; smaller windows retain ordinary page scrolling. */
#workspace_header,#pipeline_rail { padding:0!important;min-height:0!important; }
.gradio-container { padding:8px 12px!important; }
.gradio-container > .main { padding:0!important; }
.gradio-container footer { margin:4px 0 0!important;padding:0!important; }
.ss-hero { padding:8px 12px;gap:10px;margin-bottom:0;border-radius:12px;box-shadow:none; }
.ss-hero h1 { font-size:clamp(20px,1.5vw,24px);margin:1px 0; }
.ss-hero p { font-size:12px; }
.ss-eyebrow { font-size:10px; }
.ss-badge { padding:4px 7px;font-size:11px; }
.ss-pipeline { padding:5px;gap:4px;margin:0;border-radius:12px;box-shadow:none; }
.ss-stage { grid-template-columns:28px minmax(0,1fr);gap:6px;min-height:44px;padding:6px;border-radius:8px; }
.ss-stage:after { width:6px;height:6px;top:6px;right:6px; }
.ss-stage strong { padding-right:8px;font-size:12px;overflow-wrap:anywhere; }
.ss-stage span { font-size:11px; }
.ss-icon { width:28px;height:28px;border-radius:7px; }
.ss-icon svg { width:19px;height:19px; }
#conversation_toolbar { align-items:center; }
#conversation_selector { min-width:0; }
#conversation_selector [role="listbox"] { max-height:280px;overflow-y:auto; }
#workspace_tabs > .tabitem { padding:6px 0 0!important; }
#workspace_tabs [role="tabpanel"] { padding:6px 0 0!important; }
#workspace_tabs [role="tab"] { padding:6px 10px;font-size:13px; }
#workspace_tabs [role="tablist"] { flex-wrap:wrap;gap:2px; }
.ss-inspector { color:var(--ss-text);padding:10px 2px; }
.ss-inspector header { margin-bottom:16px; }
.ss-inspector h2 { margin:0 0 4px;font-size:22px;letter-spacing:-.02em; }
.ss-inspector header p,.ss-inspector-card > p { color:var(--ss-muted);margin:4px 0 10px; }
.ss-inspector-grid { display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,320px),1fr));gap:12px; }
.ss-inspector-card { padding:14px 16px;border:1px solid var(--ss-border);border-radius:12px;background:var(--ss-surface);margin-bottom:12px;min-width:0; }
.ss-inspector-card h3 { font-size:15px;margin:0 0 10px; }
.ss-inspector-fields { margin:0; }
.ss-inspector-fields > div { display:grid;grid-template-columns:100px minmax(0,1fr);gap:12px;padding:6px 0; }
.ss-inspector-fields dt { color:var(--ss-muted);font-size:12px; }
.ss-inspector-fields dd { margin:0;font-size:13px;overflow-wrap:anywhere; }
.ss-inspector pre { background:var(--ss-inset);color:var(--ss-text);border-radius:8px;padding:12px;white-space:pre-wrap;overflow-wrap:anywhere;max-height:360px;overflow:auto;font-size:12px; }
.ss-inspector details { border:1px solid var(--ss-border);border-radius:9px;padding:10px 12px;margin:8px 0;background:var(--ss-surface); }
.ss-inspector summary { cursor:pointer;font-size:13px;overflow-wrap:anywhere; }
.ss-inspector .ss-activity { max-height:var(--ss-workspace-height); }
.ss-inspector .ss-empty { margin:0;padding:8px 0; }
.ss-graph-wrap { border:1px solid var(--ss-border);border-radius:12px;background:var(--ss-surface);overflow:auto;max-height:var(--ss-workspace-height); }
.ss-graph { display:block;width:100%;max-width:880px;min-width:500px;margin:0 auto;color:var(--ss-brand); }
.ss-graph rect { fill:var(--ss-active-bg);stroke:var(--ss-active-border); }
.ss-graph text { fill:var(--ss-text);font-family:Aptos,Inter,"Segoe UI",sans-serif;font-size:13px; }
.gradio-container .ss-inspector :is(h2,h3,summary,dd,pre,code) { color:var(--ss-text)!important; }
#chat_workspace { gap:8px;align-items:stretch; }
#chat_workspace > div { min-width:0; }
#activity_column { padding:8px 10px;border-radius:10px!important;box-shadow:none!important; }
#activity_column h3 { margin:0 0 6px; }
.ss-event { grid-template-columns:8px 58px minmax(0,1fr);gap:6px;padding:6px 0; }
.ss-event p { overflow-wrap:anywhere; }
.ss-empty { padding:10px; }
.ss-decision { padding:10px;border-radius:10px; }
.ss-decision h3 { margin:6px 0; }
.ss-risk { padding:3px 7px; }
.ss-note { margin:4px 0 0; }
@media(max-width:1180px) {
    .ss-pipeline { grid-template-columns:repeat(7,minmax(145px,1fr));overflow-x:auto; }
}
@media(min-width:941px) {
    #chat_workspace {
        display:grid!important;
        grid-template-columns:minmax(0,5fr) minmax(0,4fr) minmax(0,3fr);
    }
    #conversation_column,#query_column,#activity_column {
        height:var(--ss-workspace-height);
        min-height:0!important;
        min-width:0!important;
        box-sizing:border-box;
    }
    #chat_history { height:100%!important;min-height:0!important; }
    #query_column { gap:6px;display:flex;flex-direction:column; }
    #chat_input,#send_btn { flex:none; }
    #original_sql { flex:1;min-height:0!important;overflow:hidden; }
    #original_sql .cm-editor {
        height:var(--ss-editor-height, 180px)!important;
        min-height:0!important;
    }
    #original_sql .cm-scroller { overflow:auto; }
    #activity_log { min-height:0;overflow:auto; }
    #activity_log .ss-activity {
        max-height:none;
    }
}
@media(max-width:940px) {
    #chat_workspace { flex-direction:column; }
    #query_column { order:-1; }
    #chat_history { height:240px!important; }
    #original_sql .cm-editor { max-height:200px; }
    #chat_workspace > div { width:100%;min-width:0!important; }
    #activity_log .ss-activity { max-height:280px; }
    #send_btn { position:sticky;bottom:8px;z-index:2;min-height:40px; }
}
@media(max-width:560px) {
    .gradio-container { padding:8px!important; }
    .ss-hero { padding:8px;gap:6px; }
    .ss-pipeline { grid-template-columns:repeat(7,minmax(145px,1fr)); }
    .ss-hero p,.ss-eyebrow { display:none; }
    .ss-badge { margin:4px 4px 0 0; }
}
"""


# Measure the rendered layout rather than assuming a fixed header height.
# ResizeObserver covers wrapped labels, prompt growth, font loading, and zoom.
LAYOUT_JS = r"""
() => {
    if (window.__ssLayoutCleanup) window.__ssLayoutCleanup();
    let frame;
    const workspace = document.getElementById('chat_workspace');
    const container = workspace?.closest('.gradio-container');
    if (!container) return;
    const update = () => {
        if (window.innerWidth < 941 || !workspace.getClientRects().length) return;
        const viewport = window.visualViewport;
        const bottom = viewport ? viewport.height + viewport.offsetTop : window.innerHeight;
        const height = Math.max(220, Math.floor(bottom - workspace.getBoundingClientRect().top - 48));
        const value = `${height}px`;
        if (container.style.getPropertyValue('--ss-workspace-height') !== value)
            container.style.setProperty('--ss-workspace-height', value);
        const code = document.getElementById('original_sql');
        const editor = code?.querySelector('.cm-editor');
        if (editor) {
            const editorHeight = Math.max(60, Math.floor(
                code.getBoundingClientRect().bottom - editor.getBoundingClientRect().top - 4
            ));
            const editorValue = `${editorHeight}px`;
            if (container.style.getPropertyValue('--ss-editor-height') !== editorValue)
                container.style.setProperty('--ss-editor-height', editorValue);
        }
    };
    const schedule = () => {
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(update);
    };
    const observer = new ResizeObserver(schedule);
    ['workspace_header', 'conversation_toolbar', 'pipeline_rail', 'workspace_tabs',
     'chat_input', 'original_sql'].forEach(id => {
        const element = document.getElementById(id);
        if (element) observer.observe(element);
    });
    window.addEventListener('resize', schedule);
    window.visualViewport?.addEventListener('resize', schedule);
    schedule();
    window.__ssLayoutCleanup = () => {
        observer.disconnect();
        cancelAnimationFrame(frame);
        window.removeEventListener('resize', schedule);
        window.visualViewport?.removeEventListener('resize', schedule);
    };
}
"""


def hero_html(environment: str = "Local") -> str:
    return f"""
    <div class='ss-hero'>
      <div><div class='ss-eyebrow'>SchemaShift • CMU Agentic AI Capstone</div>
      <h1>Schema migration workspace</h1>
      <p>Migrate SQL with deterministic validation, visible evidence, and review when needed.</p></div>
      <div><span class='ss-badge'>● {environment}</span><span class='ss-badge'>Human review enabled</span></div>
    </div>"""
