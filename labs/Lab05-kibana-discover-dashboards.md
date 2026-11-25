# BD516 – Lab 05 : Kibana, exploration de données et tableaux de bord

> **Chapitre 5 – Kibana : exploration de données et Discover**
> Prérequis : Lab 04 terminé (data streams `logs-bd516.app-default` ≈ 50 000 et `logs-bd516.web-default` ≈ 40 000 documents) ; Lab 02 pour les commandes – Durée : 2 h
> Libellés de l'interface : Kibana 9.5 en anglais (ils peuvent légèrement varier d'une version 9.x à l'autre).

## Objectifs

| Travaux pratiques du programme | Étapes |
|---|---|
| Organisation de l'interface : *Spaces*, applications | 1 |
| **Configurer un Data View sur l'index alimenté par Logstash** | 2 |
| **Explorer les données avec Discover et KQL** (et la syntaxe Lucene) | 3 |
| Filtres, plages temporelles et sauvegarde de recherches | 3 |
| **Utiliser ES\|QL dans Discover pour une requête pipe-based** | 4 |
| **Créer plusieurs visualisations avec Lens** (répartition, séries temporelles, top N) | 5 |
| **Composer un dashboard cohérent avec filtres globaux et plages temporelles** | 6 |
| **Partager le dashboard et exporter une visualisation** | 7 |

---

## Étape 0 – Des données « en direct » (facultatif mais conseillé)

Dans un **second terminal**, laissez tourner le générateur pendant le lab (1 événement / 2 s pendant 2 heures) :

```bash
python3 ~/BD516/data/generators/gen_logs.py follow --rate 0.5 --duration 7200
```

---

## Étape 1 – Créer l'espace de travail « BD516 »

Un **Space** isole des objets Kibana (data views, recherches, visualisations, dashboards) et peut restreindre les applications visibles.

```bash
source ~/BD516/scripts/bd516-env.sh
kb POST /api/spaces/space -d '{
  "id": "bd516",
  "name": "BD516 - Formation Elastic",
  "description": "Espace de travail de la formation BD516",
  "initials": "BD",
  "color": "#0B64DD",
  "solution": "classic"
}'
kb GET /api/spaces/space | jq -r '.[] | "\(.id) -> \(.name)"'
```

Résultat attendu : la définition de l'espace, puis `default -> Default` et `bd516 -> BD516 - Formation Elastic`.

Dans Kibana, cliquez sur le sélecteur d'espace (en haut à gauche) → **BD516 - Formation Elastic**. L'URL devient `https://localhost:5601/s/bd516/app/...`. Tout ce lab se déroule dans cet espace.

> Interface : ☰ → **Stack Management → Kibana → Spaces** permet la même opération (*Create space*, *Solution view : Classic*).

---

## Étape 2 – Data views

### 2.1 Data view des logs applicatifs (interface)

1. ☰ → **Stack Management → Kibana → Data Views** → **Create data view**.
2. Renseignez :
   - *Name* : `BD516 - Logs applicatifs (shop-api)`
   - *Index pattern* : `logs-bd516.app-*` (le panneau de droite doit lister `logs-bd516.app-default`)
   - *Timestamp field* : `@timestamp`
   - **Show advanced settings** → *Custom data view ID* : `bd516-logs-app`
3. **Save data view to Kibana**.
4. Sur la page du data view : recherchez le champ `http.response.body.bytes` → crayon (*Edit*) → *Set format* : **Bytes** → **Save**. Faites de même pour `shop.duration_ms` : *Set custom label* : `Durée (ms)`.
5. **Add field** (champ *runtime* calculé par Kibana, sans réindexation) :
   - *Name* : `endpoint` ; *Type* : **Keyword** ; *Set value* :

