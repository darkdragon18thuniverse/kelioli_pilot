#!/bin/zsh

# Resolve database path relative to the script location
SCRIPT_DIR="${0:A:h}"
DB_PATH="$SCRIPT_DIR/../src/app/production.db"

if [[ ! -f "$DB_PATH" ]]; then
    echo "❌ Error: Database file not found at: $DB_PATH"
    exit 1
fi

echo "Available tables in the database:"
sqlite3 "$DB_PATH" ".tables"
echo "---------------------------------------------------------------------"

echo -n "Enter the name of the table you want to view: "
read TABLE_NAME

if [[ -z "$TABLE_NAME" ]]; then
    echo "❌ No table name specified. Exiting."
    exit 1
fi

echo "====================================================================="
echo " DATA PREVIEW FOR TABLE: $TABLE_NAME "
echo "====================================================================="

sqlite3 "$DB_PATH" <<EOF
.headers on
.mode column
SELECT * FROM "$TABLE_NAME" LIMIT 50;
EOF

echo "====================================================================="