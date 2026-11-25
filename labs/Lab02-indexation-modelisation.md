## BD516 – Lab 02 : indexation et modélisation des données

> **Chapitre 2 – Indexation et modélisation des données**
> Prérequis : Lab 01 terminé (cluster **green**, Kibana accessible) – Durée : 2 h 30
> Requêtes : bloc `console` → **Kibana Dev Tools** ; bloc `bash` → terminal Ubuntu.

## Objectifs

| Travaux pratiques du programme | Partie |
|---|---|
| Documents, index et opérations CRUD (PUT, GET, POST, DELETE) | B |
| Mapping dynamique vs strict vs *runtime fields* | C |
| Analyzers et tokenizers : impact sur la recherche full-text | D |
| **Définir un mapping adapté pour un jeu de données métier** (templates et composants) | E |
| **Indexer un volume de documents via l'API bulk** | E, F |
| **Tester la Reindex API pour re-mapper un champ** + Update By Query + alias | F |
| Sharding et réplication : stratégies de dimensionnement | G |
| **Mettre en place une politique ILM simple** (hot / warm / cold / delete) | H |
| Snapshots et restauration | I |

## Le jeu de données « BD516 Shop »

Une boutique d'articles de sport fictive : un **catalogue** (~500 produits) et **10 000 commandes** sur les 365 derniers jours. Le générateur est **déterministe** (graine fixe) : tous les stagiaires obtiennent les mêmes documents, seules les dates suivent le jour de génération.

---

## Partie A – Générer les données

