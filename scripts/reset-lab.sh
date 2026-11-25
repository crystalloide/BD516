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
