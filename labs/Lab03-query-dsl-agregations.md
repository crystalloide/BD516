## BD516– Lab 03 : recherche avec le Query DSL et agrégations

> **Chapitre 3 – Recherche avec Query DSL et agrégations**
> Prérequis : Lab 02 terminé (`bd516-orders` : 10 000 commandes ; alias `bd516-products` → `bd516-products-v2` : 500 produits, soldes appliquées sur la catégorie Ski) – Durée : 2 h 30
> Toutes les requêtes se saisissent dans **Kibana → Dev Tools → Console**.

## Objectifs

| Travaux pratiques du programme | Partie |
|---|---|
| Présentation du Query DSL, contexte *query* vs *filter* | A |
| Requêtes full-text : `match`, `multi_match`, `match_phrase` | B |
| Requêtes term-level : `term`, `terms`, `range`, `exists`, `prefix`, `wildcard` | C |
| **Écrire des requêtes full-text et term-level combinées dans une requête booléenne** | D, E |
| Pertinence et scoring : BM25, `function_score`, `boost` | F |
| Tri, pagination (`from`/`size`, `search_after`) et *highlighting* | G |
| **Construire des agrégations bucket et metric pour répondre à des questions analytiques** | H, I, K |
| Agrégations pipeline : `derivative`, `cumulative_sum`, `bucket_script` | J |
| *Bonus : recherche vectorielle kNN, aperçu ES\|QL* | L |

Les valeurs « **attendu** » proviennent du générateur déterministe du Lab 02 (fichier `~/BD516/data/expected-results.txt`).

---

## Partie A – Anatomie d'une recherche

```console
GET bd516-orders/_search
{
  "track_total_hits": true,
  "size": 2,
  "_source": ["order_id", "order_date", "status", "total_amount", "customer.city"],
  "query": {
    "bool": {
      "filter": [
        { "term": { "status": "cancelled" } }
      ]
    }
  }
}
```

Dans la réponse : `took` (ms), `_shards`, `hits.total.value` (**885** attendu ; `track_total_hits: true` force le comptage exact au-delà de 10 000), `hits.max_score` (**0** : en contexte *filter*, pas de calcul de score), puis les documents.

| Contexte | Question posée | Score | Cache |
|---|---|---|---|
| **query** (`must`, `should`) | « dans quelle mesure le document correspond-il ? » | oui (BM25) | non |
| **filter** (`filter`, `must_not`) | « le document correspond-il, oui ou non ? » | non | oui (cache des filtres) |

---

## Partie B – Requêtes full-text

```console
# match : le texte de la requête est analysé (analyzer french du champ comment)
GET bd516-orders/_count
{
  "query": { "match": { "comment": "livraison" } }
}

# Par défaut les termes sont combinés en OR ; operator "and" exige tous les termes
GET bd516-orders/_count
{
  "query": {
    "match": { "comment": { "query": "livraison rapide", "operator": "and" } }
  }
}

# match_phrase : termes adjacents et dans l'ordre
GET bd516-orders/_count
{
  "query": { "match_phrase": { "comment": "colis endommagé" } }
}

# slop : nombre de positions de décalage tolérées ("Colis arrivé endommagé")
GET bd516-orders/_count
{
  "query": { "match_phrase": { "comment": { "query": "colis endommagé", "slop": 1 } } }
}
```

Attendu : **813**, **271**, **0**, puis **72**.

```console
# Tolérance aux fautes de frappe (distance de Levenshtein)
GET bd516-products/_search
{
  "size": 3,
  "_source": ["name"],
  "query": {
    "match": { "name": { "query": "chausures", "fuzziness": "AUTO" } }
  }
}

# multi_match : plusieurs champs, avec un poids (^) plus fort sur le nom
GET bd516-products/_search
{
  "size": 5,
  "_source": ["name", "category"],
  "query": {
    "multi_match": {
      "query": "sac randonnée léger",
      "fields": ["name^3", "description", "category"],
      "type": "best_fields",
      "tie_breaker": 0.3
    }
  }
}

# minimum_should_match : au moins 2 des 3 termes
GET bd516-products/_search
{
  "size": 5,
  "_source": ["name"],
  "query": {
    "match": {
      "description": { "query": "imperméable respirante léger", "minimum_should_match": 2 }
    }
  }
}
```