```bash
cat > ~/BD516/data/generators/gen_catalog.py <<'BD516_EOF'
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BD516 - Generateur du jeu de donnees metier (catalogue produits + commandes).

Produit des fichiers NDJSON au format de l'API _bulk d'Elasticsearch :
  data/products/products.ndjson         (catalogue, ~500 produits)
  data/orders/orders-001.ndjson ...     (commandes, 1 000 par fichier)
  data/expected-results.txt             (resultats attendus pour les TP)

Le generateur est deterministe (graine fixe) : deux executions produisent
les memes documents ; seules les dates suivent la date du jour (les commandes
couvrent les 365 derniers jours) afin que les requetes "now-30d" aient du sens.

Usage :
  python3 data/generators/gen_catalog.py              # valeurs par defaut
  python3 data/generators/gen_catalog.py --orders 20000
Uniquement la bibliotheque standard Python 3 (aucun pip install).
"""
import argparse
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 516

CATEGORIES = {
    "Randonnée": {
        "code": "RAN",
        "types": [("Chaussures de randonnée", 59, 189), ("Sac à dos", 29, 159),
                  ("Bâtons de marche", 19, 89), ("Veste imperméable", 49, 249),
                  ("Pantalon de randonnée", 29, 99), ("Lampe frontale", 15, 79)],
        "usages": ["la randonnée en montagne", "les treks de plusieurs jours",
                   "les balades en forêt", "la randonnée sous la pluie"],
        "center": [0.9, 0.2, 0.1, 0.1],
    },
    "Running": {
        "code": "RUN",
        "types": [("Chaussures de running", 49, 179), ("Chaussures de trail", 69, 199),
                  ("Montre GPS", 99, 449), ("Tee-shirt technique", 12, 49),
                  ("Ceinture porte-bidon", 9, 39)],
        "usages": ["le running sur route", "le trail sur sentier",
                   "la préparation au marathon", "les sorties fractionnées"],
        "center": [0.6, 0.8, 0.1, 0.1],
    },
    "Vélo": {
        "code": "VEL",
        "types": [("Casque de vélo", 29, 149), ("Vélo tout chemin", 399, 1499),
                  ("Éclairage vélo", 15, 89), ("Cuissard", 29, 119),
                  ("Antivol en U", 19, 79)],
        "usages": ["le vélotaf en ville", "le cyclotourisme",
                   "les sorties sur route", "le VTT en montagne"],
        "center": [0.1, 0.8, 0.6, 0.1],
    },
    "Camping": {
        "code": "CAM",
        "types": [("Tente 2 places", 69, 399), ("Sac de couchage", 39, 299),
                  ("Matelas autogonflant", 29, 129), ("Réchaud à gaz", 19, 99),
                  ("Glacière rigide", 29, 149)],
        "usages": ["le camping en famille", "le bivouac en montagne",
                   "les week-ends en plein air", "le camping itinérant"],
        "center": [0.7, 0.1, 0.1, 0.7],
    },
    "Ski": {
        "code": "SKI",
        "types": [("Skis de randonnée", 299, 899), ("Masque de ski", 29, 189),
                  ("Veste de ski", 99, 449), ("Gants de ski", 19, 99),
                  ("Casque de ski", 49, 199)],
        "usages": ["le ski de piste", "le ski de randonnée",
                   "le freeride hors-piste", "les vacances à la montagne"],
        "center": [0.3, 0.1, 0.9, 0.4],
    },
    "Natation": {
        "code": "NAT",
        "types": [("Lunettes de natation", 9, 49), ("Combinaison néoprène", 99, 399),
                  ("Maillot de bain", 19, 69), ("Bonnet de bain", 5, 19)],
        "usages": ["la natation en piscine", "la natation en eau libre",
                   "le triathlon", "l'aquagym"],
        "center": [0.1, 0.3, 0.2, 0.9],
    },
    "Fitness": {
        "code": "FIT",
        "types": [("Tapis de yoga", 15, 79), ("Haltères réglables", 49, 299),
                  ("Élastiques de musculation", 9, 39), ("Corde à sauter", 7, 29)],
        "usages": ["le fitness à la maison", "le yoga", "la musculation",
                   "le cross-training"],
        "center": [0.2, 0.6, 0.2, 0.6],
    },
    "Escalade": {
        "code": "ESC",
        "types": [("Chaussons d'escalade", 49, 159), ("Baudrier", 39, 119),
                  ("Corde dynamique", 99, 279), ("Casque d'escalade", 39, 119),
                  ("Sac à magnésie", 9, 35)],
        "usages": ["l'escalade en salle", "la grande voie",
                   "le bloc en extérieur", "l'alpinisme"],
        "center": [0.8, 0.3, 0.7, 0.2],
    },
}

BRANDS = ["Altitude", "Azimut", "Boréal", "Iroise", "Kaïros", "Mistral",
          "Nordika", "Ventoux", "Sirocco", "Vercors"]
MODELS = ["Trek", "Alpine", "Evasion", "Horizon", "Summit", "Explorer",
          "Light", "Pro", "Confort", "Expert", "Fusion", "Aventure"]
FEATURES = [
    "Membrane imperméable et respirante",
    "Coutures étanchées pour rester au sec",
    "Semelle crantée pour une excellente adhérence",
    "Tissu stretch déperlant",
    "Fabrication en matériaux recyclés",
    "Ultra léger et compact une fois replié",
    "Réflecteurs pour une meilleure visibilité de nuit",
    "Rembourrage en duvet certifié",
    "Fermetures éclair résistantes à l'eau",
    "Maintien renforcé de la cheville",
    "Séchage rapide après effort",
    "Garantie fabricant de 2 ans",
    "Isolation thermique pour le froid extrême",
    "Réglages multiples pour un ajustement précis",
]
COLORS = ["noir", "bleu", "rouge", "vert", "gris", "jaune", "orange", "blanc"]

CITIES = [  # ville, pays, lat, lon, poids
    ("Paris", "FR", 48.8566, 2.3522, 18), ("Lyon", "FR", 45.7640, 4.8357, 8),
    ("Marseille", "FR", 43.2965, 5.3698, 7), ("Toulouse", "FR", 43.6047, 1.4442, 6),
    ("Nice", "FR", 43.7102, 7.2620, 4), ("Nantes", "FR", 47.2184, -1.5536, 5),
    ("Strasbourg", "FR", 48.5734, 7.7521, 4), ("Montpellier", "FR", 43.6108, 3.8767, 4),
    ("Bordeaux", "FR", 44.8378, -0.5792, 5), ("Lille", "FR", 50.6292, 3.0573, 5),
    ("Rennes", "FR", 48.1173, -1.6778, 4), ("Grenoble", "FR", 45.1885, 5.7245, 5),
    ("Annecy", "FR", 45.8992, 6.1294, 3), ("Chamonix-Mont-Blanc", "FR", 45.9237, 6.8694, 2),
    ("Clermont-Ferrand", "FR", 45.7772, 3.0870, 2), ("Brest", "FR", 48.3904, -4.4861, 2),
    ("Dijon", "FR", 47.3220, 5.0415, 2), ("Tours", "FR", 47.3941, 0.6848, 2),
    ("Bruxelles", "BE", 50.8503, 4.3517, 3), ("Liège", "BE", 50.6326, 5.5797, 1),
    ("Genève", "CH", 46.2044, 6.1432, 2), ("Lausanne", "CH", 46.5197, 6.6323, 1),
    ("Luxembourg", "LU", 49.6116, 6.1319, 1), ("Montréal", "CA", 45.5019, -73.5674, 2),
    ("Québec", "CA", 46.8139, -71.2080, 1),
]
FIRST_NAMES = ["Camille", "Léa", "Manon", "Chloé", "Emma", "Inès", "Sarah", "Julie",
               "Lucas", "Hugo", "Louis", "Nathan", "Gabriel", "Arthur", "Jules", "Thomas",
               "Nicolas", "Julien", "Sophie", "Claire", "Antoine", "Marie", "Paul", "Alice"]
LAST_NAMES = ["Martin", "Bernard", "Dubois", "Thomas", "Robert", "Richard", "Petit",
              "Durand", "Leroy", "Moreau", "Simon", "Laurent", "Lefebvre", "Michel",
              "Garcia", "David", "Bertrand", "Roux", "Vincent", "Fournier", "Morel",
              "Girard", "André", "Mercier", "Dupont", "Lambert", "Bonnet", "Fontaine"]

STATUSES = [("delivered", 62), ("shipped", 12), ("paid", 8), ("new", 4),
            ("cancelled", 9), ("returned", 5)]
CHANNELS = [("web", 55), ("mobile", 30), ("store", 10), ("marketplace", 5)]
PAYMENTS = [("card", 60), ("paypal", 20), ("apple_pay", 10), ("transfer", 5), ("gift_card", 5)]
SEGMENTS = [("particulier", 80), ("pro", 15), ("vip", 5)]
SHIPPING = [("domicile", 50, 4.99, 3), ("relais", 35, 3.49, 4), ("express", 10, 9.90, 1),
            ("retrait_magasin", 5, 0.0, 2)]

POSITIVE = ["Livraison rapide, produit conforme à la description.",
            "Très bonne qualité, je recommande !",
            "Excellent rapport qualité prix, parfait pour mes sorties.",
            "Service client réactif et agréable.",
            "Produit léger et confortable, idéal en montagne.",
            "Colis bien emballé, livraison en avance."]
NEGATIVE = ["Colis arrivé endommagé, emballage insuffisant.",
            "Délai de livraison trop long par rapport à l'annonce.",
            "Taille trop petite, retour effectué sans difficulté.",
            "Produit défectueux à la réception, remboursement demandé.",
            "La couleur ne correspond pas à la photo."]
NEUTRAL = ["Produit correct mais sans plus.",
           "Livraison dans les temps, rien à signaler.",
           "Conforme, mais la notice est uniquement en anglais."]


def wchoice(rng, pairs, w=1):
    """Choix pondere dans une liste de tuples ; le poids est a l'index w."""
    total = sum(p[w] for p in pairs)
    r = rng.random() * total
    acc = 0.0
    for p in pairs:
        acc += p[w]
        if r < acc:
            return p
    return pairs[-1]


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize(v):
    n = math.sqrt(sum(x * x for x in v))
    return [round(x / n, 4) for x in v]


def gen_products(rng, n, anchor):
    products = []
    cats = list(CATEGORIES.items())
    for i in range(n):
        cat_name, cat = cats[i % len(cats)]
        ptype, pmin, pmax = rng.choice(cat["types"])
        brand = rng.choice(BRANDS)
        model = f"{rng.choice(MODELS)} {rng.choice([100, 300, 500, 700, 900])}"
        sku = f"{cat['code']}-{i + 1:04d}"
        price = round(rng.uniform(pmin, pmax), 0) - 0.01
        feats = rng.sample(FEATURES, 3)
        created = anchor - timedelta(days=rng.randint(1, 4 * 365), seconds=rng.randint(0, 86399))
        rating = round(min(5.0, max(1.5, rng.gauss(4.1, 0.6))), 1)
        reviews = int(rng.expovariate(1 / 80))
        stock = 0 if rng.random() < 0.06 else rng.randint(1, 500)
        tags = []
        if (anchor - created).days < 90:
            tags.append("nouveauté")
        if rng.random() < 0.15:
            tags.append("promo")
        if "Fabrication en matériaux recyclés" in feats:
            tags.append("éco-responsable")
        if rating >= 4.6 and reviews >= 100:
            tags.append("best-seller")
        doc = {
            "sku": sku,
            "name": f"{ptype} {model} {brand}",
            "category": cat_name,
            "brand": brand,
            "price": price,
            "stock": stock,
            "rating": rating,
            "reviews_count": reviews,
            "created_at": iso(created),
            "tags": tags,
            "colors": sorted(rng.sample(COLORS, rng.randint(1, 3))),
            "embedding": normalize([c + rng.uniform(-0.15, 0.15) for c in cat["center"]]),
        }
        if rng.random() >= 0.03:  # ~3 % des produits n'ont pas de description
            doc["description"] = (f"{ptype} {brand}, idéal pour {rng.choice(cat['usages'])}. "
                                  f"{feats[0]}. {feats[1]}. {feats[2]}. "
                                  f"Poids : {rng.randint(80, 2500)} g.")
        products.append(doc)
    return products


def gen_customers(rng, n):
    customers = []
    for i in range(n):
        city, country, lat, lon, _ = wchoice(rng, CITIES, w=4)
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        customers.append({
            "id": f"C{i + 1:05d}",
            "name": f"{first} {last}",
            "email": f"{first.lower()}.{last.lower()}{i + 1}@example.fr"
                     .replace("é", "e").replace("è", "e").replace("ë", "e"),
            "segment": wchoice(rng, SEGMENTS)[0],
            "city": city,
            "country": country,
            "location": {"lat": round(lat + rng.uniform(-0.05, 0.05), 5),
                         "lon": round(lon + rng.uniform(-0.05, 0.05), 5)},
        })
    return customers


def gen_orders(rng, n, products, customers, anchor):
    orders = []
    weights = [1.0 / (k + 1) ** 0.8 for k in range(len(customers))]  # clients fideles
    cum = []
    acc = 0.0
    for w in weights:
        acc += w
        cum.append(acc)
    pop = [max(1, p["reviews_count"]) for p in products]
    # Generateur dedie aux dates : les autres valeurs ne dependent pas du jour de generation
    rng_dates = random.Random(SEED + 7)
    year = 365 * 86400
    for i in range(n):
        # date : 365 derniers jours, activite en croissance (plus de commandes recentes)
        while True:
            offset = rng_dates.randint(0, year - 1)
            if rng_dates.random() < 0.55 + 0.45 * (1 - offset / year):
                break
        d = anchor - timedelta(seconds=offset)
        r = rng.random() * acc
        lo, hi = 0, len(cum) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if cum[mid] < r:
                lo = mid + 1
            else:
                hi = mid
        cust = customers[lo]
        n_items = wchoice(rng, [(1, 50), (2, 30), (3, 15), (4, 5)])[0]
        chosen = rng.choices(products, weights=pop, k=n_items)
        items, seen = [], set()
        for p in chosen:
            if p["sku"] in seen:
                continue
            seen.add(p["sku"])
            q = wchoice(rng, [(1, 75), (2, 18), (3, 7)])[0]
            items.append({"sku": p["sku"], "product_name": p["name"],
                          "category": p["category"], "brand": p["brand"],
                          "quantity": q, "unit_price": p["price"]})
        items_total = round(sum(it["quantity"] * it["unit_price"] for it in items), 2)
        ship_method, _, ship_cost, days = wchoice(rng, SHIPPING)
        if items_total >= 50 and ship_method != "express":
            ship_cost = 0.0
        age_days = (anchor - d).days
        if age_days <= 2:
            status = wchoice(rng, [("new", 40), ("paid", 45), ("cancelled", 15)])[0]
        elif age_days <= 7:
            status = wchoice(rng, [("paid", 20), ("shipped", 60), ("cancelled", 10), ("delivered", 10)])[0]
        else:
            status = wchoice(rng, STATUSES)[0]
        doc = {
            "order_id": f"CMD-{i + 1:06d}",
            "order_date": iso(d),
            "status": status,
            "channel": wchoice(rng, CHANNELS)[0],
            "payment_method": wchoice(rng, PAYMENTS)[0],
            "customer": cust,
            "items": items,
            "items_count": sum(it["quantity"] for it in items),
            "total_amount": round(items_total + ship_cost, 2),
            "shipping": {"method": ship_method, "cost": ship_cost,
                         "delivery_days": days + rng.randint(0, 3)},
            "tags": [],
        }
        if d.month == 11 and d.day >= 20:
            doc["tags"].append("black_friday")
        if d.month == 12 and d.day <= 24:
            doc["tags"].append("noel")
        if status in ("delivered", "returned") and rng.random() < 0.4:
            if status == "returned":
                doc["comment"], doc["rating"] = rng.choice(NEGATIVE), rng.randint(1, 2)
            else:
                kind = rng.random()
                if kind < 0.7:
                    doc["comment"], doc["rating"] = rng.choice(POSITIVE), rng.randint(4, 5)
                elif kind < 0.9:
                    doc["comment"], doc["rating"] = rng.choice(NEUTRAL), 3
                else:
                    doc["comment"], doc["rating"] = rng.choice(NEGATIVE), rng.randint(1, 2)
        orders.append(doc)
    orders.sort(key=lambda o: o["order_date"])
    return orders


def write_bulk(path, docs, id_field):
    with open(path, "w", encoding="utf-8") as f:
        for doc in docs:
            f.write(json.dumps({"index": {"_id": doc[id_field]}}, ensure_ascii=False) + "\n")
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Generateur du jeu de donnees BD516")
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--out", default=str(root), help="repertoire data/ (defaut : %(default)s)")
    parser.add_argument("--products", type=int, default=500)
    parser.add_argument("--customers", type=int, default=2000)
    parser.add_argument("--orders", type=int, default=10000)
    parser.add_argument("--chunk", type=int, default=1000, help="documents par fichier bulk")
    args = parser.parse_args()

    rng = random.Random(SEED)
    anchor = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    out = Path(args.out)
    (out / "products").mkdir(parents=True, exist_ok=True)
    (out / "orders").mkdir(parents=True, exist_ok=True)
    for old in (out / "orders").glob("orders-*.ndjson"):
        old.unlink()

    products = gen_products(rng, args.products, anchor)
    customers = gen_customers(rng, args.customers)
    orders = gen_orders(rng, args.orders, products, customers, anchor)

    write_bulk(out / "products" / "products.ndjson", products, "sku")
    files = 0
    for k in range(0, len(orders), args.chunk):
        files += 1
        write_bulk(out / "orders" / f"orders-{files:03d}.ndjson", orders[k:k + args.chunk], "order_id")

    # ---- resultats attendus (pour verifier les requetes des TP) ----
    st = Counter(o["status"] for o in orders)
    ch = Counter(o["channel"] for o in orders)
    seg = Counter(o["customer"]["segment"] for o in orders)
    revenue = sum(o["total_amount"] for o in orders)
    cat_rev = defaultdict(float)
    for o in orders:
        for it in o["items"]:
            cat_rev[it["category"]] += it["quantity"] * it["unit_price"]
    lines = [
        "BD516 - resultats attendus (generes le %s UTC)" % anchor.strftime("%Y-%m-%d"),
        "",
        f"Produits                 : {len(products)} (sans description : {sum('description' not in p for p in products)}, stock = 0 : {sum(p['stock'] == 0 for p in products)})",
        f"Commandes                : {len(orders)} dans {files} fichiers bulk",
        f"Clients distincts        : {len({o['customer']['id'] for o in orders})}",
        f"Commandes avec commentaire : {sum('comment' in o for o in orders)}",
        f"Chiffre d'affaires total : {revenue:.2f} EUR",
        f"Panier moyen             : {revenue / len(orders):.2f} EUR",
        f"Montant max              : {max(o['total_amount'] for o in orders):.2f} EUR",
        "",
        "Commandes par statut     : " + ", ".join(f"{k}={v}" for k, v in st.most_common()),
        "Commandes par canal      : " + ", ".join(f"{k}={v}" for k, v in ch.most_common()),
        "Commandes par segment    : " + ", ".join(f"{k}={v}" for k, v in seg.most_common()),
        "",
        "CA articles par categorie (quantite x prix unitaire) :",
    ]
    for k, v in sorted(cat_rev.items(), key=lambda kv: -kv[1]):
        lines.append(f"   {k:<12} {v:12.2f} EUR")
    (out / "expected-results.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nFichiers ecrits dans {out}/products et {out}/orders")


if __name__ == "__main__":
    main()
BD516_EOF
chmod +x ~/BD516/data/generators/gen_catalog.py
cd ~/BD516 && python3 data/generators/gen_catalog.py
```

