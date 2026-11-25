# BD516 – Lab 00 : préparation du poste stagiaire

> **Formation BD516 – Elastic Stack : Elasticsearch, Logstash et Kibana**
> Projet / dépôt GitHub : **BD516** – Versions : **Elastic Stack 9.5.7**, Docker Engine 29.x (dépôt officiel), Ubuntu 24.04 LTS
> Durée : 45 min (à faire avant le Lab 01, idéalement avant la formation)

## Objectifs

- Disposer d'un Ubuntu 24.04 opérationnel, **au choix** :
  - **Option A** : machine virtuelle Ubuntu 24.04 sous **Hyper-V** ;
  - **Option B** : **Ubuntu 24.04 sous WSL2** (Windows 10 22H2 ou Windows 11).
- Appliquer les **réglages système recommandés en production** pour Elasticsearch (mémoire virtuelle, swap, fuseau horaire).
- Installer **Docker Engine** et le plugin **Docker Compose** depuis le dépôt officiel Docker.
- Créer le projet **`~/BD516`** (même nom que le dépôt GitHub) et ses scripts outils.
- Valider l'ensemble avec le script de vérification des prérequis.

## Dimensionnement

| Ressource | Minimum | Recommandé | Remarque |
|---|---|---|---|
| vCPU | 2 | **4** | 3 nœuds Elasticsearch + Kibana + Logstash |
| RAM | 8 Go | **16 Go** | le script `init-env.sh` adapte les limites mémoire à la RAM détectée |
| Disque libre | 30 Go | **60 Go** | au-delà de 85 % d'occupation, Elasticsearch n'alloue plus les réplicas |
| Réseau | accès Internet | – | `download.docker.com`, `docker.elastic.co`, `geoip.elastic.co` |

> Toutes les commandes sont à exécuter **dans le terminal Ubuntu** (VM ou WSL), sauf mention « PowerShell ».

---

## Étape 1 – Obtenir un Ubuntu 24.04

### Option A – VM Hyper-V

1. Téléchargez l'ISO **Ubuntu 24.04 LTS** (Desktop ou Server) depuis <https://ubuntu.com/download>.
2. Dans le **Gestionnaire Hyper-V** : *Nouveau → Ordinateur virtuel* :
   - **Génération 2** ;
   - mémoire **16 384 Mo**, **décochez « Utiliser la mémoire dynamique »** (Elasticsearch verrouille sa mémoire : la mémoire dynamique est déconseillée en production) ;
   - réseau : *Default Switch* ;
   - disque : **80 Go** ;
   - installez depuis l'ISO.
3. Avant le premier démarrage, dans *Paramètres de la VM* :
   - *Sécurité* → modèle de démarrage sécurisé : **Autorité de certification UEFI Microsoft** ;
   - *Processeur* → **4** processeurs virtuels.
4. Installez Ubuntu (utilisateur au choix, par exemple `stagiaire`), puis mettez à jour :

```bash
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```

### Option B – WSL2

Dans **PowerShell (administrateur)** :

```powershell
wsl --install -d Ubuntu-24.04
```

Créez ensuite le fichier `%USERPROFILE%\.wslconfig` (par exemple avec `notepad $env:USERPROFILE\.wslconfig`) :

```ini
[wsl2]
# Ressources allouees a la VM WSL2 (adaptez a votre poste : 12 Go minimum conseille)
memory=12GB
processors=4
# Pas de swap pour Elasticsearch
swap=0
# Reglage noyau requis par Elasticsearch, applique a chaque demarrage de WSL
kernelCommandLine = "sysctl.vm.max_map_count=1048576"
```

Dans Ubuntu, activez **systemd** (indispensable pour le service Docker) :

```bash
sudo tee /etc/wsl.conf > /dev/null <<'EOF'
[boot]
systemd=true

[interop]
appendWindowsPath=false
EOF
```

Puis, dans **PowerShell** : redémarrez WSL et rouvrez le terminal Ubuntu.

