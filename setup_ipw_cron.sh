#!/bin/bash
#===============================================================================
# Setup Cron Job for IPW Audit Pipeline
# Runs weekly, accumulates 7 days of IPW data for reconciliation
#===============================================================================

PROJECT_DIR="/mnt/raid5/production-deployment"
SCRIPT_PATH="${PROJECT_DIR}/ipw_audit_pipeline.sh"

# Make the pipeline script executable
chmod +x "${SCRIPT_PATH}"

# Create cron job entry (every day at 07:00 PM)
CRON_JOB="0 19 * * * ${SCRIPT_PATH} >> ${PROJECT_DIR}/logs/ipw_audit_cron.log 2>&1"

# Check if cron job already exists
if crontab -l 2>/dev/null | grep -q "${SCRIPT_PATH}"; then
    echo "Cron job already exists. Updating..."
    # Remove existing entry
    crontab -l 2>/dev/null | grep -v "${SCRIPT_PATH}" | crontab -
fi

# Add new cron job
(crontab -l 2>/dev/null; echo "${CRON_JOB}") | crontab -

echo "=============================================="
echo "IPW Audit cron job installed successfully!"
echo "=============================================="
echo ""
echo "Schedule: Every day at 11:00 AM"
echo "Retention: 7 days of IPW data"
echo "Script:   ${SCRIPT_PATH}"
echo "Log:      ${PROJECT_DIR}/logs/ipw_audit_cron.log"
echo ""
echo "Current crontab:"
crontab -l
echo ""
echo "=============================================="
echo "Useful commands:"
echo "  - View cron jobs:    crontab -l"
echo "  - Edit cron jobs:    crontab -e"
echo "  - View cron logs:    tail -f ${PROJECT_DIR}/logs/ipw_audit_cron.log"
echo "  - Manual run:        ${SCRIPT_PATH}"
echo "=============================================="