Résultat attendu (extrait – conservez ce récapitulatif, il sert de corrigé) :

```text
Produits                 : 500 (sans description : 13, stock = 0 : 26)
Commandes                : 10000 dans 10 fichiers bulk
Clients distincts        : 1765
Commandes avec commentaire : 2589
Chiffre d'affaires total : 3000618.93 EUR
Panier moyen             : 300.06 EUR
Montant max              : 4947.94 EUR

Commandes par statut     : delivered=6110, shipped=1247, cancelled=885, paid=829, returned=515, new=414
Commandes par canal      : web=5542, mobile=3019, store=963, marketplace=476
Commandes par segment    : particulier=8187, pro=1492, vip=321
```

> Ces valeurs sont identiques pour tous (seules les dates dépendent du jour de génération) ; elles sont aussi enregistrées dans `data/expected-results.txt`.

Observez le format attendu par l'API `_bulk` : une ligne d'**action**, une ligne de **document**, et un saut de ligne final.

```bash
head -c 700 data/orders/orders-001.ndjson; echo
```

---

## Partie B – Opérations CRUD

```console
# Création (ou remplacement) d'un document avec un identifiant choisi
PUT bd516-demo/_doc/1
{
  "titre": "Découvrir Elasticsearch",
  "auteur": "Alice Martin",
  "pages": 120,
  "publie_le": "2026-01-15",
  "prix": 29.9,
  "tags": ["recherche", "nosql"]
}

# Lecture : document complet puis source seule
GET bd516-demo/_doc/1
GET bd516-demo/_source/1?_source_includes=titre,prix

# Création avec identifiant généré par Elasticsearch
POST bd516-demo/_doc
{
  "titre": "Logstash en pratique",
  "auteur": "Bruno Petit",
  "pages": 210,
  "publie_le": "2025-10-02",
  "prix": 34.5,
  "tags": ["etl"]
}

# _create refuse d'écraser un document existant : erreur 409 attendue
PUT bd516-demo/_create/1
{
  "titre": "Doublon"
}
```

