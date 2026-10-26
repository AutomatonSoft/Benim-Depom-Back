#!/bin/sh
set -e

if [ "$(id -u)" = "0" ]; then
  mkdir -p /app/runtime /app/staticfiles /app/product-media
  chown django:django /app/runtime /app/staticfiles /app/product-media
  exec gosu django "$0" "$@"
fi

exec "$@"
