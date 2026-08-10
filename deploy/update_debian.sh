#!/usr/bin/env bash
#
# Atualiza o Zabbix Automation já instalado em /opt/zabbix_automation:
# sincroniza o código, reinstala dependências se necessário, rebuilda o
# frontend e reinicia o serviço do backend + o servidor web em uso.
#
# Uso (como root, a partir da pasta raiz do projeto atualizada):
#   sudo ./deploy/update_debian.sh
#
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Este script precisa ser executado como root (sudo)." >&2
  exit 1
fi

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="/opt/zabbix_automation"
SERVICE_USER="zabbixauto"

# Detecta qual servidor web foi usado na instalação (qual dos dois tem o
# site "zabbix-automation" habilitado).
if [[ -f /etc/apache2/sites-enabled/zabbix-automation.conf ]]; then
  WEB_SERVER="apache"
elif [[ -f /etc/nginx/sites-enabled/zabbix-automation.conf ]]; then
  WEB_SERVER="nginx"
else
  echo "Não encontrei a configuração do site em /etc/apache2 nem /etc/nginx." >&2
  echo "Rode ./deploy/install_debian.sh primeiro." >&2
  exit 1
fi
echo "==> Servidor web detectado: ${WEB_SERVER}"

echo "==> Sincronizando código para ${INSTALL_DIR}..."
rsync -a --delete \
  --exclude ".git" \
  --exclude "backend/.venv" \
  --exclude "backend/__pycache__" \
  --exclude "backend/src/**/__pycache__" \
  --exclude "backend/.env" \
  --exclude "backend/output" \
  --exclude "frontend/node_modules" \
  --exclude "frontend/dist" \
  --exclude "frontend/.env" \
  --exclude "logs" \
  "${SOURCE_DIR}/" "${INSTALL_DIR}/"

# logs/ e backend/output/ são dados de runtime (arquivos gerados pela própria
# aplicação, não código-fonte) e por isso ficam de fora do rsync acima -- se
# fossem sincronizados, o "--delete" apagaria a cada update os logs
# (logs/app.log, app-error.log) e os arquivos que os usuários exportaram.
mkdir -p "${INSTALL_DIR}/logs" "${INSTALL_DIR}/backend/output"

echo "==> Atualizando dependências do backend..."
"${INSTALL_DIR}/backend/.venv/bin/pip" install -r "${INSTALL_DIR}/backend/requirements.txt"

echo "==> Rebuild do frontend..."
echo "VITE_API_BASE_URL=/api" > "${INSTALL_DIR}/frontend/.env"
(
  cd "${INSTALL_DIR}/frontend"
  npm ci
  npm run build
)

# Reaplica a configuração do servidor web (caso os arquivos em deploy/ do
# repositório tenham sido atualizados também).
if [[ "${WEB_SERVER}" == "apache" ]]; then
  cp "${INSTALL_DIR}/deploy/apache/zabbix-automation.conf" \
     /etc/apache2/sites-available/zabbix-automation.conf
  apache2ctl configtest
else
  cp "${INSTALL_DIR}/deploy/nginx/zabbix-automation.conf" \
     /etc/nginx/sites-available/zabbix-automation.conf
  nginx -t
fi

chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"

echo "==> Reiniciando backend..."
systemctl restart zabbix-automation-backend.service

if [[ "${WEB_SERVER}" == "apache" ]]; then
  systemctl reload apache2
else
  systemctl reload nginx
fi

echo "==> Atualização concluída."