Mises à jour partielles et contrôle de concurrence optimiste :

```console
# Mise à jour partielle (fusion des champs)
POST bd516-demo/_update/1
{
  "doc": { "prix": 24.9 }
}

# Mise à jour par script Painless
POST bd516-demo/_update/1
{
  "script": {
    "source": "ctx._source.pages += params.ajout",
    "params": { "ajout": 10 }
  }
}

# Notez _version, _seq_no et _primary_term
GET bd516-demo/_doc/1

# Écriture conditionnelle avec un _seq_no périmé : erreur 409 (version_conflict_engine_exception)
PUT bd516-demo/_doc/1?if_seq_no=0&if_primary_term=1
{
  "titre": "Modification concurrente"
}

# Suppression puis vérification
DELETE bd516-demo/_doc/1
HEAD bd516-demo/_doc/1
GET bd516-demo/_count
```

Résultat attendu : `_version: 3` après les deux mises à jour, **409** sur l'écriture conditionnelle, `404 - Not Found` sur le `HEAD`, `count: 1`.

---

## Partie C – Mapping dynamique, strict et runtime

### C.1 Mapping dynamique (inféré)

```console
GET bd516-demo/_mapping
```

Constatez les choix automatiques : `text` + sous-champ `keyword` pour les chaînes, `long`, `float`, `date` (détection de date). Le type est figé au premier document :

