// Canvas bridge, read side: turns the shown LiteGraph graph into text and
// data for the agent. Pure: no DOM, no ComfyUI `app`; the graph and the node
// type table are passed in. Side-effect free at import.
//
// Shapes (ComfyUI frontend 1.59.1): node ids are strings ("12", or qualified
// like "120:104"), link ids are numbers, `graph.links` is a Map, a node has
// `inputs[i].link` (link id or null), `outputs[i].links` (ids or null),
// `widgets[i] = { name, type, value, options }` and `boundingRect`.

export const LIMITS = {
  outlineDefault: 20000,
  outlineMax: 60000,
  widgetChars: 120,
  comboOptions: 50,
  nodes: 50,
  replyBytes: 256 * 1024,
};

const SEARCH_LIMIT = 30;
const CUT_MARK = "…";
const TITLE_HEIGHT = 30; // LiteGraph.NODE_TITLE_HEIGHT

// ---------------------------------------------------------------- ids

export function sameId(a, b) {
  return String(a) === String(b);
}

export function findNode(graph, id) {
  const want = String(id);
  for (const node of nodesOf(graph)) if (String(node.id) === want) return node;
  return null;
}

// id -> node, built once per call and reused for every link end.
function nodeIndex(graph) {
  return new Map(nodesOf(graph).map((n) => [String(n.id), n]));
}

function nodesOf(graph) {
  return Array.isArray(graph?.nodes) ? graph.nodes : [];
}

const PLAIN_NUMBER = /^\d+$/;

// Numeric order when both ids are plain digits ("2" before "10"), else string order.
function compareIds(a, b) {
  const x = String(a);
  const y = String(b);
  if (PLAIN_NUMBER.test(x) && PLAIN_NUMBER.test(y)) {
    const d = Number(x) - Number(y);
    if (d !== 0) return d;
  }
  return x < y ? -1 : x > y ? 1 : 0;
}

function sortedNodes(graph) {
  return [...nodesOf(graph)].sort((a, b) => compareIds(a.id, b.id));
}

// ------------------------------------------------------------ values

// Never throws, whatever the widget holds.
function safeText(value) {
  try {
    switch (typeof value) {
      case "string":
        return value;
      case "number":
      case "boolean":
      case "undefined":
      case "bigint":
        return String(value);
      case "symbol":
        return "[symbol]";
      case "function":
        return "[function]";
      default: {
        if (value === null) return "null";
        const json = JSON.stringify(value);
        return json === undefined ? "[object]" : json;
      }
    }
  } catch {
    return "[unprintable]";
  }
}

function cut(text, max) {
  return text.length > max ? text.slice(0, max) + CUT_MARK : text;
}