Attendu : la requête floue trouve les **41** produits « Chaussures … » (`hits.total.value`) ; la `multi_match` classe d'abord des sacs à dos de la catégorie Randonnée.

---

## Partie C – Requêtes term-level (valeurs exactes, non analysées)

```console
# term sur un keyword
GET bd516-orders/_count
{
  "query": { "term": { "status": "cancelled" } }
}

# Piège classique : term sur un champ text (indexé en minuscules et découpé) -> 0
GET bd516-orders/_count
{
  "query": { "term": { "customer.name": "Camille Martin" } }
}

# ... il faut utiliser le sous-champ keyword
GET bd516-orders/_count
{
  "query": { "term": { "customer.name.keyword": "Camille Martin" } }
}

# terms : plusieurs valeurs (OU)
GET bd516-orders/_count
{
  "query": { "terms": { "channel": ["store", "marketplace"] } }
}

# range numérique
GET bd516-orders/_count
{
  "query": { "range": { "total_amount": { "gte": 1000 } } }
}

# range sur des dates avec date math (arrondi au jour, fuseau de Paris)
GET bd516-orders/_count
{
  "query": {
    "range": { "order_date": { "gte": "now-30d/d", "lt": "now/d", "time_zone": "Europe/Paris" } }
  }
}

# exists / absence de champ
GET bd516-orders/_count
{
  "query": { "exists": { "field": "comment" } }
}

GET bd516-products/_count
{
  "query": { "bool": { "must_not": { "exists": { "field": "description" } } } }
}

# prefix et wildcard (coûteux sur de gros volumes : à éviter en tête de motif)
GET bd516-products/_count
{
  "query": { "prefix": { "sku": "SKI-" } }
}

GET bd516-products/_search
{
  "size": 3,
  "_source": ["name"],
  "query": { "wildcard": { "name.keyword": { "value": "tente*", "case_insensitive": true } } }
}
```

Attendu : **885** ; **0** puis **3** ; **1 439** ; **535** ; (variable selon la date) ; **2 589** ; **13** ; **62** ; **9** produits « Tente 2 places ».

---

## Partie D – Combiner full-text et term-level dans une requête `bool`

Besoin métier : *« des produits imperméables de randonnée ou de ski, à 150 € maximum, en stock ; mettre en avant les mieux notés »*.

```console
GET bd516-products/_search
{
  "track_total_hits": true,
  "size": 5,
  "_source": ["name", "category", "price", "stock", "rating"],
  "query": {
    "bool": {
      "must": [
        { "match": { "description": "imperméable" } }
      ],
      "filter": [
        { "terms": { "category": ["Randonnée", "Ski"] } },
        { "range": { "price": { "lte": 150 } } }
      ],
      "must_not": [
        { "term": { "stock": 0 } }
      ],
      "should": [
        { "range": { "rating": { "gte": 4.5, "boost": 2 } } },
        { "match": { "name": { "query": "veste", "boost": 1.5 } } }
      ]
    }
  }
}
```

Attendu : **21** produits. `must` et `should` influencent le score ; `filter` et `must_not` filtrent sans scorer. Comme la clause `must` existe, les `should` sont **optionnelles** : elles ne font que remonter les documents qui les satisfont.

> Exercice : passez la clause `match` de `must` à `filter` et observez `max_score` ; ajoutez `"minimum_should_match": 1` dans le `bool` et comparez le nombre de résultats.

---

## Partie E – Requêtes sur des objets `nested`

