#!/usr/bin/env python3
"""
Vérifie les URL des POI OSM (website / contact:website) extraits en GeoJSON,
et génère un rapport CSV + un résumé Markdown.

Usage:
    python3 check_urls.py pois.geojson report.csv summary.md \
        --concurrency 20 --timeout 15
"""

import argparse
import asyncio
import csv
import json
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

import aiohttp

USER_AGENT = "osm-website-checker/1.0 (+https://github.com/PasLoin/Osm-python-analyse_Belgium)"


def normalize_url(raw: str) -> str | None:
    """Ajoute un schéma si absent, rejette les valeurs manifestement invalides."""
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    if not parsed.netloc:
        return None
    return raw


def osm_link(osm_type: str, osm_id: int) -> str:
    return f"https://www.openstreetmap.org/{osm_type}/{osm_id}"


def load_pois(geojson_path: str):
    """Lit le GeoJSON produit par osmium export et renvoie une liste de POI à tester."""
    with open(geojson_path, encoding="utf-8") as f:
        data = json.load(f)

    pois = []
    for feature in data["features"]:
        props = feature.get("properties", {})
        tags = props.get("tags", props)  # selon la version d'osmium, les tags peuvent être à plat

        # osmium export met généralement le type/id dans "@id" (ex: "n123456") ou dans "id"
        raw_id = props.get("@id") or props.get("id") or feature.get("id", "")
        raw_id = str(raw_id)
        type_map = {"n": "node", "w": "way", "r": "relation"}
        if raw_id and raw_id[0] in type_map:
            osm_type = type_map[raw_id[0]]
            osm_id = raw_id[1:]
        else:
            osm_type, osm_id = "node", raw_id

        name = tags.get("name", "(sans nom)")

        for tag_key in ("website", "contact:website"):
            raw_url = tags.get(tag_key)
            if not raw_url:
                continue
            url = normalize_url(raw_url)
            pois.append(
                {
                    "osm_type": osm_type,
                    "osm_id": osm_id,
                    "name": name,
                    "tag": tag_key,
                    "raw_value": raw_url,
                    "url": url,
                }
            )
    return pois


async def check_one(session: aiohttp.ClientSession, poi: dict, sem: asyncio.Semaphore, timeout: int):
    async with sem:
        result = dict(poi)
        if not poi["url"]:
            result.update(status="invalid_url", http_code=None, final_url=None, error="URL vide ou invalide")
            return result

        for method in ("HEAD", "GET"):
            try:
                async with session.request(
                    method,
                    poi["url"],
                    allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    headers={"User-Agent": USER_AGENT},
                ) as resp:
                    code = resp.status
                    # certains serveurs refusent HEAD (405/501) : on retente en GET
                    if method == "HEAD" and code in (405, 501):
                        continue
                    result.update(
                        status="ok" if code < 400 else "error",
                        http_code=code,
                        final_url=str(resp.url),
                        error=None,
                    )
                    return result
            except asyncio.TimeoutError:
                result.update(status="timeout", http_code=None, final_url=None, error="Timeout")
                return result
            except aiohttp.ClientConnectorError as e:
                result.update(status="dns_or_connection_error", http_code=None, final_url=None, error=str(e))
                return result
            except aiohttp.ClientError as e:
                result.update(status="client_error", http_code=None, final_url=None, error=str(e))
                return result

        return result


async def check_all(pois: list, concurrency: int, timeout: int):
    sem = asyncio.Semaphore(concurrency)
    connector = aiohttp.TCPConnector(limit=concurrency, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [check_one(session, poi, sem, timeout) for poi in pois]
        results = []
        for i, coro in enumerate(asyncio.as_completed(tasks), 1):
            res = await coro
            results.append(res)
            if i % 50 == 0 or i == len(tasks):
                print(f"  ... {i}/{len(tasks)} vérifiés", file=sys.stderr)
        return results


def write_csv(results: list, csv_path: str):
    fields = ["osm_type", "osm_id", "name", "tag", "raw_value", "url", "final_url", "status", "http_code", "error"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in results:
            writer.writerow({k: r.get(k, "") for k in fields})


def write_summary(results: list, summary_path: str, geojson_source: str, total_extracted: int, limit: int):
    total = len(results)
    by_status = {}
    for r in results:
        by_status.setdefault(r["status"], []).append(r)

    n_ok = len(by_status.get("ok", []))
    n_problem = total - n_ok

    lines = []
    lines.append(f"# Rapport de vérification des sites web OSM\n")
    lines.append(f"- Source : `{geojson_source}`")
    lines.append(f"- Généré le : {datetime.now(timezone.utc).isoformat(timespec='seconds')} UTC")
    lines.append(f"- POI extraits du PBF (website/contact:website) : **{total_extracted}**")
    if limit and limit > 0 and limit < total_extracted:
        lines.append(f"- ⚠️ Limite active : seuls **{total}** POI ont été testés sur {total_extracted} (paramètre `max_pois={limit}`)")
    else:
        lines.append(f"- POI testés : **{total}**")
    lines.append(f"- OK (2xx/3xx) : **{n_ok}**")
    lines.append(f"- À vérifier : **{n_problem}**\n")

    lines.append("## Répartition par statut\n")
    lines.append("| Statut | Nombre |")
    lines.append("|---|---|")
    for status, items in sorted(by_status.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"| {status} | {len(items)} |")
    lines.append("")

    problems = [r for r in results if r["status"] != "ok"]
    problems.sort(key=lambda r: (r["status"], r["name"]))

    if problems:
        lines.append("## POI à vérifier\n")
        lines.append("| Nom | Statut | Code | Tag | URL | Fiche OSM |")
        lines.append("|---|---|---|---|---|---|")
        for r in problems:
            osm_url = osm_link(r["osm_type"], r["osm_id"])
            code = r["http_code"] if r["http_code"] else "-"
            url_display = (r["url"] or r["raw_value"] or "").replace("|", "\\|")
            lines.append(
                f"| {r['name']} | {r['status']} | {code} | {r['tag']} | {url_display} | [{r['osm_type']}/{r['osm_id']}]({osm_url}) |"
            )
    else:
        lines.append("Aucun problème détecté. 🎉")

    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("geojson", help="Fichier GeoJSON produit par extract_pois.sh")
    parser.add_argument("csv_out", help="Chemin du rapport CSV détaillé")
    parser.add_argument("summary_out", help="Chemin du résumé Markdown")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=15, help="Timeout par requête (secondes)")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Nombre max de POI à tester (0 ou négatif = pas de limite). Utile pour tester le script avant un run complet.",
    )
    args = parser.parse_args()

    print(f"==> Chargement des POI depuis {args.geojson}...", file=sys.stderr)
    pois = load_pois(args.geojson)
    total_extracted = len(pois)
    print(f"==> {total_extracted} URL extraites au total.", file=sys.stderr)

    if args.limit and args.limit > 0 and args.limit < len(pois):
        print(f"==> Limite active : seuls les {args.limit} premiers POI seront testés.", file=sys.stderr)
        pois = pois[: args.limit]

    print(f"==> {len(pois)} URL à tester (concurrence={args.concurrency})...", file=sys.stderr)

    results = asyncio.run(check_all(pois, args.concurrency, args.timeout))

    write_csv(results, args.csv_out)
    write_summary(results, args.summary_out, args.geojson, total_extracted, args.limit)

    print(f"==> Rapport CSV : {args.csv_out}", file=sys.stderr)
    print(f"==> Résumé      : {args.summary_out}", file=sys.stderr)


if __name__ == "__main__":
    main()