```console
# Erreur attendue : "pages" est déjà de type long
POST bd516-demo/_doc
{
  "titre": "Mauvais type",
  "pages": "cent vingt"
}
```

### C.2 Mapping strict (gouvernance)

```console
PUT bd516-strict
{
  "mappings": {
    "dynamic": "strict",
    "properties": {
      "titre": { "type": "text" }
    }
  }
}

# Erreur attendue : strict_dynamic_mapping_exception
PUT bd516-strict/_doc/1
{
  "titre": "Champ inconnu refusé",
  "inconnu": "x"
}
```

### C.3 Runtime fields (souplesse, calcul à la lecture)

```console
PUT bd516-runtime
{
  "mappings": {
    "dynamic": "runtime",
    "properties": {
      "titre": { "type": "text" }
    }
  }
}

PUT bd516-runtime/_doc/1?refresh=true
{
  "titre": "Champs non prévus",
  "inconnu": "x",
  "note": 4
}

# "inconnu" et "note" apparaissent dans la section "runtime" : ils ne sont pas indexés
GET bd516-runtime/_mapping

# ...mais restent interrogeables (évalués à la volée, plus lent sur gros volumes)
GET bd516-runtime/_search
{
  "query": { "term": { "inconnu": "x" } }
}
```

| Mode | Champ inconnu | Avantage | Risque |
|---|---|---|---|
| `true` (défaut) | indexé, mapping ajouté | souplesse | explosion du mapping, types inattendus |
| `runtime` | ajouté comme runtime field | pas d'indexation, schéma à la lecture | requêtes plus coûteuses |
| `false` | conservé dans `_source`, non interrogeable | stabilité | champ invisible pour les recherches |
| `strict` | **document rejeté** | gouvernance forte | les producteurs doivent respecter le contrat |

---

## Partie D – Analyzers et tokenizers

```console
POST _analyze
{
  "analyzer": "standard",
  "text": "L'équipe a testé les chaussures imperméables !"
}

POST _analyze
{
  "analyzer": "french",
  "text": "L'équipe a testé les chaussures imperméables !"
}

POST _analyze
{
  "tokenizer": "standard",
  "filter": ["lowercase", "asciifolding"],
  "text": "L'équipe a testé les chaussures imperméables !"
}

POST _analyze
{
  "analyzer": "keyword",
  "text": "L'équipe a testé les chaussures imperméables !"
}
```

Comparez : `standard` découpe et met en minuscules ; `french` supprime l'article élidé (`l'`), les mots vides (`a`, `les`) et réduit les mots à leur racine (*stemming* : singulier/pluriel, accents) ; `keyword` conserve la chaîne entière (champ exact, tri, agrégations). **Le même analyzer est appliqué au texte indexé et au texte recherché** : c'est ce qui permet à « imperméable » de trouver « imperméables ».

---

## Partie E – Modèle d'index pour les commandes, puis chargement bulk

### E.1 Composants réutilisables

```console
# Paramètres communs à tous les index métier BD516
PUT _component_template/bd516-commun@settings
{
  "template": {
    "settings": {
      "index.number_of_shards": 1,
      "index.number_of_replicas": 1,
      "index.refresh_interval": "1s"
    }
  },
  "_meta": { "description": "BD516 - parametres communs (1 shard primaire, 1 replica)" }
}

# Mapping métier des commandes (contrat strict)
PUT _component_template/bd516-orders@mappings
{
  "template": {
    "mappings": {
      "dynamic": "strict",
      "properties": {
        "order_id":       { "type": "keyword" },
        "order_date":     { "type": "date" },
        "status":         { "type": "keyword" },
        "channel":        { "type": "keyword" },
        "payment_method": { "type": "keyword" },
        "customer": {
          "properties": {
            "id":       { "type": "keyword" },
            "name":     { "type": "text", "fields": { "keyword": { "type": "keyword" } } },
            "email":    { "type": "keyword" },
            "segment":  { "type": "keyword" },
            "city":     { "type": "keyword" },
            "country":  { "type": "keyword" },
            "location": { "type": "geo_point" }
          }
        },
        "items": {
          "type": "nested",
          "properties": {
            "sku":          { "type": "keyword" },
            "product_name": { "type": "text", "analyzer": "french", "fields": { "keyword": { "type": "keyword", "ignore_above": 256 } } },
            "category":     { "type": "keyword" },
            "brand":        { "type": "keyword" },
            "quantity":     { "type": "integer" },
            "unit_price":   { "type": "scaled_float", "scaling_factor": 100 }
          }
        },
        "items_count":  { "type": "integer" },
        "total_amount": { "type": "scaled_float", "scaling_factor": 100 },
        "shipping": {
          "properties": {
            "method":        { "type": "keyword" },
            "cost":          { "type": "scaled_float", "scaling_factor": 100 },
            "delivery_days": { "type": "integer" }
          }
        },
        "comment": { "type": "text", "analyzer": "french" },
        "rating":  { "type": "byte" },
        "tags":    { "type": "keyword" }
      },
      "runtime": {
        "order_weekday": {
          "type": "long",
          "script": { "source": "emit(doc['order_date'].value.getDayOfWeek().getValue())" }
        }
      }
    }
  },
  "_meta": { "description": "BD516 - mapping des commandes" }
}
```

Choix de modélisation à commenter :

| Champ | Type | Pourquoi |
|---|---|---|
| `order_id`, `status`, `channel`… | `keyword` | valeurs exactes : filtres `term`, agrégations, tri |
| `customer.name` | `text` + `keyword` | recherche plein texte **et** valeur exacte |
| `customer.location` | `geo_point` | requêtes de distance, cartes Kibana |
| `items` | `nested` | conserve l'association sku/quantité/prix **de chaque ligne** |
| `total_amount`, `unit_price` | `scaled_float` (×100) | montants au centime, stockage compact |
| `comment` | `text` (`french`) | recherche plein texte en français |
| `order_weekday` | runtime `long` | jour de la semaine (1 = lundi) calculé à la lecture |

### E.2 Modèle d'index composé

```console
PUT _index_template/bd516-orders
{
  "index_patterns": ["bd516-orders*"],
  "priority": 200,
  "composed_of": ["bd516-commun@settings", "bd516-orders@mappings"],
  "template": {
    "aliases": { "bd516-ventes": {} }
  },
  "_meta": { "description": "BD516 - commandes de la boutique" }
}

