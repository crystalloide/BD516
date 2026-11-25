# BD516 – Elastic Stack : Elasticsearch, Logstash et Kibana

Travaux pratiques de la formation **BD516** (2 jours, 14 h) : indexer, rechercher, transformer et visualiser des données avec la suite Elastic.

| | |
|---|---|
| Versions | **Elastic Stack 9.5.7** (Elasticsearch, Kibana, Logstash, Metricbeat) – Docker Engine 29.x + Compose v2 |
| Poste stagiaire | VM **Ubuntu 24.04** sous Hyper-V **ou** **Ubuntu 24.04 sous WSL2** – 4 vCPU, 16 Go de RAM recommandés (8 Go minimum), 60 Go de disque |
| Architecture | 3 nœuds Elasticsearch (TLS HTTP + transport, sécurité activée, tiers hot/warm/cold, nœud *voting_only*), Kibana HTTPS, Logstash (2 pipelines), Metricbeat (bonus) |

## Handlabs

| Lab | Chapitre du programme | Contenu |
|---|---|---|
| [Lab 00](labs/Lab00-preparation-environnement.md) | Prérequis | Ubuntu 24.04 (Hyper-V / WSL2), réglages noyau de production, Docker Engine, projet `~/BD516` |
| [Lab 01](labs/Lab01-cluster-elasticsearch-docker.md) | 1 – Introduction et architecture | Cluster 3 nœuds sécurisé par Docker Compose, logs, `_cluster/health`, `_cat`, Dev Tools, rôles, utilisateurs, clés d'API, bascule du master |
| [Lab 02](labs/Lab02-indexation-modelisation.md) | 2 – Indexation et modélisation | CRUD, mappings dynamique/strict/runtime, analyzers, templates, bulk, reindex + alias, update by query, shards, ILM, snapshots, SLM |
| [Lab 03](labs/Lab03-query-dsl-agregations.md) | 3 – Query DSL et agrégations | full-text, term-level, `bool`, `nested`, scoring, pagination, highlighting, agrégations metric/bucket/pipeline, kNN |
| [Lab 04](labs/Lab04-logstash-pipelines-etl.md) | 4 – Logstash | input file + multiline, grok, date, mutate, geoip, output elasticsearch (data streams, idempotence), multi-pipelines, file persistante, supervision |
| [Lab 05](labs/Lab05-kibana-discover-dashboards.md) | 5 – Kibana | Spaces, data views, Discover, KQL/Lucene, ES\|QL (dont LOOKUP JOIN), Lens, dashboard, partage et export |

## Démarrage rapide

```bash
cd ~
rm -Rf ~/BD516
```
```bash
sudo apt install jq -y
```
```bash
git clone https://github.com/crystalloide/BD516.git ~/BD516   # ou créer les fichiers avec le Lab 00
```
```bash
cd ~/BD516
find . \( -name '*.sh' -o -path '*/generators/*.py' \) -exec chmod +x {} +   # droits d'exécution
```
```bash
./scripts/check-prereqs.sh          # prérequis (Lab 00)
```
```bash
./scripts/init-env.sh               # génère docker/.env (mots de passe, clés, mémoire)
```
```bash
source scripts/bd516-env.sh         # fonctions es / esbulk / kb
```
```bash
cd docker && docker compose up -d   # cluster + Kibana (Lab 01)
```

Kibana : <https://localhost:5601> – utilisateur `elastic`, mot de passe : `bd516_passwords`.

## Arborescence

```text
BD516/
├── labs/                      handlabs Markdown (Lab00 à Lab05)
├── scripts/                   check-prereqs.sh, init-env.sh, bd516-env.sh, reset-lab.sh
├── docker/
│   ├── docker-compose.yml     setup, es01, es02, es03, kibana, logstash*, metricbeat*  (*profils)
│   ├── .env                   généré, NON versionné (secrets)
│   ├── setup/setup.sh         CA et certificats TLS, mot de passe kibana_system
│   ├── certs/                 certificats générés (NON versionnés)
│   ├── kibana/kibana.yml
│   ├── logstash/config/       logstash.yml, pipelines.yml
│   ├── logstash/pipeline/     bd516-app/*.conf, bd516-web/*.conf
│   ├── logstash/dev/          mise au point stdin -> filtre -> stdout
│   └── metricbeat/metricbeat.yml
├── data/
│   ├── generators/            gen_catalog.py (produits, commandes), gen_logs.py (logs app + nginx)
│   ├── orders/ products/      NDJSON générés pour l'API _bulk
│   └── logs/                  app/, web/, sample/ (générés)
└── exports/                   objets Kibana exportés (NDJSON)
```

## Remise à zéro

```bash
~/BD516/scripts/reset-lab.sh          # conteneurs, volumes, certificats
~/BD516/scripts/reset-lab.sh --all    # + fichier .env (nouveaux mots de passe)
```
