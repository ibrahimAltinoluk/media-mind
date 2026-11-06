"""reverse_geocoder'ın ASCII yer adlarını GeoNames'teki asıl yazımlarına eşleyen küçük tabloyu üretir.

Çalıştırma (geliştirme bağımlılığı geonamescache gerekir):
    uv run python scripts/build_place_names.py
Çıktı: src/mediamind/data/place_names.tsv.gz  (cc, admin1, ascii_ad, asıl_ad)
"""

import csv
import gzip
import os

import geonamescache
import reverse_geocoder

gc = geonamescache.GeonamesCache(min_city_population=500)
by_pos = {}
for c in gc.get_cities().values():
    by_pos[(c["countrycode"], round(c["latitude"], 3), round(c["longitude"], 3))] = c["name"]

src = os.path.join(os.path.dirname(reverse_geocoder.__file__), "rg_cities1000.csv")
out = os.path.join(os.path.dirname(__file__), "..", "src", "mediamind", "data", "place_names.tsv.gz")
n = 0
with gzip.open(out, "wt", encoding="utf-8") as fh:
    for r in csv.DictReader(open(src, encoding="utf-8")):
        name = by_pos.get((r["cc"], round(float(r["lat"]), 3), round(float(r["lon"]), 3)))
        if name and name != r["name"]:
            fh.write(f"{r['cc']}\t{r['admin1']}\t{r['name']}\t{name}\n")
            n += 1
print(n, "ad düzeltmesi yazıldı →", out)
