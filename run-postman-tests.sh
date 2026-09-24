#!/bin/bash
# Ross API - Postman Test Runner
# Usage: ./run-postman-tests.sh [format] [folder]
# Example: ./run-postman-tests.sh html AUTH

set -e

# Configuration
COLLECTION="./Ross-API-Tests.postman_collection.json"
ENVIRONMENT="./Ross-Env-Local.postman_environment.json"
RESULTS_DIR="./test-results"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Arguments
FORMAT="${1:-cli}"  # cli, html, json (default: cli)
FOLDER="${2:-}"     # Optional: specific folder to run

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Create results directory
mkdir -p "$RESULTS_DIR"

echo -e "${YELLOW}================================${NC}"
echo -e "${YELLOW}Ross API - Postman Test Suite${NC}"
echo -e "${YELLOW}================================${NC}"
echo ""

# Check if files exist
if [ ! -f "$COLLECTION" ]; then
    echo -e "${RED}ERROR: Collection file not found: $COLLECTION${NC}"
    exit 1
fi

if [ ! -f "$ENVIRONMENT" ]; then
    echo -e "${RED}ERROR: Environment file not found: $ENVIRONMENT${NC}"
    exit 1
fi

# Build newman command
NEWMAN_CMD="newman run $COLLECTION -e $ENVIRONMENT"

if [ -n "$FOLDER" ]; then
    echo -e "${YELLOW}Running folder: ${FOLDER}${NC}"
    NEWMAN_CMD="$NEWMAN_CMD --folder $FOLDER"
fi

# Add reporters based on format
case "$FORMAT" in
    "html")
        echo -e "${YELLOW}Format: HTML Report${NC}"
        REPORT_FILE="$RESULTS_DIR/report_${TIMESTAMP}.html"
        NEWMAN_CMD="$NEWMAN_CMD --reporters cli,html --reporter-html-export $REPORT_FILE"
        ;;
    "json")
        echo -e "${YELLOW}Format: JSON Report${NC}"
        REPORT_FILE="$RESULTS_DIR/report_${TIMESTAMP}.json"
        NEWMAN_CMD="$NEWMAN_CMD --reporters cli,json --reporter-json-export $REPORT_FILE"
        ;;
    "cli"|*)
        echo -e "${YELLOW}Format: CLI Output${NC}"
        NEWMAN_CMD="$NEWMAN_CMD --reporters cli"
        ;;
esac

echo -e "${YELLOW}API URL: $(grep -A1 'base_url' $ENVIRONMENT | tail -1 | grep -oP '"\K[^"]+')${NC}"
echo ""

# Run tests
echo -e "${YELLOW}Starting tests...${NC}"
echo ""

if eval "$NEWMAN_CMD"; then
    echo ""
    echo -e "${GREEN}✓ All tests passed!${NC}"
    
    if [ -n "$REPORT_FILE" ] && [ -f "$REPORT_FILE" ]; then
        echo -e "${GREEN}Report saved: $REPORT_FILE${NC}"
    fi
    
    exit 0
else
    echo ""
    echo -e "${RED}✗ Some tests failed${NC}"
    exit 1
fi
