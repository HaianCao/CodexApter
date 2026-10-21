from __future__ import annotations

import copy
import json
import secrets
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .catalog import cached_models, fetch_models
from .config import Config, Discovery, Provider, SecretReference, save, validate_base_url
from .credentials import env_file_path, remove_token, store_token
from .errors import AdapterError, ConfigError
from .launcher import start


def public_config(config: Config) -> dict:
    return {
        "defaults": vars(config.defaults),
        "providers": [
            {
                "id": provider.id,
                "display_name": provider.display_name,
                "base_url": provider.base_url,
                "protocol": provider.protocol,
                "execution_mode": provider.execution_mode,
                "auth_secret_ref": provider.auth_secret_ref,
                "default_model": provider.default_model,
                "discovery": vars(provider.discovery),
            }
            for provider in config.providers.values()
        ],
        "secrets": [{"name": ref.name, "env_var": ref.env_var} for ref in config.secrets.values()],
    }


def _validate_provider_id(value: str) -> None:
    import re

    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value):
        raise ConfigError("Provider ID must use lowercase letters, digits, '_' or '-'.")


def _default_secret_names(provider_id: str) -> tuple[str, str]:
    """Derive internal secret identifiers from a validated provider ID."""
    env_var = "".join(char if char.isalnum() else "_" for char in provider_id).upper() + "_TOKEN"
    return f"{provider_id}-main", env_var


def _remove_provider_config(config: Config, provider_id: str) -> str | None:
    provider = config.providers[provider_id]
    secret_ref = provider.auth_secret_ref
    del config.providers[provider_id]
    if config.defaults.provider == provider_id:
        config.defaults.provider = config.defaults.model = None
    for alias in [name for name, model in config.models.items() if model.provider == provider_id]:
        del config.models[alias]
    if secret_ref and not any(item.auth_secret_ref == secret_ref for item in config.providers.values()):
        secret = config.secrets.pop(secret_ref, None)
        if secret and secret.env_var and not any(item.env_var == secret.env_var for item in config.secrets.values()):
            return secret.env_var
    return None