```powershell
wsl --shutdown
```

Vérifiez dans Ubuntu :

```bash
ps -p 1 -o comm=        # doit afficher : systemd
nproc; free -h
```

> **Docker Desktop déjà installé ?** Ce lab installe Docker Engine **directement dans Ubuntu**. Dans Docker Desktop, désactivez l'intégration WSL pour *Ubuntu-24.04* (*Settings → Resources → WSL integration*) afin d'éviter deux moteurs Docker concurrents.

> **Important (WSL)** : travaillez toujours dans le système de fichiers Linux (`~/BD516`), **jamais** sous `/mnt/c/...` (performances et droits incorrects).

---

## Étape 2 – Paquets de base et fuseau horaire

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg jq git openssl unzip python3 tzdata iproute2
sudo timedatectl set-timezone Europe/Paris
timedatectl | grep "Time zone"
```

Résultat attendu : `Time zone: Europe/Paris (CET, +0100)` (ou CEST en été).

---

## Étape 3 – Réglages système recommandés pour Elasticsearch

Elasticsearch utilise intensivement les fichiers mappés en mémoire (`mmap`) : la valeur par défaut de `vm.max_map_count` (65 530) est trop basse. En mode production (écoute sur une interface non locale, ce qui est le cas dans un conteneur), Elasticsearch **refuse de démarrer** si les *bootstrap checks* échouent.

```bash
sudo tee /etc/sysctl.d/99-bd516-elasticsearch.conf > /dev/null <<'EOF'
# BD516 - reglages recommandes par Elastic pour les hotes Elasticsearch
# Nombre maximal de zones memoire mappees par processus (valeur recommandee : 1048576)
vm.max_map_count = 1048576
# Ne swapper qu'en dernier recours
vm.swappiness = 1
EOF
sudo sysctl --system | grep -E "max_map_count|swappiness"
```

Désactivez le swap (**option A – VM** uniquement ; sous WSL c'est `swap=0` dans `.wslconfig`) :

```bash
sudo swapoff -a
sudo sed -i.bak -E 's@^([^#].*[[:space:]]swap[[:space:]].*)$@# \1@' /etc/fstab
grep -i swap /etc/fstab; free -h | grep -i swap
```

Résultat attendu : `Swap: 0B 0B 0B`.

> Les autres réglages de production (limites `nofile`/`memlock`, `bootstrap.memory_lock`, `net.ipv4.tcp_retries2=5`, limites mémoire des conteneurs) sont portés par le fichier `docker-compose.yml` du Lab 01.

---

## Étape 4 – Installer Docker Engine (dépôt officiel)

Supprimez d'éventuels paquets concurrents (aucune erreur si absents) :

```bash
for pkg in docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc; do
  sudo apt remove -y "$pkg" 2>/dev/null
done
```

Ajoutez le dépôt Docker et installez la dernière version :

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Autorisez votre utilisateur à piloter Docker sans `sudo`, puis ouvrez une nouvelle session :

```bash
sudo usermod -aG docker "$USER"
newgrp docker     # ou fermez/rouvrez le terminal (WSL : wsl --shutdown si besoin)
```

---

## Étape 5 – Configuration du démon Docker (journalisation de production)

Le pilote `json-file` par défaut ne fait pas de rotation : en production, on utilise le pilote **`local`** (compressé, avec rotation).

```bash
sudo tee /etc/docker/daemon.json > /dev/null <<'EOF'
{
  "log-driver": "local",
  "log-opts": {
    "max-size": "20m",
    "max-file": "5"
  }
}
EOF
sudo systemctl enable --now docker
sudo systemctl restart docker
docker version --format 'Client {{.Client.Version}} / Moteur {{.Server.Version}}'
docker compose version
docker run --rm hello-world | head -3
```

Résultat attendu : versions 29.x (ou plus récentes) et le message `Hello from Docker!`.

---

## Étape 6 – Créer le projet BD516

Deux possibilités :

- **le dépôt GitHub BD516 est publié** par le formateur (par exemple `https://github.com/crystalloide/BD516`) :