Commandes contenant **une même ligne** de la catégorie Ski commandée en au moins 2 exemplaires :

```console
GET bd516-orders/_search
{
  "track_total_hits": true,
  "size": 1,
  "_source": ["order_id", "items.category", "items.quantity"],
  "query": {
    "nested": {
      "path": "items",
      "query": {
        "bool": {
          "filter": [
            { "term": { "items.category": "Ski" } },
            { "range": { "items.quantity": { "gte": 2 } } }
          ]
        }
      },
      "inner_hits": { "size": 1, "_source": ["items.product_name", "items.quantity"] }
    }
  }
}

# Sans "nested", les sous-documents ne sont pas visibles : 0 résultat
GET bd516-orders/_count
{
  "query": { "term": { "items.category": "Ski" } }
}
```

Attendu : **414** commandes. Avec un simple type `object`, les tableaux seraient « aplatis » et la même requête renverrait **685** commandes (un article Ski *et* un autre article en quantité ≥ 2) : c'est la raison d'être du type `nested`.

---

## Partie F – Pertinence et scoring

```console
# Détail du calcul BM25 (tf, idf, normalisation par la longueur du champ)
GET bd516-products/_explain/RAN-0001
{
  "query": { "match": { "description": "imperméable" } }
}

# function_score : combiner pertinence, note client et nouveauté
GET bd516-products/_search
{
  "size": 5,
  "_source": ["name", "rating", "created_at"],
  "query": {
    "function_score": {
      "query": { "match": { "name": "veste" } },
      "functions": [
        { "field_value_factor": { "field": "rating", "modifier": "sqrt", "missing": 1 } },
        { "gauss": { "created_at": { "origin": "now", "scale": "180d", "decay": 0.5 } } }
      ],
      "score_mode": "multiply",
      "boost_mode": "multiply"
    }
  }
}
```

Relancez la seconde requête sans le bloc `functions` (ou avec `"boost_mode": "replace"`) et comparez le classement. Le modèle **BM25** (paramètres `k1 = 1.2`, `b = 0.75` par défaut) récompense un terme fréquent dans le document (avec saturation), rare dans l'index, et dans un champ court.

---

## Partie G – Tri, pagination, highlighting

```console
# Tri multi-critères (le tri sur un champ remplace le tri par score)
GET bd516-orders/_search
{
  "size": 3,
  "_source": ["order_id", "order_date", "total_amount"],
  "sort": [
    { "order_date": "desc" },
    { "order_id": "asc" }
  ]
}
```

**Pagination `from` / `size`** : simple, mais limitée à 10 000 résultats (`index.max_result_window`) et coûteuse en profondeur.

```console
GET bd516-orders/_search
{
  "from": 3,
  "size": 3,
  "_source": ["order_id", "order_date"],
  "sort": [{ "order_date": "desc" }, { "order_id": "asc" }]
}
```

**Pagination `search_after`** : on repart des valeurs de tri du dernier résultat de la page précédente. Copiez le tableau `sort` du **3e** résultat de la première requête de cette partie (par exemple `[1764028800000, "CMD-004321"]`) :

```console
GET bd516-orders/_search
{
  "size": 3,
  "_source": ["order_id", "order_date"],
  "sort": [{ "order_date": "desc" }, { "order_id": "asc" }],
  "search_after": [1764028800000, "CMD-004321"]
}
```

Vérifiez que les résultats sont identiques à ceux de la requête `from: 3`. Pour un parcours cohérent pendant des écritures, on ajoute un **point in time** :

```console
POST bd516-orders/_pit?keep_alive=2m
```

```console
# Remplacez <ID_PIT> par l'identifiant obtenu ; pas de nom d'index dans l'URL avec un PIT
GET _search
{
  "size": 3,
  "_source": ["order_id"],
  "pit": { "id": "<ID_PIT>", "keep_alive": "2m" },
  "sort": [{ "order_date": "desc" }]
}
```

