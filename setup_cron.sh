#!/bin/bash
#===============================================================================
# Setup Cron Job for Daily Pipeline Execution
# Runs daily at 12:00 PM (noon)
#===============================================================================

PROJECT_DIR="/mnt/raid5/production-deployment"
SCRIPT_PATH="${PROJECT_DIR}/daily_pipeline.sh"

# Make the pipeline script executable
chmod +x "${SCRIPT_PATH}"

# Create cron job entry (3:00 PM daily)
CRON_JOB="0 15 * * * ${SCRIPT_PATH} >> ${PROJECT_DIR}/logs/cron.log 2>&1"

# Check if cron job already exists
if crontab -l 2>/dev/null | grep -q "${SCRIPT_PATH}"; then
    echo "Cron job already exists. Updating..."
    # Remove existing entry
    crontab -l 2>/dev/null | grep -v "${SCRIPT_PATH}" | crontab -
fi

# Add new cron job
(crontab -l 2>/dev/null; echo "${CRON_JOB}") | crontab -

echo "=============================================="
echo "Cron job installed successfully!"
echo "=============================================="
echo ""
echo "Schedule: Daily at 3:00 PM"
echo "Script:   ${SCRIPT_PATH}"
echo "Log:      ${PROJECT_DIR}/logs/cron.log"
echo ""
echo "Current crontab:"
crontab -l
echo ""
echo "=============================================="
echo "Useful commands:"
echo "  - View cron jobs:    crontab -l"
echo "  - Edit cron jobs:    crontab -e"
echo "  - Remove cron jobs:  crontab -r"
echo "  - View cron logs:    tail -f ${PROJECT_DIR}/logs/cron.log"
echo "  - Manual run:        ${SCRIPT_PATH}"
echo "=============================================="