```painless
if (doc['url.path'].size() == 0) { return; }
String p = doc['url.path'].value;
if (p.startsWith('/api/v1/products/')) { emit('/api/v1/products/{sku}'); }
else if (p.startsWith('/api/v1/orders/')) { emit('/api/v1/orders/{id}'); }
else { emit(p); }
```

   → **Save**. Le champ regroupe les URL contenant un identifiant (`/api/v1/products/RAN-0012` → `/api/v1/products/{sku}`), indispensable pour un « top N » lisible.

### 2.2 Data views des logs web et des commandes (API)

```bash
kb POST /s/bd516/api/data_views/data_view -d '{
  "override": true,
  "data_view": {
    "id": "bd516-logs-web",
    "name": "BD516 - Logs web (nginx)",
    "title": "logs-bd516.web-*",
    "timeFieldName": "@timestamp",
    "fieldFormats": { "http.response.body.bytes": { "id": "bytes" } }
  }
}' | jq '.data_view | {id, name, title}'

kb POST /s/bd516/api/data_views/data_view -d '{
  "override": true,
  "data_view": {
    "id": "bd516-orders",
    "name": "BD516 - Commandes",
    "title": "bd516-orders",
    "timeFieldName": "order_date",
    "fieldFormats": { "total_amount": { "id": "number", "params": { "pattern": "0,0.00" } } },
    "fieldAttrs": { "total_amount": { "customLabel": "Montant (EUR)" } }
  }
}' | jq '.data_view | {id, name, title}'

kb GET /s/bd516/api/data_views | jq -r '.data_view[] | "\(.id)  \(.title)"'
```

Résultat attendu : les trois data views `bd516-logs-app`, `bd516-logs-web` et `bd516-orders`.

---

## Étape 3 – Discover, KQL et Lucene

Ouvrez ☰ → **Analytics → Discover**, data view **BD516 - Logs applicatifs (shop-api)**, plage **Last 7 days**.

### 3.1 Prise en main

1. **Histogramme** : cliquez-glissez sur le pic d'hier 14:00–15:30 pour zoomer, puis revenez avec le bouton ← du navigateur ou le sélecteur de temps.
2. **Liste des champs** : cliquez `log.level` → *Visualize* affiche directement la répartition ; le **+** ajoute une colonne. Ajoutez `log.level`, `endpoint`, `http.response.status_code`, `shop.duration_ms`, `source.geo.country_name`.
3. **Filtre en un clic** : dans un document, survolez la valeur `ERROR` du champ `log.level` → loupe ⊕ (*Filter for value*). Une *pastille* de filtre apparaît sous la barre de requête : épinglez-la (*Pin across all apps*), désactivez-la, inversez-la (*Exclude results*).
4. **Plage temporelle** : sélecteur → *Commonly used* : *Last 24 hours* ; puis plage absolue `hier 13:30` → `hier 16:00` ; activez l'**actualisation automatique** (*Refresh every* 10 s) si le générateur tourne.

### 3.2 KQL (Kibana Query Language)

Supprimez les filtres, revenez à **Last 7 days**, puis saisissez successivement dans la barre de requête :

| Requête KQL | Intention |
|---|---|
| `log.level : "ERROR"` | égalité |
| `log.level : ("WARN" or "ERROR")` | liste de valeurs |
| `http.response.status_code >= 500` | intervalle numérique |
| `url.path : "/api/v1/payments" and http.response.status_code >= 500` | combinaison |
| `url.path : /api/v1/products*` | joker (champ keyword) |
| `not source.geo.country_iso_code : "FR" and not tags : "internal_ip"` | négation |
| `shop.duration_ms > 1000 and service.name : "shop-api"` | requêtes lentes |
| `error.type : *Timeout*` | joker en tête (coûteux) |
| `user.id : *` | existence du champ (utilisateurs authentifiés) |
| `message : "paiement indisponible"` | phrase dans un champ texte |

Attendu : la requête `url.path : "/api/v1/payments" and http.response.status_code >= 500` concentre ses résultats sur la fenêtre d'incident d'hier.

