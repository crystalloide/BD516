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
