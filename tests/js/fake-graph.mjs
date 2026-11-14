// Test-only. Builds graphs of the shapes recorded in the frontend notes for
// ComfyUI frontend 1.59.1: string node ids, numeric link ids, `graph.links`
// as a Map, `input.link` as a link id or null, `output.links` as an array of
// link ids or null, `boundingRect` as [x, y - titleHeight, w, h + titleHeight].

const TITLE_HEIGHT = 30;

function makeNode(spec) {
  const node = {
    id: String(spec.id),
    type: spec.type ?? "Fake",
    title: spec.title ?? spec.type ?? "Fake",
    mode: spec.mode ?? 0,
    pos: spec.pos ?? [0, 0],
    size: spec.size ?? [100, 50],
    color: undefined,
    selected: false,
    has_errors: false,
    widgets: (spec.widgets ?? []).map((w) => ({ type: "text", options: {}, ...w })),
    inputs: (spec.inputs ?? []).map((i) => ({ link: null, ...i })),
    outputs: (spec.outputs ?? []).map((o) => ({ links: null, ...o })),
    isSubgraphNode: () => spec.subgraph === true,
  };
  Object.defineProperty(node, "boundingRect", {
    get() {
      return [node.pos[0], node.pos[1] - TITLE_HEIGHT, node.size[0], node.size[1] + TITLE_HEIGHT];
    },
  });
  return node;
}

export function fakeGraph({ nodes = [], links = [], groups = [], id = "root" } = {}) {
  const graph = {
    id,
    nodes: [],
    links: new Map(),
    groups: groups.map((g) => ({
      title: g.title,
      pos: g.pos,
      size: g.size,
      get boundingRect() {
        return [this.pos[0], this.pos[1], this.size[0], this.size[1]];
      },
    })),
    nextLink: 1,
    getNodeById(nid) {
      return graph.nodes.find((n) => n.id === nid) ?? null;
    },
    addNode(spec) {
      const node = makeNode(spec);
      graph.nodes.push(node);
      return node;
    },
    removeNode(nid) {
      const node = graph.getNodeById(String(nid));
      if (!node) return;
      for (const l of [...graph.links.values()]) {
        if (l.origin_id === node.id || l.target_id === node.id) graph.disconnect(l.target_id, l.target_slot);
      }
      graph.nodes = graph.nodes.filter((n) => n !== node);
    },
    connect(fromId, outSlot, toId, inSlot) {
      const from = graph.getNodeById(String(fromId));
      const to = graph.getNodeById(String(toId));
      const link = {
        id: graph.nextLink++,
        origin_id: from.id,
        origin_slot: outSlot,
        target_id: to.id,
        target_slot: inSlot,
        type: from.outputs[outSlot].type,
      };
      graph.links.set(link.id, link);
      to.inputs[inSlot].link = link.id;
      (from.outputs[outSlot].links ??= []).push(link.id);
      return link;
    },
    disconnect(toId, inSlot) {
      const to = graph.getNodeById(String(toId));
      const lid = to.inputs[inSlot].link;
      if (lid == null) return;
      const link = graph.links.get(lid);
      const from = graph.getNodeById(link.origin_id);
      if (from) from.outputs[link.origin_slot].links = from.outputs[link.origin_slot].links.filter((x) => x !== lid);
      graph.links.delete(lid);
      to.inputs[inSlot].link = null;
    },
    setWidget(nid, name, value) {
      graph.getNodeById(String(nid)).widgets.find((w) => w.name === name).value = value;
    },
  };
  for (const n of nodes) graph.addNode(n);
  for (const [from, outSlot, to, inSlot] of links) graph.connect(from, outSlot, to, inSlot);
  return graph;
}