### 3.3 Syntaxe Lucene

Menu de la barre de requête (icône à gauche du champ de saisie) → **Language : Lucene** (ou désactivez *Kibana Query Language*) :

| Requête Lucene | Équivalent / particularité |
|---|---|
| `http.response.status_code:[500 TO 599] AND NOT url.path:"/api/v1/health"` | intervalle inclusif `[ ]` |
| `user.id:/u1[0-9]{3}/` | **expression régulière** (absente de KQL) |
| `_exists_:error.type` | existence de champ |
| `log.level:(WARN OR ERROR) AND shop.duration_ms:>1000` | opérateurs en majuscules |

| | KQL | Lucene |
|---|---|---|
| Opérateurs | `and`, `or`, `not` (insensibles à la casse) | `AND`, `OR`, `NOT`, `+`, `-` |
| Champs imbriqués `nested` | oui : `items:{ category : "Ski" and quantity >= 2 }` | non |
| Regex, fuzzy (`~`), boost (`^`) | non | oui |
| Auto-complétion des champs et valeurs | oui | non |

Revenez en **KQL**.

### 3.4 Sauvegarder une recherche (session Discover)

1. Requête KQL : `http.response.status_code >= 500` ; colonnes `log.level`, `endpoint`, `http.response.status_code`, `error.type`, `message` ; tri `@timestamp` décroissant.
2. Barre d'application → **Save** → *Title* : `BD516 - Erreurs 5xx` → **Save**.
3. Vérifiez qu'elle apparaît dans **Open**.

### 3.5 Nested en KQL (data view des commandes)

Data view **BD516 - Commandes**, plage **Last 1 year** :

```text
items:{ category : "Ski" and quantity >= 2 }
```

Attendu : **414** commandes (même résultat que la requête `nested` du Lab 03).

---

## Étape 4 – ES|QL dans Discover

Dans Discover, cliquez **Query in ES|QL** (libellé **Try ES|QL** sur les versions précédentes, ou choix *ES|QL* dans le menu de langue). L'éditeur accepte une requête *pipe-based* ; la plage temporelle de Kibana s'applique sur `@timestamp`. Exécutez avec **Ctrl+Entrée**.

### 4.1 Filtrer, agréger, trier

```esql
FROM logs-bd516.app-*
| WHERE http.response.status_code >= 500
| STATS erreurs = COUNT(*) BY url.path
| SORT erreurs DESC
| LIMIT 10
```

### 4.2 Calculer des colonnes avec EVAL (regroupement par endpoint)

```esql
FROM logs-bd516.app-*
| WHERE url.path IS NOT NULL
| EVAL endpoint = CASE(
    STARTS_WITH(url.path, "/api/v1/products/"), "/api/v1/products/{sku}",
    STARTS_WITH(url.path, "/api/v1/orders/"), "/api/v1/orders/{id}",
    url.path)
| STATS requetes = COUNT(*), p95_ms = PERCENTILE(shop.duration_ms, 95), moyenne_ms = ROUND(AVG(shop.duration_ms), 1) BY endpoint
| SORT p95_ms DESC
```

Attendu : `/api/v1/payments` en tête (latence de la passerelle de paiement), `/api/v1/health` en dernier.

### 4.3 Série temporelle (Discover affiche automatiquement le graphique)

```esql
FROM logs-bd516.app-*
| WHERE http.response.status_code >= 500
| STATS erreurs_5xx = COUNT(*) BY heure = BUCKET(@timestamp, 1 hour)
| SORT heure
```

Le pic d'hier 14:00–15:30 est nettement visible. Conservez cette requête : elle pourra alimenter un panneau ES|QL du dashboard (étape 6, « Pour aller plus loin »).

### 4.4 Données web : navigateurs et pays