```bash
git clone https://github.com/crystalloide/BD516.git ~/BD516
```

  Les fichiers des étapes 6.1 à 6.5 sont alors déjà présents : passez directement à la fin de l'étape 6 (`chmod` et `~/.bashrc`).

- **sinon**, créez l'arborescence vous-même ; tous les fichiers sont fournis dans les labs :

```bash
mkdir -p ~/BD516/{labs,scripts,exports} \
         ~/BD516/docker/{setup,kibana,certs,metricbeat} \
         ~/BD516/docker/logstash/{config,dev,pipeline/bd516-app,pipeline/bd516-web} \
         ~/BD516/data/{generators,orders,products,logs/app,logs/web,logs/sample}
cd ~/BD516 && git init -b main
touch docker/certs/.gitkeep data/orders/.gitkeep data/products/.gitkeep \
      data/logs/app/.gitkeep data/logs/web/.gitkeep data/logs/sample/.gitkeep
```

### 6.1 Fichier `.gitignore` (secrets et données générées exclus du dépôt)

```bash
cat > ~/BD516/.gitignore <<'BD516_EOF'
# BD516 - fichiers generes ou secrets (ne jamais commiter)
docker/.env
docker/certs/*
!docker/certs/.gitkeep
data/orders/*.ndjson
data/products/*.ndjson
data/logs/**/*.log
data/logs/**/*.tmp
data/expected-results.txt
exports/
__pycache__/
*.pyc
BD516_EOF
```

### 6.2 Script de vérification des prérequis

