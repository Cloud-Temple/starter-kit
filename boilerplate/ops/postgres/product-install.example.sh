#!/bin/sh
# Copy to product-install.sh and call YOUR public installer and read-only check.
# Do not put application DDL here in the distributed kit. No credentials fallback.
echo 'Product installation hook missing: complete DATA_DESIGN and product-install.sh.' >&2
exit 1
