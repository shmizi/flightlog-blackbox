// Validate a workflow's nodes against the node definitions shipped with an n8n install.
// usage: node n8n/validate_workflow.js <n8n node_modules dir> <workflow.json>
// Checks node types and versions, parameter names, option values and required fields the way
// the n8n editor resolves them on load, and lists the credentials each node will ask for.
const fs = require('fs');
const path = require('path');

const nm = process.argv[2];
const raw = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const wf = Array.isArray(raw) ? raw[0] : raw;     // n8n export:workflow writes an array
const req = (m) => require(path.join(nm, m));
const { NodeHelpers } = req('n8n-workflow');

function loadNodeType(type) {
  const [pkgName, nodeName] = [type.slice(0, type.lastIndexOf('.')), type.slice(type.lastIndexOf('.') + 1)];
  const pkgDir = path.join(nm, pkgName);
  const pkg = JSON.parse(fs.readFileSync(path.join(pkgDir, 'package.json'), 'utf8'));
  for (const file of pkg.n8n.nodes) {
    const base = path.basename(file, '.node.js');
    if (base.toLowerCase() !== nodeName.toLowerCase()) continue;
    const mod = require(path.join(pkgDir, file));
    for (const Cls of Object.values(mod)) {
      if (typeof Cls !== 'function') continue;
      const inst = new Cls();
      const name = (inst.description || {}).name;
      if (name === nodeName) return inst;
    }
  }
  return null;
}

function describe(inst, version) {
  if (inst.nodeVersions) {
    const versions = Object.keys(inst.nodeVersions).map(Number);
    const d = inst.nodeVersions[version];
    return { versions, description: d ? d.description : null };
  }
  const v = inst.description.version;
  const versions = Array.isArray(v) ? v : [v];
  return { versions, description: versions.includes(version) ? inst.description : null };
}

const isExpr = (v) => typeof v === 'string' && v.startsWith('=');

// Recursively check that every key we set exists in the node definition and that option values are allowed.
function checkValues(props, values, where, problems) {
  for (const [key, val] of Object.entries(values || {})) {
    const cands = props.filter((p) => p.name === key);
    if (!cands.length) { problems.push(`${where}${key}: unknown parameter`); continue; }
    const errs = [];
    const ok = cands.some((p) => {
      const local = [];
      if (p.type === 'options' && !isExpr(val) && p.options && !p.options.some((o) => o.value === val)) {
        local.push(`${where}${key}: value ${JSON.stringify(val)} not in [${p.options.map((o) => o.value).join(', ')}]`);
      } else if (p.type === 'collection' && val && typeof val === 'object') {
        checkValues(p.options || [], val, `${where}${key}.`, local);
      } else if (p.type === 'fixedCollection' && val && typeof val === 'object') {
        for (const [group, entries] of Object.entries(val)) {
          const g = (p.options || []).find((o) => o.name === group);
          if (!g) { local.push(`${where}${key}.${group}: unknown group`); continue; }
          for (const e of Array.isArray(entries) ? entries : [entries]) checkValues(g.values || [], e, `${where}${key}.${group}.`, local);
        }
      }
      errs.push(...local);
      return local.length === 0;
    });
    if (!ok) problems.push(...errs.slice(0, 3));
  }
}

let failures = 0;
for (const node of wf.nodes) {
  const inst = loadNodeType(node.type);
  if (!inst) { console.log(`FAIL ${node.name}: node type ${node.type} not found`); failures++; continue; }
  const { versions, description } = describe(inst, node.typeVersion);
  if (!description) {
    console.log(`FAIL ${node.name}: typeVersion ${node.typeVersion} not supported (has ${versions.join(', ')})`);
    failures++;
    continue;
  }
  const problems = [];
  // n8n's node loader (n8n-core directory-loader.js) prepends pollTimes to polling triggers.
  if (description.polling && !description.properties.some((p) => p.name === 'pollTimes')) {
    description.properties.unshift(...req('n8n-core/dist/nodes-loader/constants.js').commonPollingParameters);
  }
  checkValues(description.properties, node.parameters, '', problems);

  // What the editor does on load: resolve parameters (drops ones that are hidden for the
  // chosen options) and collect issues such as missing required values.
  const resolved = NodeHelpers.getNodeParameters(description.properties, node.parameters, true, false, node, description);
  for (const key of Object.keys(node.parameters)) {
    if (resolved && !(key in resolved)) problems.push(`${key}: set but not shown for these options (dropped on load)`);
  }
  const issues = NodeHelpers.getNodeParametersIssues(description.properties, { ...node, parameters: resolved }, description);
  if (issues && issues.parameters) {
    for (const [k, msgs] of Object.entries(issues.parameters)) problems.push(`${k}: ${msgs.join('; ')}`);
  }
  // Credentials the editor would ask for with these settings (respecting their display conditions).
  const creds = (description.credentials || [])
    .filter((c) => c.required && NodeHelpers.displayParameter(resolved, c, node, description))
    .map((c) => c.name);
  const latest = Math.max(...versions);
  const verNote = node.typeVersion < latest ? ` (latest ${latest})` : '';
  if (problems.length) {
    failures++;
    console.log(`FAIL ${node.name} [${node.type} v${node.typeVersion}${verNote}]`);
    problems.forEach((p) => console.log(`     - ${p}`));
  } else {
    console.log(`ok   ${node.name} [${node.type} v${node.typeVersion}${verNote}]` +
      (creds.length ? ` needs credential: ${creds.join(' or ')}` : ''));
  }
}
process.exit(failures ? 1 : 0);