**Highlighting** :

```console
GET bd516-products/_search
{
  "size": 3,
  "_source": ["name"],
  "query": { "match": { "description": "imperméable respirante" } },
  "highlight": {
    "pre_tags": ["<mark>"],
    "post_tags": ["</mark>"],
    "fields": { "description": {} }
  }
}
```

Attendu : des extraits `"... Membrane <mark>imperméable</mark> et <mark>respirante</mark> ..."`.

---

## Partie H – Agrégations metric

```console
GET bd516-orders/_search
{
  "size": 0,
  "aggs": {
    "montants":        { "stats": { "field": "total_amount" } },
    "panier_moyen":    { "avg": { "field": "total_amount" } },
    "ca_total":        { "sum": { "field": "total_amount" } },
    "clients_uniques": { "cardinality": { "field": "customer.id", "precision_threshold": 3000 } },
    "quantiles":       { "percentiles": { "field": "total_amount", "percents": [50, 90, 99] } }
  }
}
```

Attendu : `count` **10 000**, `min` **8,48**, `max` **4 947,94**, `avg` ≈ **300,06**, `sum` ≈ **3 000 618,93**, `clients_uniques` ≈ **1 765** (`cardinality` est approximative au-delà du seuil de précision ; `percentiles` aussi).

---

## Partie I – Agrégations bucket

```console
# terms : répartition par statut
GET bd516-orders/_search
{
  "size": 0,
  "aggs": {
    "par_statut": { "terms": { "field": "status", "size": 10 } }
  }
}

# date_histogram : commandes et chiffre d'affaires par mois (fuseau de Paris)
GET bd516-orders/_search
{
  "size": 0,
  "aggs": {
    "par_mois": {
      "date_histogram": {
        "field": "order_date",
        "calendar_interval": "month",
        "time_zone": "Europe/Paris",
        "format": "yyyy-MM",
        "min_doc_count": 0
      },
      "aggs": { "ca": { "sum": { "field": "total_amount" } } }
    }
  }
}

# range : tranches de montant
GET bd516-orders/_search
{
  "size": 0,
  "aggs": {
    "tranches": {
      "range": {
        "field": "total_amount",
        "ranges": [
          { "key": "moins_de_50", "to": 50 },
          { "key": "50_200", "from": 50, "to": 200 },
          { "key": "200_500", "from": 200, "to": 500 },
          { "key": "plus_de_500", "from": 500 }
        ]
      }
    }
  }
}

# filters : seaux définis par des requêtes
GET bd516-orders/_search
{
  "size": 0,
  "aggs": {
    "segments_qualite": {
      "filters": {
        "filters": {
          "avec_commentaire": { "exists": { "field": "comment" } },
          "note_basse":       { "range": { "rating": { "lte": 2 } } }
        }
      }
    }
  }
}

# Champ runtime du Lab 02 : commandes par jour de la semaine (1 = lundi)
GET bd516-orders/_search
{
  "size": 0,
  "aggs": {
    "jour_semaine": { "terms": { "field": "order_weekday", "size": 7, "order": { "_key": "asc" } } }
  }
}
```

Attendu : statuts `delivered` **6 110**, `shipped` **1 247**, `cancelled` **885**, `paid` **829**, `returned` **515**, `new` **414** ; 12 ou 13 mois ; tranches **1 440** / **4 248** / **2 759** / **1 553** ; filtres **2 589** / **473**.

---

## Partie J – Agrégations pipeline

```console
GET bd516-orders/_search
{
  "size": 0,
  "aggs": {
    "par_mois": {
      "date_histogram": {
        "field": "order_date",
        "calendar_interval": "month",
        "time_zone": "Europe/Paris",
        "format": "yyyy-MM"
      },
      "aggs": {
        "ca":        { "sum": { "field": "total_amount" } },
        "evolution": { "derivative": { "buckets_path": "ca" } },
        "ca_cumule": { "cumulative_sum": { "buckets_path": "ca" } },
        "panier_moyen": {
          "bucket_script": {
            "buckets_path": { "ca": "ca", "nb": "_count" },
            "script": "params.ca / params.nb"
          }
        }
      }
    }
  }
}
```