def serve(config: Config) -> int:
    session_token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def _authorized(self) -> bool:
            return secrets.compare_digest(self.headers.get("X-Codex-AI-Token", ""), session_token)

        def _json(self, value, status: int = HTTPStatus.OK):
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 64_000:
                    raise AdapterError("Request body is too large.")
                value = json.loads(self.rfile.read(length))
            except (ValueError, json.JSONDecodeError) as exc:
                raise AdapterError("Invalid JSON request.") from exc
            if not isinstance(value, dict):
                raise AdapterError("Request body must be a JSON object.")
            return value

        def _provider(self, body: dict) -> Provider:
            provider = config.providers.get(str(body.get("provider", "")))
            if not provider:
                raise AdapterError("Choose a configured provider.")
            return provider

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/":
                # The token remains in the URL only for the initial local page load;
                # no configuration or secret is embedded in the HTML response.
                page = _HTML.replace("__SESSION_TOKEN__", session_token).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
                return
            if not self._authorized():
                self._json({"error": "Unauthorized"}, HTTPStatus.FORBIDDEN)
                return
            if parsed.path == "/api/config":
                self._json(public_config(config))
                return
            if parsed.path == "/api/models":
                provider_id = parse_qs(parsed.query).get("provider", [""])[0]
                provider = config.providers.get(provider_id)
                if not provider:
                    self._json({"error": "Unknown provider"}, HTTPStatus.NOT_FOUND)
                    return
                models, stale = cached_models(provider)
                self._json({"models": models, "stale": stale})
                return
            self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self):
            if not self._authorized():
                self._json({"error": "Unauthorized"}, HTTPStatus.FORBIDDEN)
                return
            try:
                body = self._body()
                path = urlparse(self.path).path
                if path == "/api/provider":
                    provider_id = str(body.get("id", "")).strip()
                    _validate_provider_id(provider_id)
                    display_name = str(body.get("display_name", "")).strip()
                    base_url = validate_base_url(str(body.get("base_url", "")).strip())
                    if not display_name:
                        raise ConfigError("Display name is required.")
                    current = config.providers.get(provider_id)
                    # Keep existing custom mappings intact, but new providers get
                    # predictable names without asking users to manage internals.
                    if current and current.auth_secret_ref:
                        secret_ref = current.auth_secret_ref
                        secret = config.secrets.get(secret_ref)
                        secret_env = secret.env_var if secret else _default_secret_names(provider_id)[1]
                    else:
                        secret_ref, secret_env = _default_secret_names(provider_id)
                    config.secrets[secret_ref] = SecretReference(secret_ref, env_var=secret_env)
                    config.providers[provider_id] = Provider(
                        id=provider_id,
                        display_name=display_name,
                        base_url=base_url,
                        protocol="responses",
                        execution_mode="direct",
                        auth_secret_ref=secret_ref,
                        default_model=str(body.get("default_model", "")).strip() or (current.default_model if current else None),
                        discovery=Discovery(enabled=bool(body.get("discovery", True))),
                    )
                    api_key = str(body.get("api_key", ""))
                    if api_key:
                        store_token(secret_env, api_key, config=config)
                    config.env_file = env_file_path(config)
                    save(config)
                    self._json(public_config(config))
                    return
                if path == "/api/default":
                    provider = self._provider(body)
                    model = str(body.get("model", "")).strip()
                    if not model:
                        raise ConfigError("Choose a model or enter a model ID.")
                    config.defaults.provider = provider.id
                    config.defaults.model = model
                    config.defaults.reasoning_effort = str(body.get("reasoning", "medium"))
                    if config.defaults.reasoning_effort not in {"low", "medium", "high"}:
                        raise ConfigError("Reasoning effort must be low, medium, or high.")
                    config.env_file = env_file_path(config)
                    save(config)
                    self._json(public_config(config))
                    return
                if path == "/api/models/refresh":
                    provider = self._provider(body)
                    self._json({"models": fetch_models(config, provider), "stale": False})
                    return
                if path == "/api/run":
                    provider = self._provider(body)
                    model = str(body.get("model", "")).strip()
                    reasoning = str(body.get("reasoning", "medium"))
                    if not model:
                        raise ConfigError("Choose a model or enter a model ID.")
                    if reasoning not in {"low", "medium", "high"}:
                        raise ConfigError("Reasoning effort must be low, medium, or high.")
                    config.defaults.provider, config.defaults.model, config.defaults.reasoning_effort = provider.id, model, reasoning
                    config.env_file = env_file_path(config)
                    save(config)
                    start(config, provider, model, reasoning, [])
                    self._json({"started": True})
                    threading.Thread(target=server.shutdown, daemon=True).start()
                    return
                self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            except (AdapterError, KeyError, TypeError, ValueError) as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        def do_DELETE(self):
            if not self._authorized():
                self._json({"error": "Unauthorized"}, HTTPStatus.FORBIDDEN)
                return
            provider_id = parse_qs(urlparse(self.path).query).get("provider", [""])[0]
            if provider_id not in config.providers:
                self._json({"error": "Unknown provider"}, HTTPStatus.NOT_FOUND)
                return
            updated_config = copy.deepcopy(config)
            env_var_to_remove = _remove_provider_config(updated_config, provider_id)
            try:
                save(updated_config)
            except (OSError, ConfigError) as exc:
                self._json({"error": f"Could not save provider changes: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            config.providers = updated_config.providers
            config.secrets = updated_config.secrets
            config.models = updated_config.models
            config.defaults = updated_config.defaults
            if env_var_to_remove:
                try:
                    remove_token(env_var_to_remove, config=config)
                except OSError:
                    self._json({"error": "Provider was removed, but its saved API key could not be deleted from the .env file."}, HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
            self._json(public_config(config))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_port}/"
    print("Opened Codex AI setup in your browser. Press Ctrl+C here to stop the local server.")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()


_HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Codex AI Setup</title>
<style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#131b2b;--line:#26334a;--muted:#94a3b8;--text:#edf3ff;--accent:#818cf8;--accent2:#6366f1;--danger:#f87171}*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 10% 0%,#1a2540 0,transparent 45%),var(--bg);color:var(--text);font:15px/1.5 Inter,Segoe UI,system-ui,sans-serif}.shell{max-width:1080px;margin:auto;padding:38px 24px 60px}.top{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:24px}.brand{display:flex;align-items:center;gap:13px}.mark{display:grid;place-items:center;width:42px;height:42px;border-radius:13px;background:linear-gradient(135deg,#818cf8,#4f46e5);font-weight:800}.brand h1{margin:0;font-size:23px;letter-spacing:-.4px}.brand p{margin:2px 0 0;color:var(--muted);font-size:13px}.status{color:#c7d2fe;font-size:13px;min-height:20px}.layout{display:grid;grid-template-columns:minmax(280px,.85fr) minmax(360px,1.4fr);gap:18px}.panel{border:1px solid var(--line);border-radius:17px;background:linear-gradient(160deg,#172033ee,#111827f5);padding:22px;box-shadow:0 18px 48px #0003}.panel-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}.panel h2{font-size:16px;margin:0}.eyebrow{color:var(--muted);font-size:12px;margin:3px 0 0}.field{display:block;margin:12px 0}.field span{display:block;color:#b7c4d9;font-size:12px;font-weight:600;margin-bottom:5px}.input,select{width:100%;min-height:42px;border:1px solid #34435d;border-radius:10px;padding:9px 11px;background:#0d1422;color:var(--text);font:inherit;outline:none}.input:focus,select:focus{border-color:var(--accent);box-shadow:0 0 0 3px #818cf822}.row{display:flex;gap:9px;align-items:center}.row>*{flex:1}.actions{display:flex;gap:9px;margin-top:15px}.btn{border:1px solid #35435b;border-radius:10px;background:#1c2940;color:var(--text);padding:9px 13px;font:600 13px inherit;cursor:pointer;transition:.15s}.btn:hover{filter:brightness(1.16);transform:translateY(-1px)}.btn.primary{background:linear-gradient(135deg,var(--accent2),#7c3aed);border-color:#818cf8}.btn.ghost{background:transparent}.btn.danger{color:#fecaca;border-color:#65333b;background:#321b25}.btn:disabled{opacity:.5;cursor:wait;transform:none}.provider-meta{display:flex;gap:7px;flex-wrap:wrap;margin-top:9px}.badge{border:1px solid var(--line);background:#0e1625;border-radius:99px;padding:4px 8px;color:#b9c5d8;font-size:11px}.form{margin-top:16px;padding-top:15px;border-top:1px solid var(--line)}.check{display:flex;align-items:center;gap:8px;color:#b7c4d9;font-size:13px;margin:12px 0}.check input{accent-color:var(--accent)}.model-box{border:1px solid var(--line);border-radius:11px;margin-top:8px;max-height:360px;overflow:auto;background:#0d1422}.model-option{padding:9px 11px;border-bottom:1px solid #202c40;cursor:pointer;overflow-wrap:anywhere;font:13px ui-monospace,Consolas,monospace}.model-option:last-child{border:0}.model-option:hover,.model-option[aria-selected=true]{background:#202b43}.empty{padding:14px;color:var(--muted);font-size:13px}.hint{font-size:12px;color:var(--muted);margin:8px 1px}.divider{height:1px;background:var(--line);margin:18px 0}.hidden{display:none!important}.toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:#202b43;border:1px solid #465777;padding:10px 15px;border-radius:11px;box-shadow:0 10px 30px #0006;font-size:13px;max-width:90vw}.toast.error{border-color:#9f4545;color:#fecaca}@media(max-width:760px){.shell{padding:22px 14px}.layout{grid-template-columns:1fr}.top{align-items:flex-start;flex-direction:column}.status{align-self:flex-start}}
</style></head><body><main class="shell"><header class="top"><div class="brand"><div class="mark">C</div><div><h1>Codex AI</h1><p>Choose a provider and model for your next session.</p></div></div><div id="status" class="status" role="status"></div></header>
<div class="layout"><section class="panel"><div class="panel-head"><div><h2>Provider</h2><p class="eyebrow">Manage your API connection</p></div><button id="addBtn" class="btn ghost" type="button">＋ Add</button></div>
<label class="field"><span>Active provider</span><select id="providerSelect"></select></label><div id="providerMeta" class="provider-meta"></div><div class="actions"><button id="editBtn" class="btn" type="button">Edit selected</button><button id="deleteProvider" class="btn danger" type="button">Delete selected</button></div>
<div id="providerForm" class="form hidden"><div class="row"><label class="field"><span>Provider ID</span><input class="input" id="providerId" placeholder="agentrouter" autocomplete="off"></label><label class="field"><span>Display name</span><input class="input" id="displayName" placeholder="Agent Router" autocomplete="off"></label></div>
<label class="field"><span>Base URL</span><input class="input" id="baseUrl" placeholder="https://provider.example/v1" autocomplete="url"></label>
<div class="row"><label class="field"><span>Secret reference</span><input class="input" id="secretRef" placeholder="provider-main" autocomplete="off"></label><label class="field"><span>Environment variable</span><input class="input" id="secretEnv" placeholder="PROVIDER_API_KEY" autocomplete="off"></label></div>
<label class="field"><span>API key <small>(leave empty to keep current)</small></span><input class="input" id="apiKey" type="password" placeholder="Paste API key" autocomplete="new-password"></label>
<label class="check"><input id="discoveryEnabled" type="checkbox" checked> Fetch models from <code>/models</code></label><div class="actions"><button id="saveProvider" class="btn primary" type="button">Save provider</button><button id="cancelEdit" class="btn" type="button">Cancel</button></div></div>
<div class="divider"></div><p class="hint">Provider IDs are used internally. The selector shows the display name.</p></section>
<section class="panel"><div class="panel-head"><div><h2>Model</h2><p class="eyebrow">Search the catalog or enter a model ID</p></div><button id="refreshBtn" class="btn" type="button">↻ Refresh models</button></div>
<label class="field"><span>Model ID</span><input class="input" id="modelInput" placeholder="Type to search, or enter a model ID" autocomplete="off"></label><div id="modelList" class="model-box" role="listbox"></div><p id="catalogHint" class="hint"></p>
<div class="row"><label class="field"><span>Reasoning effort</span><select id="reasoning"><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></label><div></div></div>
<div class="actions"><button id="saveDefault" class="btn" type="button">Save as default</button><button id="runBtn" class="btn primary" type="button">Run Codex</button></div></section></div></main><div id="toast" class="toast hidden" role="alert"></div>
<script>
'use strict';
// Keep the page background continuous when its content is shorter than the viewport.
const polish=document.createElement('style');polish.textContent='html{min-height:100%;background:#0b1020}body{min-height:100vh;background-color:#0b1020;background-attachment:fixed;background-repeat:no-repeat}';document.head.append(polish);
const SESSION_TOKEN='__SESSION_TOKEN__';
const $=id=>document.getElementById(id);
let state={config:{providers:[],secrets:[],defaults:{}},models:[],stale:false,toastTimer:null};
// Secret reference and env var are implementation details; derive them server-side.
document.getElementById('secretRef')?.closest('.row')?.remove();
document.getElementById('providerId').setAttribute('pattern','[a-z0-9][a-z0-9_-]*');
document.getElementById('providerId').setAttribute('title','Lowercase letters, digits, underscores or hyphens; no spaces.');
function notify(message,error=false){const el=$('toast');el.textContent=message;el.classList.toggle('error',error);el.classList.remove('hidden');clearTimeout(state.toastTimer);state.toastTimer=setTimeout(()=>el.classList.add('hidden'),4200);$('status').textContent=message;}
async function api(path,options={}){const response=await fetch(path,{...options,headers:{'X-Codex-AI-Token':SESSION_TOKEN,'Content-Type':'application/json',...(options.headers||{})},cache:'no-store'});let data={};try{data=await response.json()}catch{}if(!response.ok)throw new Error(data.error||`Request failed (${response.status})`);return data;}
function selectedProvider(){return state.config.providers.find(p=>p.id===$('providerSelect').value)||null;}
function fillProviderSelect(){const select=$('providerSelect');select.replaceChildren();for(const item of state.config.providers){const option=document.createElement('option');option.value=item.id;option.textContent=item.display_name||item.id;select.append(option);}const preferred=state.config.defaults.provider;if(state.config.providers.some(p=>p.id===preferred))select.value=preferred;else if(state.config.providers.length)select.selectedIndex=0;}
function showProvider(){const p=selectedProvider();const meta=$('providerMeta');meta.replaceChildren();if(!p){const empty=document.createElement('span');empty.className='empty';empty.textContent='No providers yet. Add one to get started.';meta.append(empty);$('refreshBtn').disabled=true;return;}for(const text of [p.id,p.base_url,p.protocol==='responses'?'Responses':`Legacy protocol: ${p.protocol}`]){const badge=document.createElement('span');badge.className='badge';badge.textContent=text;meta.append(badge);} $('refreshBtn').disabled=!p.discovery.enabled;}
function renderModels(){const box=$('modelList');box.replaceChildren();const query=$('modelInput').value.trim().toLowerCase();const matches=state.models.filter(model=>model.toLowerCase().includes(query));for(const model of matches){const row=document.createElement('div');row.className='model-option';row.setAttribute('role','option');row.tabIndex=0;row.textContent=model;const choose=()=>{$('modelInput').value=model;renderModels();};row.addEventListener('click',choose);row.addEventListener('keydown',event=>{if(event.key==='Enter'){choose();$('modelInput').focus();}});box.append(row);}if(!matches.length){const empty=document.createElement('div');empty.className='empty';empty.textContent=query?'No catalog match. Pressing Run will use the typed model ID.':'No model catalog loaded. Enter a model ID manually.';box.append(empty);}const p=selectedProvider();$('catalogHint').textContent=state.stale?'Showing a cached catalog that may be out of date.':(p?.discovery.enabled?`${matches.length} matching model${matches.length===1?'':'s'}.`:'This provider has no model discovery; manual model IDs are supported.');}
async function loadConfig(){state.config=await api('/api/config');fillProviderSelect();showProvider();const defaults=state.config.defaults||{};$('modelInput').value=defaults.model||'';$('reasoning').value=defaults.reasoning_effort||'medium';await loadModels();}
async function loadModels(){const p=selectedProvider();state.models=[];state.stale=false;if(!p){renderModels();return;}try{const result=await api(`/api/models?provider=${encodeURIComponent(p.id)}`);state.models=result.models||[];state.stale=!!result.stale;}catch(error){notify(error.message,true);}renderModels();if(p.discovery.enabled)refreshModels();}
function editProvider(){const p=selectedProvider();$('providerForm').classList.remove('hidden');$('providerId').value=p?.id||'';$('providerId').readOnly=!!p;$('displayName').value=p?.display_name||'';$('baseUrl').value=p?.base_url||'';$('apiKey').value='';$('discoveryEnabled').checked=p?.discovery?.enabled??true;}
function cancelEdit(){$('providerForm').classList.add('hidden');}
async function saveProvider(){const payload={id:$('providerId').value.trim(),display_name:$('displayName').value.trim(),base_url:$('baseUrl').value.trim(),api_key:$('apiKey').value,discovery:$('discoveryEnabled').checked};try{await api('/api/provider',{method:'POST',body:JSON.stringify(payload)});$('apiKey').value='';cancelEdit();await loadConfig();notify('Provider saved.');}catch(error){notify(error.message,true);}}
async function removeProvider(){const p=selectedProvider();if(!p)return;if(!confirm(`Delete ${p.display_name||p.id}?`))return;try{await api(`/api/provider?provider=${encodeURIComponent(p.id)}`,{method:'DELETE'});await loadConfig();notify('Provider deleted.');}catch(error){notify(error.message,true);}}
async function refreshModels(){const p=selectedProvider();if(!p)return;const button=$('refreshBtn');button.disabled=true;button.textContent='Loading…';try{const result=await api('/api/models/refresh',{method:'POST',body:JSON.stringify({provider:p.id})});state.models=result.models||[];state.stale=false;renderModels();notify(`Loaded ${state.models.length} models.`);}catch(error){notify(error.message,true);}finally{button.textContent='↻ Refresh models';button.disabled=!selectedProvider()?.discovery.enabled;}}
function selection(){const provider=selectedProvider();const model=$('modelInput').value.trim();if(!provider)throw new Error('Add or select a provider first.');if(!model)throw new Error('Enter or select a model ID.');return {provider:provider.id,model,reasoning:$('reasoning').value};}
async function saveDefault(){try{await api('/api/default',{method:'POST',body:JSON.stringify(selection())});state.config.defaults={...state.config.defaults,provider:$('providerSelect').value,model:$('modelInput').value.trim(),reasoning_effort:$('reasoning').value};notify('Default configuration saved.');}catch(error){notify(error.message,true);}}
async function runCodex(){try{const data=selection();$('runBtn').disabled=true;$('runBtn').textContent='Starting…';await api('/api/run',{method:'POST',body:JSON.stringify(data)});const main=document.createElement('main');main.className='shell';const panel=document.createElement('section');panel.className='panel';const title=document.createElement('h1');title.textContent='Codex is starting';const message=document.createElement('p');message.textContent='You can close this tab. The local setup service has stopped.';panel.append(title,message);main.append(panel);document.body.replaceChildren(main);setTimeout(()=>window.close(),100);}catch(error){$('runBtn').disabled=false;$('runBtn').textContent='Run Codex';notify(error.message,true);}}
$('providerSelect').addEventListener('change',()=>{showProvider();loadModels();});$('modelInput').addEventListener('input',renderModels);$('addBtn').addEventListener('click',()=>{ $('providerSelect').selectedIndex=-1;showProvider();editProvider();$('providerId').readOnly=false;});$('editBtn').addEventListener('click',editProvider);$('saveProvider').addEventListener('click',saveProvider);$('cancelEdit').addEventListener('click',cancelEdit);$('refreshBtn').addEventListener('click',refreshModels);$('saveDefault').addEventListener('click',saveDefault);$('runBtn').addEventListener('click',runCodex);$('deleteProvider').addEventListener('click',removeProvider);loadConfig().catch(error=>notify(error.message,true));
</script></body></html>'''