```bash
cat > ~/BD516/scripts/check-prereqs.sh <<'BD516_EOF'
#!/usr/bin/env bash
# =============================================================================
# BD516 - Verification des prerequis du poste stagiaire
# (VM Ubuntu 24.04 sous Hyper-V ou Ubuntu 24.04 sous WSL2)
# Usage : ./scripts/check-prereqs.sh      -> code retour 0 si tout est OK
# =============================================================================
set -uo pipefail

BD516_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ok=0; warn=0; ko=0
pass() { printf '  [ OK ] %s\n' "$*"; ok=$((ok + 1)); }
attn() { printf '  [WARN] %s\n' "$*"; warn=$((warn + 1)); }
fail() { printf '  [ KO ] %s\n' "$*"; ko=$((ko + 1)); }

echo "=== BD516 - verification des prerequis ($(date '+%Y-%m-%d %H:%M')) ==="

# --- Systeme ----------------------------------------------------------------
if grep -q 'VERSION_ID="24.04"' /etc/os-release 2>/dev/null; then
  pass "Ubuntu 24.04 ($(grep PRETTY_NAME /etc/os-release | cut -d'"' -f2))"
else
  attn "Systeme non teste : $(grep PRETTY_NAME /etc/os-release 2>/dev/null | cut -d'"' -f2)"
fi
if grep -qi microsoft /proc/version 2>/dev/null; then
  ENV_KIND="WSL2"
  if [[ "$(ps -p 1 -o comm= 2>/dev/null)" == "systemd" ]]; then
    pass "WSL2 avec systemd actif"
  else
    fail "WSL2 sans systemd : ajoutez [boot] systemd=true dans /etc/wsl.conf puis 'wsl --shutdown'"
  fi
else
  ENV_KIND="VM"
  pass "Machine virtuelle / Linux natif"
fi

cpus=$(nproc)
if (( cpus >= 4 )); then pass "${cpus} vCPU"; else attn "${cpus} vCPU (4 recommandes)"; fi

mem_gib=$(awk '/MemTotal/ {printf "%d", $2/1024/1024 + 0.5}' /proc/meminfo)
if (( mem_gib >= 15 )); then pass "${mem_gib} Gio de RAM"
elif (( mem_gib >= 11 )); then attn "${mem_gib} Gio de RAM (16 recommandes, profil memoire reduit)"
elif (( mem_gib >= 7 )); then attn "${mem_gib} Gio de RAM : profil minimal, Kibana sera lent (16 recommandes)"
else fail "${mem_gib} Gio de RAM : insuffisant (minimum 8, 16 recommandes)"; fi

avail_gb=$(df -BG --output=avail "${HOME}" | tail -1 | tr -dc '0-9')
used_pct=$(df --output=pcent "${HOME}" | tail -1 | tr -dc '0-9')
if (( avail_gb >= 30 && used_pct < 70 )); then pass "Disque : ${avail_gb} Go libres (${used_pct}% utilise)"
elif (( avail_gb >= 15 && used_pct < 80 )); then attn "Disque : ${avail_gb} Go libres, ${used_pct}% utilise (30 Go libres recommandes)"
else fail "Disque : ${avail_gb} Go libres, ${used_pct}% utilise -> au-dela de 85% Elasticsearch n'alloue plus les replicas"; fi

case "${BD516_HOME}" in
  /mnt/*) fail "Le projet est sur un disque Windows (${BD516_HOME}) : placez-le dans ~/BD516 (systeme de fichiers Linux)";;
  *) pass "Projet dans ${BD516_HOME}";;
esac

# --- Reglages noyau ------------------------------------------------------------
mmc=$(sysctl -n vm.max_map_count 2>/dev/null || echo 0)
if (( mmc >= 1048576 )); then pass "vm.max_map_count = ${mmc}"
else fail "vm.max_map_count = ${mmc} (1048576 requis) -> voir Lab 00, etape 3"; fi

swp=$(sysctl -n vm.swappiness 2>/dev/null || echo 60)
if (( swp <= 1 )); then pass "vm.swappiness = ${swp}"; else attn "vm.swappiness = ${swp} (1 recommande)"; fi

swap_kb=$(awk '/SwapTotal/ {print $2}' /proc/meminfo)
if (( swap_kb == 0 )); then pass "Swap desactive"; else attn "Swap actif ($((swap_kb / 1024)) Mio) : memory_lock protege la heap, mais desactiver le swap est recommande"; fi

tz=$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo "?")
if [[ "${tz}" == "Europe/Paris" ]]; then pass "Fuseau horaire Europe/Paris"; else attn "Fuseau horaire ${tz} (Europe/Paris attendu par les labs)"; fi

# --- Outils -------------------------------------------------------------------
for tool in curl jq git openssl python3 unzip; do
  if command -v "${tool}" >/dev/null 2>&1; then pass "${tool} present"; else fail "${tool} absent (sudo apt install -y ${tool})"; fi
done
if python3 -c 'from zoneinfo import ZoneInfo; ZoneInfo("Europe/Paris")' 2>/dev/null; then
  pass "Python $(python3 -c 'import platform; print(platform.python_version())') avec zoneinfo"
else fail "Python : module zoneinfo / tzdata indisponible (sudo apt install -y tzdata)"; fi

# --- Docker -------------------------------------------------------------------
if command -v docker >/dev/null 2>&1; then
  pass "Docker CLI $(docker version --format '{{.Client.Version}}' 2>/dev/null)"
  if docker info >/dev/null 2>&1; then
    pass "Moteur Docker $(docker version --format '{{.Server.Version}}' 2>/dev/null) accessible sans sudo"
    drv=$(docker info --format '{{.Driver}}' 2>/dev/null)
    pass "Stockage Docker : ${drv}"
    logdrv=$(docker info --format '{{.LoggingDriver}}' 2>/dev/null)
    if [[ "${logdrv}" == "local" ]]; then pass "Pilote de logs Docker : local (rotation)"; else attn "Pilote de logs Docker : ${logdrv} ('local' recommande, Lab 00 etape 5)"; fi
  else
    fail "Moteur Docker inaccessible : 'sudo usermod -aG docker \$USER' puis reconnectez-vous"
  fi
  if docker compose version >/dev/null 2>&1; then pass "$(docker compose version)"; else fail "Plugin docker compose absent"; fi
else
  fail "Docker absent -> Lab 00, etape 4"
fi

# --- Ports --------------------------------------------------------------------
for port in 9200 9201 9202 5601 9600; do
  if ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]${port}\$"; then
    if docker ps --format '{{.Ports}}' 2>/dev/null | grep -q ":${port}->"; then pass "Port ${port} utilise par BD516"
    else fail "Port ${port} deja utilise par un autre programme"; fi
  else pass "Port ${port} libre"; fi
done

echo "=== Environnement ${ENV_KIND} : ${ok} OK, ${warn} avertissement(s), ${ko} erreur(s) ==="
(( ko == 0 ))
BD516_EOF
```