const BARE_STRING = /^[^\s"=]+$/;

function outlineValue(value) {
  let text = safeText(value);
  if (typeof value === "string" && !BARE_STRING.test(text)) text = JSON.stringify(text);
  return cut(text, LIMITS.widgetChars);
}

// A JSON-safe copy for replies: NaN, Infinity, bigint etc. become strings.
function jsonValue(value) {
  switch (typeof value) {
    case "string":
    case "boolean":
      return value;
    case "number":
      return Number.isFinite(value) ? value : String(value);
    case "undefined":
      return null;
    case "bigint":
    case "symbol":
    case "function":
      return safeText(value);
    default:
      if (value === null) return null;
      try {
        return JSON.parse(JSON.stringify(value));
      } catch {
        return "[unprintable]";
      }
  }
}

function widgetsOf(node) {
  return Array.isArray(node.widgets) ? node.widgets.filter((w) => w && w.type !== "button") : [];
}

function modeName(mode) {
  if (mode === 0 || mode === undefined) return "active";
  if (mode === 2) return "muted";
  if (mode === 4) return "bypassed";
  return "other";
}

// ------------------------------------------------------------- links

function linkOf(graph, linkId) {
  if (linkId === null || linkId === undefined) return null;
  const links = graph?.links;
  if (!links) return null;
  const link = typeof links.get === "function" ? links.get(linkId) : links[linkId];
  return link ?? null;
}

function slotName(slots, index) {
  const slot = Array.isArray(slots) ? slots[index] : undefined;
  return slot?.name || slot?.type || String(index);
}

// ------------------------------------------------------- fingerprint

// cyrb53-style mix with two 32-bit lanes; non-cryptographic.
function hash64(text) {
  let h1 = 0xdeadbeef;
  let h2 = 0x41c6ce57;
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    h1 = Math.imul(h1 ^ c, 2654435761);
    h2 = Math.imul(h2 ^ c, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  const hex = (n) => (n >>> 0).toString(16).padStart(8, "0");
  return hex(h2) + hex(h1);
}

function nodeKey(graph, node) {
  const widgets = widgetsOf(node).map((w) => [w.name, typeof w.value, safeText(w.value)]);
  const inputs = [];
  (node.inputs ?? []).forEach((input, slot) => {
    const link = linkOf(graph, input?.link);
    if (link) inputs.push([slot, String(link.origin_id), link.origin_slot]);
  });
  return JSON.stringify([String(node.id), node.type, node.mode ?? 0, node.title ?? "", widgets, inputs]);
}

// Structure only: positions, sizes, colours, selection and view are left out.
export function fingerprint(graph, identity) {
  const head = JSON.stringify([String(identity?.workflow ?? ""), String(identity?.graph ?? "")]);
  const body = sortedNodes(graph).map((n) => nodeKey(graph, n));
  return "fp_" + hash64([head, ...body].join("\n"));
}

// ------------------------------------------------------------ groups

// A group's rectangle. Groups keep boundingRect current when they move.
export function rectOf(item) {
  const r = item.boundingRect;
  if (r && r.length >= 4) return [r[0], r[1], r[2], r[3]];
  return nodeRect(item);
}

// A node's rectangle, title bar included, from its pos and size. Never from
// node.boundingRect: in frontend 1.59.1 that is filled when the canvas draws,
// so it is all zeros for a node just added and the old place for one just moved.
export function nodeRect(node) {
  const [x, y] = node.pos ?? [0, 0];
  const [w, h] = node.size ?? [0, 0];
  return [x, y - TITLE_HEIGHT, w, h + TITLE_HEIGHT];
}

// Rule used: a node is in a group when the centre of the node's bounding
// rectangle (title bar included) lies inside the group's rectangle. The
// frontend notes leave the rule open; centre-point means a node dragged half
// out of a group counts for the group its middle is in, and a node belongs to
// every group whose rectangle holds its centre. Computed from rectangles; no
// group state is touched.
export function groupMembers(graph) {
  const groups = Array.isArray(graph?.groups) ? graph.groups : [];
  const ordered = sortedNodes(graph).map((n) => {
    const [x, y, w, h] = nodeRect(n);
    return { id: String(n.id), cx: x + w / 2, cy: y + h / 2 };
  });
  return groups.map((group) => {
    const [gx, gy, gw, gh] = rectOf(group);
    const ids = ordered
      .filter((n) => n.cx >= gx && n.cx <= gx + gw && n.cy >= gy && n.cy <= gy + gh)
      .map((n) => n.id);
    return { title: String(group.title ?? ""), ids };
  });
}

// ----------------------------------------------------------- types

export function lookupType(types, name) {
  if (!types) return null;
  if (typeof types.get === "function") return types.get(name) ?? null;
  return Object.prototype.hasOwnProperty.call(types, name) ? types[name] : null;
}

function typeEntries(types) {
  if (!types) return [];
  if (typeof types.entries === "function" && typeof types.get === "function") return [...types.entries()];
  return Object.entries(types);
}

function isSubgraphNode(node) {
  try {
    return node.isSubgraphNode?.() === true;
  } catch {
    return false;
  }
}

// Missing means the type is not registered. Without a type table nothing is
// marked missing: has_errors is also set for validation errors, so it says
// nothing about the type.
export function isMissing(node, types) {
  if (!types || isSubgraphNode(node)) return false;
  return lookupType(types, node.type) === null;
}

function comboValues(widget) {
  let values = widget.options?.values;
  try {
    if (typeof values === "function") values = values();
  } catch {
    values = undefined;
  }
  return Array.isArray(values) ? values : null;
}

function optionsSummary(values) {
  if (!values) return {};
  return {
    options: values.slice(0, LIMITS.comboOptions).map(jsonValue),
    options_total: values.length,
  };
}

export function nodeTypeInfo(types, name) {
  const def = lookupType(types, name);
  if (!def) return null;
  const inputs = Object.values(def.inputs ?? {}).map((i) => {
    const info = { name: i.name, type: i.type, required: i.isOptional !== true };
    if (i.default !== undefined) info.default = jsonValue(i.default);
    if (i.min !== undefined) info.min = i.min;
    if (i.max !== undefined) info.max = i.max;
    if (Array.isArray(i.options)) Object.assign(info, optionsSummary(i.options));
    return info;
  });
  const outputs = (def.outputs ?? []).map((o) => ({ name: o.name, type: o.type }));
  return { name, display_name: def.display_name ?? name, category: def.category ?? "", inputs, outputs };
}

export function searchTypes(types, text) {
  const q = String(text ?? "").toLowerCase();
  const hits = [];
  for (const [name, def] of typeEntries(types)) {
    const lower = name.toLowerCase();
    const display = String(def?.display_name ?? "").toLowerCase();
    if (!lower.includes(q) && !display.includes(q)) continue;
    const rank = lower === q || display === q ? 0 : lower.startsWith(q) || display.startsWith(q) ? 1 : 2;
    hits.push({ rank, name, category: String(def?.category ?? "") });
  }
  hits.sort((a, b) => a.rank - b.rank || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  return hits.slice(0, SEARCH_LIMIT).map(({ name, category }) => ({ name, category }));
}

// ----------------------------------------------------------- detail

function widgetDetail(widget) {
  const out = { name: widget.name, type: widget.type, value: jsonValue(widget.value) };
  const opts = widget.options ?? {};
  if (widget.type === "combo") Object.assign(out, optionsSummary(comboValues(widget)));
  if (typeof opts.min === "number") out.min = opts.min;
  if (typeof opts.max === "number") out.max = opts.max;
  const step = opts.step2 ?? opts.step;
  if (typeof step === "number") out.step = step;
  return out;
}

function inputDetail(graph, byId, input, slot) {
  const out = { name: input?.name ?? String(slot), type: input?.type ?? "*", from: null };
  if (input?.widget?.name) out.widget = input.widget.name;
  const link = linkOf(graph, input?.link);
  if (link) {
    const origin = byId.get(String(link.origin_id));
    out.from = {
      id: String(link.origin_id),
      output: slotName(origin?.outputs, link.origin_slot),
      slot: link.origin_slot,
    };
  }
  return out;
}

function outputDetail(graph, byId, output, slot) {
  const to = [];
  for (const linkId of output?.links ?? []) {
    const link = linkOf(graph, linkId);
    if (!link) continue;
    const target = byId.get(String(link.target_id));
    to.push({
      id: String(link.target_id),
      input: slotName(target?.inputs, link.target_slot),
      slot: link.target_slot,
    });
  }
  return { name: output?.name ?? String(slot), type: output?.type ?? "*", to };
}

// The get_nodes shape (spec 6.5), JSON-safe.
export function nodeDetail(graph, node, types) {
  const byId = nodeIndex(graph);
  const detail = {
    id: String(node.id),
    type: node.type,
    title: node.title ?? "",
    mode: modeName(node.mode),
    mode_value: node.mode ?? 0,
    pos: [...(node.pos ?? [0, 0])],
    size: [...(node.size ?? [0, 0])],
    widgets: widgetsOf(node).map(widgetDetail),
    inputs: (node.inputs ?? []).map((input, slot) => inputDetail(graph, byId, input, slot)),
    outputs: (node.outputs ?? []).map((output, slot) => outputDetail(graph, byId, output, slot)),
  };
  if (isSubgraphNode(node)) detail.subgraph = true;
  if (isMissing(node, types)) detail.missing = true;
  return detail;
}

// ---------------------------------------------------------- outline

// Sources first. Layered Kahn: each pass takes every node whose feeders are
// all placed, in id order. Unconnected nodes are sources, so they come in the
// first pass; nodes caught in a cycle never become ready and follow in id order.
function topologicalOrder(graph) {
  const nodes = sortedNodes(graph);
  const byId = new Map(nodes.map((n) => [String(n.id), n]));
  const feeders = new Map();
  const consumers = new Map();
  for (const node of nodes) {
    const id = String(node.id);
    feeders.set(id, new Set());
    consumers.set(id, []);
  }
  for (const node of nodes) {
    for (const input of node.inputs ?? []) {
      const link = linkOf(graph, input?.link);
      const from = link ? String(link.origin_id) : null;
      if (from === null || !byId.has(from)) continue;
      const id = String(node.id);
      if (!feeders.get(id).has(from)) {
        feeders.get(id).add(from);
        consumers.get(from).push(id);
      }
    }
  }
  const waiting = new Map(nodes.map((n) => [String(n.id), feeders.get(String(n.id)).size]));
  const placed = new Set();
  const order = [];
  let layer = nodes.filter((n) => waiting.get(String(n.id)) === 0).map((n) => String(n.id));
  while (layer.length > 0) {
    layer.sort(compareIds);
    const next = [];
    for (const id of layer) {
      placed.add(id);
      order.push(byId.get(id));
      for (const to of consumers.get(id)) {
        waiting.set(to, waiting.get(to) - 1);
        if (waiting.get(to) === 0) next.push(to);
      }
    }
    layer = next;
  }
  for (const node of nodes) if (!placed.has(String(node.id))) order.push(node);
  return order;
}

function nodeLines(graph, byId, node, types, level) {
  const tags = [];
  if (isSubgraphNode(node)) tags.push("[subgraph]");
  else if (isMissing(node, types)) tags.push("[missing]");
  if (node.mode === 2) tags.push("[muted]");
  else if (node.mode === 4) tags.push("[bypassed]");
  else if (node.mode !== 0 && node.mode !== undefined) tags.push(`[mode ${safeText(node.mode)}]`);

  let head = `#${node.id} ${node.type}`;
  if (level !== "no_titles" && node.title && node.title !== node.type) head += ` ${JSON.stringify(node.title)}`;
  if (tags.length > 0) head += ` ${tags.join(" ")}`;
  if (level === "full") {
    const values = widgetsOf(node).map((w) => `${w.name}=${outlineValue(w.value)}`);
    if (values.length > 0) head += ` · ${values.join(" ")}`;
  }
  const lines = [head];

  const incoming = [];
  (node.inputs ?? []).forEach((input) => {
    const link = linkOf(graph, input?.link);
    if (!link) return;
    const origin = byId.get(String(link.origin_id));
    incoming.push(`${input.name}: #${link.origin_id}.${slotName(origin?.outputs, link.origin_slot)}`);
  });
  if (incoming.length > 0) lines.push(`    <- ${incoming.join(", ")}`);

  const outgoing = [];
  (node.outputs ?? []).forEach((output, slot) => {
    const targets = [];
    for (const linkId of output?.links ?? []) {
      const link = linkOf(graph, linkId);
      if (!link) continue;
      const target = byId.get(String(link.target_id));
      targets.push(`#${link.target_id}.${slotName(target?.inputs, link.target_slot)}`);
    }
    if (targets.length > 0) outgoing.push(`${output.name || output.type || slot}: ${targets.join(", ")}`);
  });
  if (outgoing.length > 0) lines.push(`    -> ${outgoing.join("; ")}`);
  return lines;
}

function groupsIndex(members) {
  return members.filter((g) => g.ids.length > 0).map((g) => `GROUPS: ${JSON.stringify(g.title)} -> ${g.ids.map((i) => "#" + i).join(" ")}`);
}

function groupsOnly(members, nodeCount) {
  const lines = members.map((g) => `GROUP ${JSON.stringify(g.title)}: ${g.ids.length} nodes`);
  const grouped = new Set(members.flatMap((g) => g.ids)).size;
  lines.push(`NODES: ${nodeCount} total, ${nodeCount - grouped} in no group; node lines left out to fit`);
  return lines;
}

function clampBudget(maxChars) {
  const n = Number(maxChars);
  if (!Number.isFinite(n) || n <= 0) return LIMITS.outlineDefault;
  return Math.min(Math.floor(n), LIMITS.outlineMax);
}

// { text, node_count, detail }. Sheds widget values, then titles, then lists
// groups only; node_count is always the real count.
export function outline(graph, { maxChars = LIMITS.outlineDefault, types } = {}) {
  const budget = clampBudget(maxChars);
  const byId = nodeIndex(graph);
  const ordered = topologicalOrder(graph);
  const members = groupMembers(graph);
  const tail = groupsIndex(members);
  for (const level of ["full", "no_values", "no_titles"]) {
    const lines = ordered.flatMap((node) => nodeLines(graph, byId, node, types, level));
    const text = [...lines, ...tail].join("\n");
    if (text.length <= budget) return { text, node_count: ordered.length, detail: level };
  }
  const text = groupsOnly(members, ordered.length).join("\n");
  return {
    text: text.length <= budget ? text : text.slice(0, Math.max(0, budget - 1)) + CUT_MARK,
    node_count: ordered.length,
    detail: "groups_only",
  };
}