`evolution` (différence avec le mois précédent) est absente du premier seau ; `ca_cumule` du dernier mois égale le CA total (≈ 3 000 618,93).

```console
# bucket_sort : ne garder que les 3 mois au plus fort chiffre d'affaires
GET bd516-orders/_search
{
  "size": 0,
  "aggs": {
    "par_mois": {
      "date_histogram": { "field": "order_date", "calendar_interval": "month", "format": "yyyy-MM" },
      "aggs": {
        "ca": { "sum": { "field": "total_amount" } },
        "top3": { "bucket_sort": { "sort": [{ "ca": { "order": "desc" } }], "size": 3 } }
      }
    }
  }
}
```

---

## Partie K – Questions analytiques (exercices)

Écrivez chaque requête **avant** de lire la solution.

**Q1. Chiffre d'affaires, nombre de commandes et panier moyen par canal de vente.**

<details><summary>Solution</summary>

```console
GET bd516-orders/_search
{
  "size": 0,
  "aggs": {
    "par_canal": {
      "terms": { "field": "channel", "order": { "ca": "desc" } },
      "aggs": {
        "ca": { "sum": { "field": "total_amount" } },
        "panier_moyen": { "avg": { "field": "total_amount" } }
      }
    }
  }
}
```

Attendu : web 5 542 / ≈ 1 660 894,51 / ≈ 299,69 ; mobile 3 019 / ≈ 905 191,12 ; store 963 / ≈ 291 881,99 ; marketplace 476 / ≈ 142 651,31.
</details>

**Q2. Les 5 villes qui génèrent le plus de chiffre d'affaires.**

<details><summary>Solution</summary>

```console
GET bd516-orders/_search
{
  "size": 0,
  "aggs": {
    "top_villes": {
      "terms": { "field": "customer.city", "size": 5, "order": { "ca": "desc" } },
      "aggs": { "ca": { "sum": { "field": "total_amount" } } }
    }
  }
}
```

Attendu : Paris (≈ 529 446), Bruxelles (≈ 221 734), Nantes (≈ 206 081), Lille (≈ 205 271), Lyon (≈ 189 254).
</details>

**Q3. Taux d'annulation (en %) par canal.**

<details><summary>Solution</summary>

```console
GET bd516-orders/_search
{
  "size": 0,
  "aggs": {
    "par_canal": {
      "terms": { "field": "channel" },
      "aggs": {
        "annulees": { "filter": { "term": { "status": "cancelled" } } },
        "taux_annulation": {
          "bucket_script": {
            "buckets_path": { "annulees": "annulees>_count", "total": "_count" },
            "script": "Math.round(params.annulees * 10000.0 / params.total) / 100.0"
          }
        }
      }
    }
  }
}
```

Attendu : web 9,11 % ; mobile 8,51 % ; store 9,03 % ; marketplace 7,56 %.
</details>

**Q4. Chiffre d'affaires des articles par catégorie de produit** (lignes de commande `nested`).

<details><summary>Solution</summary>

```console
GET bd516-orders/_search
{
  "size": 0,
  "aggs": {
    "lignes": {
      "nested": { "path": "items" },
      "aggs": {
        "par_categorie": {
          "terms": { "field": "items.category", "size": 10, "order": { "ca_articles": "desc" } },
          "aggs": {
            "ca_articles": {
              "sum": { "script": { "source": "doc['items.quantity'].value * doc['items.unit_price'].value" } }
            },
            "quantite": { "sum": { "field": "items.quantity" } }
          }
        }
      }
    }
  }
}
```