### 6.3 Script de génération du fichier `.env` (mots de passe, clés, mémoire)

```bash
cat > ~/BD516/scripts/init-env.sh <<'BD516_EOF'
#!/usr/bin/env bash
# =============================================================================
# BD516 - Generation du fichier docker/.env (mots de passe, cles, memoire)
# Usage : ./scripts/init-env.sh           (ne touche pas a un .env existant)
#         ./scripts/init-env.sh --force   (regenere : uniquement apres reset-lab.sh)
# =============================================================================
set -euo pipefail

BD516_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${BD516_HOME}/docker/.env"
STACK_VERSION="9.5.7"

if [[ -f "${ENV_FILE}" && "${1:-}" != "--force" ]]; then
  echo "Le fichier ${ENV_FILE} existe deja : rien a faire (option --force pour le regenerer)."
  exit 0
fi

secret() { openssl rand -hex "$1"; }   # caracteres [0-9a-f] uniquement : aucun souci de quoting

# Dimensionnement memoire selon la RAM disponible
mem_gib=$(awk '/MemTotal/ {printf "%d", $2/1024/1024 + 0.5}' /proc/meminfo)
if (( mem_gib >= 15 )); then
  ES_MEM=2g;    KB_MEM=2g;    LS_MEM=2g;    LS_HEAP=1g;   PROFILE="standard (16 Go)"
elif (( mem_gib >= 11 )); then
  ES_MEM=1536m; KB_MEM=1536m; LS_MEM=1536m; LS_HEAP=768m; PROFILE="reduit (12 Go)"
else
  ES_MEM=1g;    KB_MEM=1536m; LS_MEM=1g;    LS_HEAP=512m; PROFILE="minimal (8 Go)"
fi

# Noms et adresses de la machine hote ajoutes aux certificats (acces depuis Windows)
san_dns=$( { hostname -s; hostname -f; } 2>/dev/null | tr -cd 'A-Za-z0-9.\n-' | grep -v '^localhost$' | sort -u | paste -sd, - || true)
san_ips=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+(\.[0-9]+){3}$' | grep -v '^127\.' | sort -u | paste -sd, - || true)

umask 077
cat > "${ENV_FILE}" <<EOF
# =============================================================================
# BD516 - fichier .env genere le $(date '+%Y-%m-%d %H:%M') par scripts/init-env.sh
# NE PAS COMMITER (contient des secrets) - il est exclu par .gitignore
# =============================================================================
STACK_VERSION=${STACK_VERSION}
CLUSTER_NAME=bd516-cluster
LICENSE=basic

# Adresse d'ecoute des ports publies (0.0.0.0 = toutes les interfaces de l'hote)
BIND_IP=0.0.0.0

# Memoire - profil ${PROFILE} (RAM detectee : ${mem_gib} Gio)
ES_MEM_LIMIT=${ES_MEM}
KB_MEM_LIMIT=${KB_MEM}
LS_MEM_LIMIT=${LS_MEM}
LS_HEAP=${LS_HEAP}

# Mots de passe (hexadecimaux, sans caractere special)
ELASTIC_PASSWORD=$(secret 12)
KIBANA_PASSWORD=$(secret 12)
LOGSTASH_WRITER_PASSWORD=$(secret 12)
MONITORING_PASSWORD=$(secret 12)

# Cles de chiffrement Kibana (64 caracteres)
KIBANA_ENC_KEY=$(secret 32)
KIBANA_REPORTING_KEY=$(secret 32)
KIBANA_SECURITY_KEY=$(secret 32)
KIBANA_PUBLIC_URL=https://localhost:5601

# Noms / IP supplementaires inscrits dans les certificats TLS
CERT_SAN_DNS=${san_dns}
CERT_SAN_IPS=${san_ips}
EOF

echo "Fichier ${ENV_FILE} cree (profil memoire ${PROFILE})."
echo "Mot de passe du super-utilisateur 'elastic' : $(grep '^ELASTIC_PASSWORD=' "${ENV_FILE}" | cut -d= -f2)"
BD516_EOF
```