# Prévisualiser l'index qui serait créé
POST _index_template/_simulate_index/bd516-orders
```

### E.3 Chargement par l'API bulk

```bash
cd ~/BD516
for f in data/orders/orders-*.ndjson; do
  printf '%s : ' "$f"
  esbulk '/bd516-orders/_bulk?filter_path=took,errors' "$f"
done
es POST '/bd516-orders/_refresh'
es GET '/bd516-orders/_count'
```

Résultat attendu : `"errors":false` pour chacun des 10 fichiers, puis `{"count":10000,...}`.

```console
GET _cat/indices/bd516-*?v&h=index,health,pri,rep,docs.count,store.size

# L'alias défini dans le modèle d'index
GET bd516-ventes/_count

# Le contrat strict protège l'index
POST bd516-orders/_doc
{
  "order_id": "CMD-TEST",
  "champ_imprevu": true
}

# Champ runtime : jour de la semaine des 3 commandes les plus récentes
GET bd516-orders/_search
{
  "size": 3,
  "sort": [{ "order_date": "desc" }],
  "_source": ["order_id", "order_date"],
  "fields": ["order_weekday"]
}
```

> **Bonnes pratiques bulk** : lots de 5 à 15 Mo, plusieurs requêtes en parallèle pour les gros volumes, `refresh_interval: -1` et `number_of_replicas: 0` pendant un chargement initial massif (puis rétablir), et toujours vérifier `errors`.

---

## Partie F – Re-mapper un champ : Reindex API, alias et Update By Query

Scénario : le catalogue a été chargé « vite fait » **sans mapping** (`bd516-products-v1`). Il faut le corriger **sans interrompre l'application**, qui interroge l'alias `bd516-products`.

### F.1 Chargement de la v1 (mapping dynamique)

```bash
cd ~/BD516
esbulk '/bd516-products-v1/_bulk?refresh=true&filter_path=took,errors' data/products/products.ndjson
es POST '/_aliases' -d '{"actions":[{"add":{"index":"bd516-products-v1","alias":"bd516-products"}}]}'
```

```console
GET bd516-products-v1/_mapping/field/sku,price,embedding,description

# Problème 1 : sku est un champ text -> la recherche exacte ne trouve rien
GET bd516-products/_search
{
  "query": { "term": { "sku": "RAN-0001" } }
}

# Problème 2 : analyzer standard -> "imperméables" ne trouve pas "imperméable"
GET bd516-products/_count
{
  "query": { "match": { "description": "imperméables" } }
}
```

Résultat attendu : **0** résultat dans les deux cas. `sku` a été analysé (`ran`, `0001`) et l'analyzer `standard` ne cherche que la forme exacte « imperméables », absente des descriptions (qui contiennent « imperméable »).

### F.2 Création de la v2 avec un mapping explicite

```console
PUT bd516-products-v2
{
  "settings": {
    "number_of_shards": 1,
    "number_of_replicas": 1,
    "analysis": {
      "filter": {
        "fr_elision": {
          "type": "elision",
          "articles_case": true,
          "articles": ["l", "m", "t", "qu", "n", "s", "j", "d", "c", "jusqu", "quoiqu", "lorsqu", "puisqu"]
        },
        "fr_stop":    { "type": "stop", "stopwords": "_french_" },
        "fr_stemmer": { "type": "stemmer", "language": "light_french" }
      },
      "analyzer": {
        "bd516_francais": {
          "type": "custom",
          "tokenizer": "standard",
          "filter": ["fr_elision", "lowercase", "fr_stop", "fr_stemmer", "asciifolding"]
        }
      },
      "normalizer": {
        "bd516_tri": { "type": "custom", "filter": ["lowercase", "asciifolding"] }
      }
    }
  },
  "mappings": {
    "dynamic": "strict",
    "properties": {
      "sku":           { "type": "keyword" },
      "name":          { "type": "text", "analyzer": "bd516_francais", "fields": { "keyword": { "type": "keyword" } } },
      "description":   { "type": "text", "analyzer": "bd516_francais" },
      "category":      { "type": "keyword" },
      "brand":         { "type": "keyword" },
      "price":         { "type": "scaled_float", "scaling_factor": 100 },
      "price_band":    { "type": "keyword" },
      "stock":         { "type": "integer" },
      "rating":        { "type": "float" },
      "reviews_count": { "type": "integer" },
      "created_at":    { "type": "date" },
      "ingested_at":   { "type": "date" },
      "tags":          { "type": "keyword" },
      "colors":        { "type": "keyword" },
      "embedding":     { "type": "dense_vector", "dims": 4, "index": true, "similarity": "cosine" }
    }
  }
}

POST bd516-products-v2/_analyze
{
  "analyzer": "bd516_francais",
  "text": "Chaussures de randonnée IMPERMÉABLES"
}
```

### F.3 Pipeline d'ingestion appliqué pendant la migration

```console
PUT _ingest/pipeline/bd516-products-enrich
{
  "description": "BD516 - enrichissement du catalogue pendant la migration v1 -> v2",
  "processors": [
    {
      "script": {
        "lang": "painless",
        "source": "double p = ctx.price; ctx.price_band = p < 30 ? 'eco' : (p < 100 ? 'standard' : 'premium');"
      }
    },
    {
      "set": { "field": "ingested_at", "value": "{{{_ingest.timestamp}}}" }
    }
  ]
}

# Toujours tester un pipeline avant de l'utiliser
POST _ingest/pipeline/bd516-products-enrich/_simulate
{
  "docs": [
    { "_source": { "sku": "TEST-1", "price": 24.99 } },
    { "_source": { "sku": "TEST-2", "price": 149.99 } }
  ]
}
```

### F.4 Reindex, puis bascule atomique de l'alias

```console
POST _reindex?wait_for_completion=true
{
  "source": { "index": "bd516-products-v1", "size": 500 },
  "dest":   { "index": "bd516-products-v2", "pipeline": "bd516-products-enrich" }
}

GET bd516-products-v2/_count

# Bascule atomique : l'application ne voit jamais d'alias vide
POST _aliases
{
  "actions": [
    { "remove": { "index": "bd516-products-v1", "alias": "bd516-products" } },
    { "add":    { "index": "bd516-products-v2", "alias": "bd516-products", "is_write_index": true } }
  ]
}

