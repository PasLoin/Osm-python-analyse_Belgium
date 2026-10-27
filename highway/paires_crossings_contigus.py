import collections
import csv
import sys

import osmium

PBF = sys.argv[1] if len(sys.argv) > 1 else "brussels.osm.pbf"
OUT = sys.argv[2] if len(sys.argv) > 2 else "paires_crossings_contigus.csv"
IN_SCOPE = {"yes", "no", "incorrect"}

node_tags = {}
ways = {}


class Loader(osmium.SimpleHandler):
    def node(self, n):
        if len(n.tags):
            node_tags[n.id] = dict(n.tags)

    def way(self, w):
        t = dict(w.tags)
        if "highway" in t or "footway" in t:
            ways[w.id] = (t, [r.ref for r in w.nodes])


Loader().apply_file(PBF)

node_ways = collections.defaultdict(list)
for wid, (t, refs) in ways.items():
    for ref in set(refs):
        node_ways[ref].append(wid)


def is_crossing_way(t):
    return t.get("highway") == "footway" and t.get("footway") == "crossing"


crossing_ways = {wid for wid, (t, _) in ways.items() if is_crossing_way(t)}

endpoint_of = collections.defaultdict(list)
for wid in crossing_ways:
    refs = ways[wid][1]
    for e in {refs[0], refs[-1]}:
        endpoint_of[e].append(wid)

pure_junctions = set()
for nid, wids in endpoint_of.items():
    if len(wids) < 2:
        continue
    t = node_tags.get(nid, {})
    if t.get("barrier") == "kerb" or t.get("highway") == "crossing":
        continue
    if any(w not in wids for w in node_ways[nid]):
        continue
    pure_junctions.add(nid)


def far_end(wid, nid):
    refs = ways[wid][1]
    return refs[-1] if refs[0] == nid else refs[0]


def crossing_nodes(wid):
    return [r for r in ways[wid][1][1:-1] if node_tags.get(r, {}).get("highway") == "crossing"]


def crossed_highways(nid):
    return sorted({
        ways[w][0].get("highway")
        for w in node_ways[nid]
        if ways[w][0].get("highway") != "footway"
    })


pairs = []
for j in sorted(pure_junctions):
    wids = endpoint_of[j]
    if len(wids) != 2:
        continue
    a, b = wids
    fa, fb = far_end(a, j), far_end(b, j)
    if fa in pure_junctions or fb in pure_junctions:
        continue
    pairs.append((j, a, b, fa, fb))


def describe(wid):
    xs = crossing_nodes(wid)
    return (
        " ".join(map(str, xs)),
        " ".join("|".join(crossed_highways(x)) or "-" for x in xs),
        " ".join(node_tags[x].get("tactile_paving", "-") for x in xs),
    )


with open(OUT, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow([
        "paire", "noeud_jonction", "way_1", "way_2",
        "crossing_nodes_way_1", "voie_traversee_way_1", "tactile_paving_way_1",
        "crossing_nodes_way_2", "voie_traversee_way_2", "tactile_paving_way_2",
        "extremite_ext_way_1", "extremite_ext_way_2",
    ])
    for i, (j, a, b, fa, fb) in enumerate(pairs, 1):
        writer.writerow([i, j, a, b, *describe(a), *describe(b), fa, fb])

print(f"{len(pairs)} paires, {2 * len(pairs)} ways -> {OUT}")
