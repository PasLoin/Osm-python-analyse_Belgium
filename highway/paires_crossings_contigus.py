#### usage 
### python3 chaines_crossings_contigus.py Brussels-daily.pbf
#minmax
### python3 chaines_crossings_contigus.py Brussels-daily.pbf -o paires.csv --min-ways 2 --max-ways 2
### python3 chaines_crossings_contigus.py Brussels-daily.pbf -o tout.csv --min-ways 2

import argparse
import collections
import csv

import osmium

IN_SCOPE = {"yes", "no", "incorrect"}


def is_crossing_way(tags):
    return tags.get("highway") == "footway" and tags.get("footway") == "crossing"


class Loader(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.node_tags = {}
        self.ways = {}
        self.locations = {}

    def node(self, n):
        if len(n.tags):
            self.node_tags[n.id] = dict(n.tags)

    def way(self, w):
        tags = dict(w.tags)
        if "highway" not in tags and "footway" not in tags:
            return
        refs = [r.ref for r in w.nodes]
        self.ways[w.id] = (tags, refs)
        if is_crossing_way(tags):
            for r in (w.nodes[0], w.nodes[-1]):
                if r.location.valid():
                    self.locations[r.ref] = (r.location.lat, r.location.lon)


def build(pbf):
    data = Loader()
    data.apply_file(pbf, locations=True)
    node_tags, ways = data.node_tags, data.ways

    node_ways = collections.defaultdict(list)
    for wid, (_, refs) in ways.items():
        for ref in set(refs):
            node_ways[ref].append(wid)

    endpoint_of = collections.defaultdict(list)
    for wid, (tags, refs) in ways.items():
        if is_crossing_way(tags):
            for e in {refs[0], refs[-1]}:
                endpoint_of[e].append(wid)

    junctions = set()
    for nid, wids in endpoint_of.items():
        if len(wids) < 2:
            continue
        t = node_tags.get(nid, {})
        if t.get("barrier") == "kerb" or t.get("highway") == "crossing":
            continue
        if any(w not in wids for w in node_ways[nid]):
            continue
        junctions.add(nid)

    adjacency = collections.defaultdict(set)
    for j in junctions:
        for a in endpoint_of[j]:
            for b in endpoint_of[j]:
                if a != b:
                    adjacency[a].add(b)

    seen = set()
    components = []
    for start in adjacency:
        if start in seen:
            continue
        stack, comp = [start], set()
        while stack:
            w = stack.pop()
            if w in comp:
                continue
            comp.add(w)
            stack.extend(adjacency[w])
        seen |= comp
        components.append(comp)

    def crossing_nodes(wid):
        return [r for r in ways[wid][1] if node_tags.get(r, {}).get("highway") == "crossing"]

    def crossed(nid):
        out = set()
        for w in node_ways[nid]:
            t = ways[w][0]
            if t.get("highway") == "footway":
                continue
            out.add((t.get("highway", ""), t.get("name:fr") or t.get("name") or ""))
        return sorted(out)

    def ordered(comp):
        degree = {w: len(adjacency[w] & comp) for w in comp}
        if any(d > 2 for d in degree.values()):
            return None
        ends = [w for w, d in degree.items() if d == 1]
        if not ends:
            return None
        seq = [ends[0]]
        while len(seq) < len(comp):
            nxt = [x for x in adjacency[seq[-1]] & comp if x not in seq]
            if not nxt:
                return None
            seq.append(nxt[0])
        return seq

    results = []
    for comp in components:
        seq = ordered(comp)
        branched = seq is None
        wids = seq or sorted(comp)
        comp_junctions = sorted(j for j in junctions if set(endpoint_of[j]) <= comp)
        xings = []
        for w in wids:
            for x in crossing_nodes(w):
                if all(x != y[0] for y in xings):
                    xings.append((x, node_tags[x].get("tactile_paving", ""), crossed(x)))
        middle_without_crossing = (
            not branched and len(wids) == 3 and not crossing_nodes(wids[1])
        )
        lat, lon = data.locations.get(comp_junctions[0], ("", "")) if comp_junctions else ("", "")
        results.append({
            "nb_ways": len(comp),
            "branched": branched,
            "ways": wids,
            "junctions": comp_junctions,
            "crossings": xings,
            "in_scope": sum(1 for _, tp, _ in xings if tp in IN_SCOPE),
            "middle_without_crossing": middle_without_crossing,
            "streets": sorted({name for _, _, cr in xings for _, name in cr if name}),
            "lat": lat,
            "lon": lon,
        })
    results.sort(key=lambda r: (-r["in_scope"], -r["nb_ways"], r["ways"][0]))
    return results


def write_csv(results, path, min_ways, max_ways):
    rows = [r for r in results if r["nb_ways"] >= min_ways and (max_ways is None or r["nb_ways"] <= max_ways)]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "nb_ways", "ramifiee", "milieu_sans_crossing", "ways_dans_l_ordre",
            "noeuds_jonction", "crossings", "nb_crossings_en_perimetre",
            "rues", "lat", "lon", "josm",
        ])
        for r in rows:
            objects = ",".join([f"w{w}" for w in r["ways"]] + [f"n{j}" for j in r["junctions"]])
            writer.writerow([
                r["nb_ways"],
                "oui" if r["branched"] else "non",
                "oui" if r["middle_without_crossing"] else "non",
                " ".join(map(str, r["ways"])),
                " ".join(map(str, r["junctions"])),
                " ".join(
                    f"{x}({tp or '-'}:{'|'.join(h for h, _ in cr) or '-'})"
                    for x, tp, cr in r["crossings"]
                ),
                r["in_scope"],
                " / ".join(r["streets"]),
                r["lat"],
                r["lon"],
                f"http://127.0.0.1:8111/load_object?objects={objects}",
            ])
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pbf")
    parser.add_argument("-o", "--output", default="chaines_crossings_contigus.csv")
    parser.add_argument("--min-ways", type=int, default=3)
    parser.add_argument("--max-ways", type=int, default=None)
    args = parser.parse_args()

    results = build(args.pbf)
    rows = write_csv(results, args.output, args.min_ways, args.max_ways)

    sizes = collections.Counter(r["nb_ways"] for r in results)
    print("Composantes par nombre de ways :", dict(sorted(sizes.items())))
    print(f"{len(rows)} lignes écrites dans {args.output} "
          f"({sum(1 for r in rows if r['in_scope'])} avec au moins un crossing en périmètre, "
          f"{sum(1 for r in rows if r['branched'])} ramifiées)")


if __name__ == "__main__":
    main()