GET _alias/bd516-products
```

Résultat attendu : `"total": 500, "created": 500, "failures": []`, puis `count: 500` et l'alias pointant sur la v2. Rejouez maintenant les deux requêtes de F.1 :

```console
GET bd516-products/_search
{
  "query": { "term": { "sku": "RAN-0001" } },
  "_source": ["sku", "name", "price", "price_band"]
}

GET bd516-products/_count
{
  "query": { "match": { "description": "imperméables" } }
}
```

Le `term` trouve désormais le produit (`Lampe frontale Confort 900 Iroise`, `price_band: standard`) et la recherche « imperméables » renvoie **100** documents : singulier et pluriel partagent la même racine grâce au stemming français.

### F.5 Update By Query : ajouter un sous-champ et le peupler

Besoin : trier les produits par nom **sans tenir compte de la casse ni des accents**. On ajoute un sous-champ à un champ existant (autorisé) :

```console
PUT bd516-products-v2/_mapping
{
  "properties": {
    "name": {
      "type": "text",
      "analyzer": "bd516_francais",
      "fields": {
        "keyword": { "type": "keyword" },
        "tri":     { "type": "keyword", "normalizer": "bd516_tri" }
      }
    }
  }
}

# Les documents existants n'ont pas encore de valeur pour name.tri : 0 attendu
GET bd516-products/_search
{
  "size": 0,
  "aggs": { "avec_tri": { "value_count": { "field": "name.tri" } } }
}

# Réindexation "sur place" de tous les documents
POST bd516-products-v2/_update_by_query?refresh=true&conflicts=proceed

GET bd516-products/_search
{
  "size": 0,
  "aggs": { "avec_tri": { "value_count": { "field": "name.tri" } } }
}

GET bd516-products/_search
{
  "size": 5,
  "sort": [{ "name.tri": "asc" }],
  "_source": ["name"]
}
```

Résultat attendu : `value` passe de **0** à **500**.

Update By Query avec script (opération métier en masse) :

```console
POST bd516-products-v2/_update_by_query?refresh=true
{
  "query": { "term": { "category": "Ski" } },
  "script": {
    "lang": "painless",
    "source": "ctx._source.price = Math.round(ctx._source.price * params.coef * 100) / 100.0; ctx._source.tags.add('soldes');",
    "params": { "coef": 0.9 }
  }
}

GET bd516-products/_search
{
  "size": 3,
  "query": { "term": { "tags": "soldes" } },
  "_source": ["name", "price", "tags"]
}
```

---

## Partie G – Sharding et réplication

```console
PUT bd516-shards-demo
{
  "settings": { "number_of_shards": 3, "number_of_replicas": 1 }
}

GET _cat/shards/bd516-shards-demo?v&s=shard,prirep&h=index,shard,prirep,state,node
```

Résultat attendu : 6 shards (3 primaires `p`, 3 réplicas `r`) répartis sur **es01 et es02** uniquement (tier `data_content`), jamais deux copies d'un même shard sur le même nœud.

Demandons plus de réplicas que le cluster ne peut en héberger :

```console
PUT bd516-shards-demo/_settings
{
  "index.number_of_replicas": 2
}

GET _cluster/health/bd516-shards-demo?filter_path=status,unassigned_shards

# Pourquoi un shard n'est-il pas alloué ? (premier shard non alloué du cluster)
GET _cluster/allocation/explain?filter_path=index,shard,primary,current_state,allocate_explanation,node_allocation_decisions.node_name,node_allocation_decisions.deciders.explanation
```

Résultat attendu : statut **yellow**, 3 shards non alloués ; l'explication cite la règle *same shard* (une copie existe déjà sur es01/es02) et la préférence de tier `data_content` pour es03. Revenez à une configuration saine :

```console
PUT bd516-shards-demo/_settings
{
  "index.number_of_replicas": 1
}

DELETE bd516-shards-demo
```

| Repère de dimensionnement | Valeur indicative |
|---|---|
| Taille d'un shard | 10 à 50 Go (logs, séries temporelles : viser ~50 Go via rollover) |
| Documents par shard | < 200 millions |
| Réplicas | ≥ 1 en production (haute disponibilité, débit de lecture) |
| Nombre de shards primaires | fixé à la création ; `_split` / `_shrink` pour le modifier, sinon reindex |

---

## Partie H – Index Lifecycle Management (ILM)

Pour observer les phases en quelques minutes, on accélère la vérification ILM (10 min par défaut) et on utilise des seuils minuscules. **Ne jamais faire cela en production.**

```console
PUT _cluster/settings
{
  "persistent": { "indices.lifecycle.poll_interval": "10s" }
}

PUT _ilm/policy/bd516-ilm-demo
{
  "policy": {
    "_meta": { "description": "BD516 - demonstration accelérée des phases ILM" },
    "phases": {
      "hot": {
        "actions": {
          "rollover": { "max_docs": 10 },
          "set_priority": { "priority": 100 }
        }
      },
      "warm": {
        "min_age": "1m",
        "actions": {
          "allocate": { "number_of_replicas": 0 },
          "forcemerge": { "max_num_segments": 1 },
          "set_priority": { "priority": 50 }
        }
      },
      "cold": {
        "min_age": "3m",
        "actions": {
          "set_priority": { "priority": 0 }
        }
      },
      "delete": {
        "min_age": "6m",
        "actions": { "delete": {} }
      }
    }
  }
}

PUT _index_template/bd516-ilm-demo
{
  "index_patterns": ["bd516-ilm-demo*"],
  "data_stream": {},
  "priority": 300,
  "template": {
    "settings": {
      "index.lifecycle.name": "bd516-ilm-demo",
      "index.number_of_shards": 1,
      "index.number_of_replicas": 1,
      "index.refresh_interval": "1s"
    },
    "mappings": {
      "properties": {
        "@timestamp": { "type": "date" },
        "message":    { "type": "text" },
        "level":      { "type": "keyword" }
      }
    }
  }
}
```

> Phase **frozen** : elle repose sur les *searchable snapshots* (licence Enterprise) ; elle n'est pas activée ici. La phase **warm** passe les réplicas à 0 car ce cluster n'a qu'un seul nœud warm (es03).

Alimentez le data stream (un document par seconde pendant 40 s) :

```bash
for i in $(seq 1 40); do
  es POST /bd516-ilm-demo/_doc -d "{\"@timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)\",\"message\":\"evenement ${i}\",\"level\":\"info\"}" > /dev/null
  sleep 1
