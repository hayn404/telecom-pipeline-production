#!/bin/bash
#===================================================================================
# Telecom Pipeline - Stop Production Services
#===================================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "============================================"
echo "Stopping Production Services"
echo "============================================"
echo ""

cd "$PROJECT_ROOT"

docker-compose -f docker-compose-production.yml down

echo ""
echo "✓ All services stopped"
echo ""
echo "Note: Data in ./data/parquet/ is preserved"
echo "To restart: ./02-deploy-production.sh"
echo ""