```esql
FROM logs-bd516.web-*
| WHERE user_agent.name IS NOT NULL
| STATS requetes = COUNT(*), pays = COUNT_DISTINCT(source.geo.country_iso_code) BY user_agent.name
| SORT requetes DESC
| LIMIT 8
```

### 4.5 Bonus : LOOKUP JOIN avec un référentiel

Créez un petit index de référence en mode `lookup` (**Dev Tools → Console**) :

```console
PUT bd516-http-status
{
  "settings": { "index.mode": "lookup" },
  "mappings": {
    "properties": {
      "http.response.status_code": { "type": "long" },
      "categorie": { "type": "keyword" },
      "libelle":   { "type": "keyword" }
    }
  }
}

POST bd516-http-status/_bulk?refresh=true
{ "index": {} }
{ "http.response.status_code": 200, "categorie": "succès", "libelle": "OK" }
{ "index": {} }
{ "http.response.status_code": 302, "categorie": "redirection", "libelle": "Found" }
{ "index": {} }
{ "http.response.status_code": 304, "categorie": "redirection", "libelle": "Not Modified" }
{ "index": {} }
{ "http.response.status_code": 404, "categorie": "erreur client", "libelle": "Not Found" }
{ "index": {} }
{ "http.response.status_code": 500, "categorie": "erreur serveur", "libelle": "Internal Server Error" }
{ "index": {} }
{ "http.response.status_code": 502, "categorie": "erreur serveur", "libelle": "Bad Gateway" }
{ "index": {} }
{ "http.response.status_code": 503, "categorie": "erreur serveur", "libelle": "Service Unavailable" }
```

Puis, dans Discover (ES|QL) :

```esql
FROM logs-bd516.web-*
| LOOKUP JOIN bd516-http-status ON http.response.status_code
| STATS requetes = COUNT(*) BY categorie, libelle
| SORT requetes DESC
```

---

## Étape 5 – Visualisations Lens

Méthode générale : ☰ → **Analytics → Visualize Library** → **Create visualization** → **Lens**. Dans l'éditeur : data view en haut à gauche, liste des champs à gauche, **espace de travail** au centre (déposez-y un champ), **type de graphique** au-dessus de l'espace de travail, **panneau des dimensions** à droite. Vérifiez la plage **Last 7 days**. Terminez chaque visualisation par **Save** → *Title* → *Add to dashboard : None* → **Save and add to library**.

### V1 – Répartition : évènements par niveau de log (donut)

1. Data view `BD516 - Logs applicatifs (shop-api)`.
2. Type de graphique : **Donut**.
3. *Slice by* → **Top values** de `log.level` (taille 5) ; *Metric* → **Count**.
4. Enregistrez : `BD516 - Répartition par niveau`.

### V2 – Série temporelle : requêtes et échecs dans le temps (barres empilées)

1. Type : **Bar vertical stacked**.
2. *Horizontal axis* → `@timestamp` (**Date histogram**, intervalle *Auto*) ; *Vertical axis* → **Count** ; *Breakdown* → **Top values** de `event.outcome`.
3. Couleurs : `failure` en rouge (*Breakdown* → `event.outcome` → *Color mapping*).
4. Enregistrez : `BD516 - Requêtes et échecs dans le temps`.

### V3 – Top N : les 10 endpoints les plus lents (barres horizontales)

1. Type : **Bar horizontal**.
2. *Vertical axis* → **Top values** de `endpoint` (taille **10**, classement par la métrique) ; *Horizontal axis* → **Percentile** de `shop.duration_ms`, percentile **95**.
3. Libellé de la métrique : `Latence p95 (ms)`.
4. Enregistrez : `BD516 - Top 10 endpoints les plus lents (p95)`.

### V4 – Top N géographique : pays des visiteurs du site (logs web)

1. Data view `BD516 - Logs web (nginx)` ; type **Table**.
2. *Rows* → **Top values** de `source.geo.country_name` (taille 10) ; *Metrics* → **Count**, puis **Unique count** de `source.address` (libellé `Visiteurs uniques`).
3. Enregistrez : `BD516 - Top 10 pays (web)`.

