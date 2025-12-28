#!/bin/bash
#===================================================================================
# Check Spark Processor Logs
#===================================================================================

echo "Checking Spark processor logs..."
echo ""

# Check if container exists
if docker ps -a | grep -q "telecom-prod-spark-processor"; then
    echo "📋 Recent logs from Spark processor:"
    echo "========================================"
    docker logs telecom-prod-spark-processor 2>&1 | tail -50
else
    echo "❌ No Spark processor container found"
    echo "The container may have been removed after crash"
fi

echo ""
echo "========================================"
echo "💾 System memory status:"
free -h

echo ""
echo "========================================"
echo "🔍 Docker system info:"
docker system df