Attendu : Vélo ≈ 895 500,80 ; Ski ≈ 491 685,71 ; Fitness ≈ 310 783,32 ; Escalade ≈ 294 749,52 ; Camping ≈ 283 645,87 ; Randonnée ≈ 257 712,04 ; Running ≈ 241 048,10 ; Natation ≈ 210 189,64 (voir `expected-results.txt`).
</details>

**Q5. Nombre de clients distincts par segment.**

<details><summary>Solution</summary>

```console
GET bd516-orders/_search
{
  "size": 0,
  "aggs": {
    "par_segment": {
      "terms": { "field": "customer.segment" },
      "aggs": { "clients": { "cardinality": { "field": "customer.id" } } }
    }
  }
}
```

Attendu : particulier ≈ 1 427 ; pro ≈ 258 ; vip ≈ 80.
</details>

**Q6. Commandes livrées à moins de 50 km de Lyon, et leur montant moyen.**

<details><summary>Solution</summary>

```console
GET bd516-orders/_search
{
  "size": 0,
  "query": {
    "bool": {
      "filter": {
        "geo_distance": {
          "distance": "50km",
          "customer.location": { "lat": 45.7640, "lon": 4.8357 }
        }
      }
    }
  },
  "aggs": {
    "montant_moyen": { "avg": { "field": "total_amount" } },
    "villes": { "terms": { "field": "customer.city" } }
  }
}
```

Attendu : **562** commandes, toutes à Lyon (Grenoble et Annecy sont à plus de 90 km).
</details>

---

## Partie L – Bonus

### L.1 Recherche vectorielle (kNN) sur le champ `dense_vector`

Les produits portent un petit vecteur de 4 dimensions (`embedding`) proche du « profil » de leur catégorie. Recherchons les voisins du profil *Randonnée* :

```console
GET bd516-products/_search
{
  "size": 5,
  "_source": ["name", "category"],
  "knn": {
    "field": "embedding",
    "query_vector": [0.9649, 0.2144, 0.1072, 0.1072],
    "k": 5,
    "num_candidates": 50
  }
}
```

Attendu : 5 produits de la catégorie **Randonnée**. En production, les vecteurs (384 à 4 096 dimensions) sont produits par un modèle d'*embedding* (inférence Elasticsearch ou externe).

### L.2 Aperçu ES|QL (détaillé au Lab 05)

```console
POST _query?format=txt
{
  "query": "FROM bd516-orders | STATS commandes = COUNT(*), ca = ROUND(SUM(total_amount), 2) BY channel | SORT ca DESC"
}
```

---

## Dépannage

| Symptôme | Cause / solution |
|---|---|
| `0` résultat sur un `term` | champ `text` analysé : utiliser le sous-champ `.keyword` ou un `match` |
| `Text fields are not optimised for operations that require per-document field data` | agrégation/tri sur un champ `text` : utiliser `.keyword` |
| Les chiffres de la partie D diffèrent | les soldes Ski du Lab 02 (partie F.5) n'ont pas été appliquées : les prix Ski sont plus élevés |
| `search_after` renvoie la première page | le tableau doit contenir **toutes** les valeurs de tri du dernier résultat, dans l'ordre |
| `No mapping found for [order_weekday]` | le composant `bd516-orders@mappings` du Lab 02 n'a pas été créé avant le chargement |

## Récapitulatif

- [x] Requêtes full-text (`match`, `multi_match`, `match_phrase`, fuzziness) et term-level
- [x] Requête `bool` combinant `must`, `filter`, `must_not`, `should` ; requête `nested`
- [x] Scoring BM25 expliqué, `function_score`, `boost`
- [x] Tri, `from`/`size`, `search_after`, point in time, highlighting
- [x] Agrégations metric, bucket (`terms`, `date_histogram`, `range`, `filters`, `nested`) et pipeline
- [x] Six questions métier résolues par agrégations