done
echo "Envoi termine"
```

Observez l'évolution (relancez toutes les 30 secondes pendant ~8 minutes) :

```console
GET _data_stream/bd516-ilm-demo?filter_path=data_streams.name,data_streams.generation,data_streams.indices.index_name

GET _cat/indices/bd516-ilm-demo?v&h=index,health,pri,rep,docs.count&s=index

GET _cat/shards/bd516-ilm-demo?v&h=index,prirep,state,node&s=index

GET bd516-ilm-demo/_ilm/explain?filter_path=indices.*.phase,indices.*.action,indices.*.step,indices.*.age
```

Déroulement attendu :

1. **hot** : après ~10 documents, *rollover* → nouvelle génération (`.ds-bd516-ilm-demo-AAAA.MM.JJ-000002`, puis `-000003`…) ;
2. **warm** (1 min après le rollover) : réplicas à 0, *forcemerge*, et **migration du shard vers es03** (tier `data_warm`) ;
3. **cold** (3 min) : l'index reste sur es03 (tier `data_cold`) ;
4. **delete** (6 min) : les anciennes générations disparaissent ; l'index d'écriture courant n'est jamais supprimé.

Nettoyage :

```console
DELETE _data_stream/bd516-ilm-demo
DELETE _index_template/bd516-ilm-demo
DELETE _ilm/policy/bd516-ilm-demo

PUT _cluster/settings
{
  "persistent": { "indices.lifecycle.poll_interval": null }
}
```

---

## Partie I – Snapshots et restauration

Les trois nœuds partagent le volume Docker `snapshots`, déclaré dans `path.repo` (`/usr/share/elasticsearch/snapshots`).

```console
PUT _snapshot/bd516_backup
{
  "type": "fs",
  "settings": { "location": "bd516", "compress": true }
}

# Chaque nœud doit pouvoir écrire dans le dépôt
POST _snapshot/bd516_backup/_verify

PUT _snapshot/bd516_backup/snap-catalogue-1?wait_for_completion=true
{
  "indices": "bd516-orders,bd516-products-v2",
  "include_global_state": false,
  "metadata": { "auteur": "stagiaire", "lab": "02" }
}

GET _cat/snapshots/bd516_backup?v
```

Résultat attendu : `_verify` liste es01, es02 et es03 ; le snapshot est `SUCCESS` avec 2 index.

### Incident simulé : suppression du catalogue

```console
DELETE bd516-products-v2

# L'alias a disparu avec l'index : erreur index_not_found_exception
GET bd516-products/_count

POST _snapshot/bd516_backup/snap-catalogue-1/_restore?wait_for_completion=true
{
  "indices": "bd516-products-v2",
  "include_aliases": true
}

GET bd516-products/_count
```

Résultat attendu : `count: 500` (index **et** alias restaurés).

### Restauration sous un autre nom (vérification d'une sauvegarde)

```console
POST _snapshot/bd516_backup/snap-catalogue-1/_restore?wait_for_completion=true
{
  "indices": "bd516-orders",
  "rename_pattern": "bd516-(.+)",
  "rename_replacement": "restaure-$1",
  "include_aliases": false
}

GET _cat/indices/restaure-*?v&h=index,health,docs.count

DELETE restaure-orders
```

### Snapshot Lifecycle Management (SLM)

```console
PUT _slm/policy/bd516-quotidien
{
  "schedule": "0 30 1 * * ?",
  "name": "<bd516-quotidien-{now/d}>",
  "repository": "bd516_backup",
  "config": {
    "indices": ["bd516-*"],
    "include_global_state": false
  },
  "retention": { "expire_after": "14d", "min_count": 3, "max_count": 14 }
}

# Exécution immédiate (sans attendre 01:30)
POST _slm/policy/bd516-quotidien/_execute

# Quelques secondes plus tard : last_success renseigné, prochaine exécution planifiée
GET _slm/policy/bd516-quotidien?filter_path=*.last_success,*.next_execution
GET _cat/snapshots/bd516_backup?v
```

> En production, le dépôt doit être **hors du cluster** (NFS partagé monté sur tous les nœuds, S3, Azure Blob, GCS) ; un snapshot est incrémental au niveau des segments.

---

## Nettoyage de fin de lab

```console
DELETE bd516-demo,bd516-strict,bd516-runtime

GET _cat/indices/bd516-*?v&h=index,health,docs.count&s=index
```

Index conservés pour la suite : `bd516-orders` (10 000) et `bd516-products-v2` (500, alias `bd516-products`). `bd516-products-v1` peut être supprimé ou gardé pour comparaison.

## Dépannage

| Symptôme | Cause / solution |
|---|---|
| `"errors": true` dans la réponse bulk | relancez sans `filter_path` sur le fichier concerné pour lire `items[].index.error` |
| `strict_dynamic_mapping_exception` pendant le bulk des commandes | le composant `bd516-orders@mappings` a été modifié ; recréez-le puis supprimez/rechargez l'index |
| Le rollover ILM ne se produit pas | `poll_interval` non modifié, ou moins de 10 documents ; consultez `_ilm/explain` (champ `step_info`) |
| `repository_verification_exception` | volume `snapshots` mal initialisé : relancez `docker compose up -d` (le conteneur setup corrige les droits) |
| `_reindex` signale des `failures` | champ absent du mapping strict de la v2 : lisez `failures[].cause.reason` |

## Récapitulatif

- [x] CRUD, versions et contrôle de concurrence optimiste
- [x] Mappings dynamique / strict / runtime ; analyzers `standard`, `french` et personnalisé
- [x] Composants + modèle d'index `bd516-orders`, 10 000 commandes chargées en bulk
- [x] Migration `bd516-products-v1 → v2` : reindex + pipeline + bascule d'alias + update by query
- [x] Allocation des shards expliquée, politique ILM hot → warm → cold → delete observée
- [x] Dépôt de snapshots, sauvegarde, restauration et politique SLM
