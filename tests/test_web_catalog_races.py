import json
import re
import shutil
import subprocess

import pytest

from codex_adapter.web import _HTML


@pytest.mark.parametrize("scenario", ["load", "refresh", "stale_error", "deselect"])
def test_late_catalog_results_cannot_change_current_provider(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the frontend async regression harness")
    functions = "\n".join(re.search(r"    async function " + name + r"\(\)\{.*?\n    \}", _HTML, re.DOTALL).group(0) for name in ("loadModels", "refreshModels"))
    harness = r"""
const assert=require('node:assert/strict');
let provider={id:'A',discovery:{enabled:true}},pending={},messages=[],renders=[];
let state={models:[],stale:false,catalogGeneration:0},button={disabled:false,innerHTML:''};
function selectedProvider(){return provider} function $(id){return button}
function notify(message){messages.push(message)}
function renderModels(){renders.push([...state.models])}
function api(path,options){let key=options?JSON.parse(options.body).provider:path.split('=')[1];return new Promise((resolve,reject)=>pending[key]={resolve,reject})}
"""
    checks = r"""
(async()=>{
  const refresh=scenario==='refresh',operation=refresh?refreshModels:loadModels;
  if(!refresh)provider.discovery.enabled=false;
  let a=operation();provider={id:'B',discovery:{enabled:refresh}};let b=operation();
  if(scenario==='deselect')provider=null;
  pending.B.resolve({models:['B-model']});await b;
  if(scenario==='stale_error')pending.A.reject(new Error('stale A failure'));
  else pending.A.resolve({models:['A-model']});await a;
  assert(!messages.includes('stale A failure'));
  assert(!state.models.includes('A-model'));
  if(scenario!=='deselect')assert.deepEqual(state.models,['B-model']);
  console.log(JSON.stringify({scenario,passed:true}));
})().catch(error=>{console.error(error);process.exitCode=1});
"""
    result = subprocess.run([node, "-"], input=harness + functions + "\nconst scenario=" + json.dumps(scenario) + ";\n" + checks, encoding="utf-8", capture_output=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["passed"]
