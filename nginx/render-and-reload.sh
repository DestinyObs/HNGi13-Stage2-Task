#!/bin/sh
set -eu

# Recompute backup flags based on ACTIVE_POOL
case "${ACTIVE_POOL:-blue}" in
  blue)
    export BLUE_BACKUP=""
    export GREEN_BACKUP="backup"
    ;;
  green)
    export BLUE_BACKUP="backup"
    export GREEN_BACKUP=""
    ;;
  *)
    echo "Invalid ACTIVE_POOL='${ACTIVE_POOL}'. Use 'blue' or 'green'." >&2
    exit 1
    ;;
esac

# Render template with current env
envsubst '\$BLUE_BACKUP \$GREEN_BACKUP' \
  < /etc/nginx/templates/default.conf.template \
  > /etc/nginx/conf.d/default.conf

# Test and reload nginx
nginx -t
nginx -s reload

echo "Nginx reloaded with ACTIVE_POOL=${ACTIVE_POOL} (blue backup='${BLUE_BACKUP}', green backup='${GREEN_BACKUP}')"