### 6.4 Fonctions d'aide pour le shell (`es`, `esbulk`, `kb`)

```bash
cat > ~/BD516/scripts/bd516-env.sh <<'BD516_EOF'
# shellcheck shell=bash
# =============================================================================
# BD516 - Variables et fonctions d'aide pour le shell (a "sourcer")
#   source ~/BD516/scripts/bd516-env.sh
# Fonctions :
#   es     METHODE CHEMIN [options curl]   -> API REST Elasticsearch (utilisateur elastic)
#   esbulk CHEMIN FICHIER.ndjson           -> API _bulk (Content-Type x-ndjson)
#   kb     METHODE CHEMIN [options curl]   -> API REST Kibana
#   bd516_passwords                        -> affiche les mots de passe du .env
# =============================================================================
export BD516_HOME="${BD516_HOME:-${HOME}/BD516}"
export ES_URL="https://localhost:9200"
export KB_URL="https://localhost:5601"
export ES_CA="${BD516_HOME}/docker/certs/ca/ca.crt"

if [[ -f "${BD516_HOME}/docker/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${BD516_HOME}/docker/.env"
  set +a
fi

es() {
  local method="$1" path="$2"
  shift 2
  curl -sS --cacert "${ES_CA}" -u "elastic:${ELASTIC_PASSWORD}" -X "${method}" \
       -H 'Content-Type: application/json' "${ES_URL}${path}" "$@"
  echo
}

esbulk() {
  local path="$1" file="$2"
  curl -sS --cacert "${ES_CA}" -u "elastic:${ELASTIC_PASSWORD}" -X POST \
       -H 'Content-Type: application/x-ndjson' "${ES_URL}${path}" --data-binary "@${file}"
  echo
}

kb() {
  local method="$1" path="$2"
  shift 2
  curl -sS --cacert "${ES_CA}" -u "elastic:${ELASTIC_PASSWORD}" -X "${method}" \
       -H 'kbn-xsrf: bd516' -H 'Content-Type: application/json' "${KB_URL}${path}" "$@"
  echo
}

bd516_passwords() {
  grep -E '^(ELASTIC|KIBANA|LOGSTASH_WRITER|MONITORING)_PASSWORD=' "${BD516_HOME}/docker/.env"
}

# Raccourci : se placer dans le dossier docker pour les commandes "docker compose"
alias bd516='cd "${BD516_HOME}/docker"'
BD516_EOF
```

### 6.5 Script de remise à zéro

