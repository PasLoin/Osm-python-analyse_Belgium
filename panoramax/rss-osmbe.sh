curl -s "https://panoramax.osm.be/api/collections?format=rss&limit=10" | python3 -c "
import sys, re, html
xml = sys.stdin.read()
items = re.findall(r'<item>.*?</item>', xml, re.S)
print(len(items), 'entrees dans le flux')
for it in items[:80]:
    title = re.search(r'<title>(.*?)</title>', it)
    pub = re.search(r'<pubDate>(.*?)</pubDate>', it)
    guid = re.search(r'<guid[^>]*>(.*?)</guid>', it)
    author = re.search(r'<author>(.*?)</author>', it)
    point = re.search(r'<georss:point>([\d.\-]+) ([\d.\-]+)</georss:point>', it)
    url = guid.group(1) if guid else '?'
    cid = url.rsplit('/', 1)[-1]
    if point:
        lon, lat = point.group(1), point.group(2)
        web = f'https://panoramax.osm.be/?focus=map&map=17/{lat}/{lon}&seq={cid}'
    else:
        web = '(pas de coordonnees dans le flux)'
    print('-' * 70)
    print(pub.group(1) if pub else '?', '|', (author.group(1) if author else '?')[:15], '|', html.unescape(title.group(1) if title else '?')[:40])
    print('   id  :', cid)
    print('   api :', url)
    print('   web :', web)
"