### V5 – Indicateur clé : taux d'erreur serveur (Metric)

1. Data view applicatif ; type **Metric**.
2. *Primary metric* → **Formula** :

```text
count(kql='http.response.status_code >= 500') / count(kql='http.response.status_code : *')
```

3. *Value format* : **Percent** ; libellé `Taux d'erreurs 5xx`.
4. Enregistrez : `BD516 - Taux d'erreurs 5xx`.

> **Suggestions** (bas de l'espace de travail) propose d'autres rendus du même jeu de dimensions ; essayez-les.

---

## Étape 6 – Composer le dashboard

1. ☰ → **Analytics → Dashboards** → **Create dashboard**.
2. **Add from library** (ou *Add* → *From library*) : ajoutez V1 à V5 et la recherche enregistrée `BD516 - Erreurs 5xx`.
3. Disposition conseillée : en haut V5 (petit) et V1 ; au milieu V2 sur toute la largeur ; en bas V3, V4 puis le tableau des erreurs. Redimensionnez en tirant l'angle des panneaux.
4. **Contrôles (filtres globaux interactifs)** : *Controls* → **Add control** (ou *Add* → *Controls* → *Control*) :
   - data view applicatif, champ `log.level`, type **Options list**, libellé `Niveau` ;
   - data view applicatif, champ `endpoint`, **Options list**, libellé `Endpoint` ;
   - data view applicatif, champ `shop.duration_ms`, **Range slider**, libellé `Durée (ms)`.
   Les contrôles filtrent tous les panneaux construits sur le même champ et, par défaut, se « chaînent » entre eux.
5. **Filtre global** : barre de requête du dashboard → `not tags : "internal_ip"` (exclut les health checks) – il s'applique à tous les panneaux.
6. **Plage temporelle** : *Last 7 days*.
7. **Save** → *Title* : `BD516 - Supervision de la boutique` ; cochez **Store time with dashboard** → **Save**.
8. Testez l'interactivité : dans V1, cliquez la part `ERROR` → un filtre s'ajoute pour tout le dashboard ; dans V2, sélectionnez à la souris la fenêtre d'incident → la plage temporelle de **tous** les panneaux change.

> **Pour aller plus loin** : ajoutez un panneau ES|QL (*Add* → *New panel* → *ES|QL*) avec la requête de l'étape 4.3, et une carte (*Add* → *Maps*, couche *Documents* sur `source.geo.location`).

---

## Étape 7 – Partager et exporter

### 7.1 Partager le dashboard

1. Dashboard en mode lecture → **Share** :
   - onglet **Link** → **Copy link** (option *Shorten URL* si proposée) ; la plage temporelle et les filtres en cours sont inclus ;
   - onglet **Embed** (*Embed code*) → **Copy embed code** : extrait `<iframe>` à intégrer dans un portail (l'utilisateur doit être authentifié).
2. Ouvrez le lien dans une **fenêtre privée** et connectez-vous avec `analyste` / `Analyste-BD516` (Lab 01) : le dashboard s'affiche en **lecture seule** (rôle `bd516_analyst` : `read` sur les espaces).

### 7.2 Exporter une visualisation

- **Données CSV** : sur le panneau V3, menu **⋯** → **Inspect** → **View : Data** → **Download CSV** (*Formatted CSV*).
- **Image / PDF** : **Share** → **Export** → **PNG** / **PDF** – fonctionnalité soumise à abonnement (*Gold+*), disponible avec la licence d'essai : `POST _license/start_trial?acknowledge=true` (Dev Tools ; action **unique et irréversible**, 30 jours).
- **Recherche enregistrée** : dans Discover, *Share* → **Export** → **CSV** (rapport CSV, inclus dans la licence Basic).

### 7.3 Exporter et réimporter les objets Kibana (NDJSON)

Interface : ☰ → **Stack Management → Kibana → Saved Objects** → cochez le dashboard → **Export** (*Include related objects*).

Équivalent API (export du dashboard **avec** ses visualisations, sa recherche et ses data views) :

```bash
mkdir -p ~/BD516/exports
kb POST /s/bd516/api/saved_objects/_export -d '{
  "type": ["dashboard"],
  "includeReferencesDeep": true
}' > ~/BD516/exports/bd516-dashboards.ndjson
grep -c '"type"' ~/BD516/exports/bd516-dashboards.ndjson
jq -r 'select(.type != null) | "\(.type)\t\(.attributes.title // .attributes.name)"' ~/BD516/exports/bd516-dashboards.ndjson
```

Réimportez-le dans l'espace **Default** (copie : nouveaux identifiants) :

```bash
curl -s --cacert "$ES_CA" -u "elastic:${ELASTIC_PASSWORD}" -H 'kbn-xsrf: bd516' \
  -X POST "${KB_URL}/api/saved_objects/_import?createNewCopies=true" \
  --form file=@"${HOME}/BD516/exports/bd516-dashboards.ndjson" | jq '{success, successCount}'
```

Attendu : `"success": true` et le nombre d'objets importés. Le fichier NDJSON est l'artefact à versionner (dépôt GitHub `BD516`, dossier `exports/`) pour reproduire le dashboard sur un autre cluster.

---

## Étape 8 (exercice) – Dashboard « Ventes »

Avec le data view `BD516 - Commandes` (plage *Last 1 year*), construisez seul un dashboard `BD516 - Ventes` :

1. **Metric** : chiffre d'affaires total (`Sum` de `total_amount`) et panier moyen ;
2. **Line** : CA mensuel (`order_date` par mois) avec une *Breakdown* par `channel` ;
3. **Donut** : répartition des commandes par `status` ;
4. **Bar horizontal** : top 10 de `customer.city` par CA ;
5. contrôle **Options list** sur `customer.segment`.

Contrôle : CA total ≈ **3 000 618,93 €**, panier moyen ≈ **300,06 €** (valeurs du Lab 02).

---

## Dépannage

| Symptôme | Cause / solution |
|---|---|
| « No results match your search criteria » | plage temporelle trop courte (*Last 15 minutes* par défaut) → *Last 7 days* |
| Data view : aucun index correspondant | motif mal saisi (`logs-bd516.app-*`) ou Lab 04 non terminé |
| Le champ `endpoint` est vide | script du champ runtime mal recopié : *Data Views* → `endpoint` → *Edit* (aperçu du résultat dans le formulaire) |
| ES\|QL : `Unknown index [logs-bd516.app-*]` | vous êtes dans un autre cluster / l'index n'existe pas : vérifiez dans Dev Tools `GET _data_stream/logs-bd516*` |
| ES\|QL `LOOKUP JOIN` refusé | l'index de référence doit être créé avec `"index.mode": "lookup"` (étape 4.5) |
| Export PNG/PDF absent ou grisé | licence Basic : utilisez l'export CSV ou démarrez la licence d'essai |
| `analyste` ne voit pas l'espace BD516 | rôle `bd516_analyst` non créé (Lab 01, étape 8) |

## Récapitulatif

- [x] Espace `bd516` et trois data views (interface et API), formats et champ runtime `endpoint`
- [x] Exploration Discover : histogramme, champs, filtres épinglés, KQL, Lucene, recherche sauvegardée
- [x] Requêtes ES|QL (`WHERE`, `EVAL`, `STATS ... BY`, `BUCKET`, `LOOKUP JOIN`)
- [x] Cinq visualisations Lens (répartition, série temporelle, top N, table, indicateur)
- [x] Dashboard avec contrôles, filtre global, plage temporelle enregistrée et interactions
- [x] Partage par lien / iframe, export CSV, export-import NDJSON des objets Kibana