```bash
cat > ~/BD516/scripts/reset-lab.sh <<'BD516_EOF'
#!/usr/bin/env bash
# =============================================================================
# BD516 - Remise a zero complete de l'environnement
#   - arrete et supprime tous les conteneurs et volumes (donnees, snapshots)
#   - supprime les certificats generes
#   - option --all : supprime aussi docker/.env (nouveaux mots de passe ensuite)
# =============================================================================
set -euo pipefail

BD516_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${BD516_HOME}/docker"

read -r -p "Supprimer TOUTES les donnees du cluster BD516 ? (oui/non) " answer
[[ "${answer}" == "oui" ]] || { echo "Abandon."; exit 1; }

docker compose --profile logstash --profile monitoring down -v --remove-orphans
sudo find "${BD516_HOME}/docker/certs" -mindepth 1 ! -name '.gitkeep' -delete
if [[ "${1:-}" == "--all" ]]; then
  rm -f "${BD516_HOME}/docker/.env"
  echo "docker/.env supprime : relancez ./scripts/init-env.sh"
fi
echo "Environnement reinitialise. Redemarrage : cd ~/BD516/docker && docker compose up -d"
BD516_EOF
```

Rendez les scripts exécutables et chargez les fonctions d'aide à chaque ouverture de terminal :

```bash
chmod +x ~/BD516/scripts/*.sh
grep -q 'BD516/scripts/bd516-env.sh' ~/.bashrc || \
  echo '[ -f ~/BD516/scripts/bd516-env.sh ] && source ~/BD516/scripts/bd516-env.sh' >> ~/.bashrc
source ~/.bashrc
```

---

## Étape 7 – Vérifier les prérequis

```bash
cd ~/BD516
./scripts/check-prereqs.sh
```

Résultat attendu (extrait) : aucune ligne `[ KO ]`.

```text
  [ OK ] Ubuntu 24.04 (Ubuntu 24.04.x LTS)
  [ OK ] vm.max_map_count = 1048576
  [ OK ] Moteur Docker 29.x accessible sans sudo
  [ OK ] Pilote de logs Docker : local (rotation)
  ...
=== Environnement VM : 27 OK, 0 avertissement(s), 0 erreur(s) ===
```

Les `[WARN]` (RAM < 16 Go, swap actif…) n'empêchent pas de continuer : les labs s'adaptent.

---

## Étape 8 – Pré-télécharger les images Elastic 9.5.7

Gain de temps pour le Lab 01 et validation de l'accès au registre `docker.elastic.co` :

```bash
for img in elasticsearch/elasticsearch kibana/kibana logstash/logstash beats/metricbeat; do
  docker pull "docker.elastic.co/${img}:9.5.7"
done
docker image ls --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}' | grep elastic
```

---

## Étape 9 (facultatif) – Publier le squelette sur GitHub

Le fichier `.gitignore` exclut le fichier `.env` (mots de passe), les certificats et les données générées.

```bash
cd ~/BD516
git add .
git commit -m "BD516 : squelette du projet et scripts outils"
git remote add origin https://github.com/<votre-compte>/BD516.git
git push -u origin main
```

---

## Dépannage

| Symptôme | Cause / solution |
|---|---|
| `ps -p 1` n'affiche pas `systemd` (WSL) | `/etc/wsl.conf` absent ou incorrect → étape 1B puis `wsl --shutdown` |
| `permission denied ... /var/run/docker.sock` | groupe `docker` non pris en compte → `newgrp docker` ou nouvelle session |
| `vm.max_map_count` revient à 65530 (WSL) | ligne `kernelCommandLine` absente de `.wslconfig`, puis `wsl --shutdown` |
| `docker pull` très lent / refusé | proxy d'entreprise : configurez `HTTPS_PROXY` pour le démon Docker (`systemctl edit docker`) |
| Disque à plus de 80 % | agrandissez le disque de la VM ; Elasticsearch bloque l'allocation à 85 % (*low watermark*) |

## Récapitulatif

- [x] Ubuntu 24.04 (VM Hyper-V ou WSL2) avec systemd, fuseau Europe/Paris
- [x] `vm.max_map_count = 1048576`, `vm.swappiness = 1`, swap désactivé
- [x] Docker Engine 29.x + Compose, journalisation `local` avec rotation
- [x] Projet `~/BD516` et scripts `check-prereqs.sh`, `init-env.sh`, `bd516-env.sh`, `reset-lab.sh`
- [x] Images Elastic 9.5.7 téléchargées
