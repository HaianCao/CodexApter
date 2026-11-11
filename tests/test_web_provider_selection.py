"""Provider changes select only model defaults belonging to that provider."""

import re
import shutil
import subprocess

import pytest

from codexapter.web import _HTML


def test_model_selection_tracks_provider_without_restricting_manual_entry():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the frontend regression harness")
    changed = re.search(r"    async function providerChanged\(\)\{.*?\n    \}", _HTML, re.DOTALL).group(0)
    loaded = re.search(r"    async function loadConfig\(\)\{[^\n]+", _HTML).group(0)
    assert "$('providerSelect').addEventListener('change',providerChanged)" in _HTML
    harness = r"""
const assert=require('node:assert/strict');
const providers=[{id:'one',default_model:'one-fallback'},{id:'two',default_model:'two-model'},{id:'three'}];
let state={config:{providers,defaults:{provider:'one',model:'one-saved',reasoning_effort:'high'}}};
let selected=providers[0],input={value:'stale-model'},effort={},observed=[];
const editor={hidden:false,classList:{add(value){assert.equal(value,'hidden');editor.hidden=true}}};
const key={value:'synthetic-unsaved-key'},editorId={value:'one'};
function $(id){return {modelInput:input,reasoning:effort,providerForm:editor,apiKey:key,providerId:editorId}[id]}
function selectedProvider(){return selected}
function showProvider(){}
function fillProviderSelect(){selected=providers.find(p=>p.id===state.config.defaults.provider)||providers[0]}
async function loadModels(){observed.push({id:selected?.id,model:input.value})}
async function api(){return state.config}
"""
    checks = r"""
(async()=>{
  await loadConfig();assert.equal(input.value,'one-saved');assert.equal(effort.value,'high');
  assert(editor.hidden);assert.equal(key.value,'');assert.equal(editorId.value,'');
  editor.hidden=false;key.value='synthetic-unsaved-key';editorId.value='one';
  input.value='manual-on-one';selected=providers[1];await providerChanged();assert.equal(input.value,'two-model');
  assert(editor.hidden);assert.equal(key.value,'');assert.equal(editorId.value,'');
  selected=providers[2];await providerChanged();assert.equal(input.value,'');
  input.value='manual-exact-id';await loadModels();assert.equal(input.value,'manual-exact-id');
  selected=providers[0];await providerChanged();assert.equal(input.value,'one-saved');
  state.config.defaults.model='';await providerChanged();assert.equal(input.value,'one-fallback');
  state.config.defaults={provider:'deleted',model:'deleted-model'};await loadConfig();assert.equal(input.value,'one-fallback');
  selected=null;await providerChanged();assert.equal(input.value,'');
  assert(!observed.some(x=>x.id==='two'&&x.model!=='two-model'));
  assert(!observed.some(x=>x.model==='deleted-model'));
  console.log('passed');
})().catch(error=>{console.error(error);process.exitCode=1});
"""
    result = subprocess.run(
        [node, "-"],
        input=harness + changed + "\n" + loaded + "\n" + checks,
        encoding="utf-8",
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "passed"
