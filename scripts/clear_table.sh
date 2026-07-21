#!/bin/zsh

set -e

SCRIPT_DIR="${0:A:h}"
DB_PATH="$SCRIPT_DIR/../src/app/production.db"

# 1. Check if database file exists
if [[ ! -f "$DB_PATH" ]]; then
    echo "❌ Error: Database file not found at: $DB_PATH"
    exit 1
fi

# 2. List available tables
echo "Available tables:"
sqlite3 "$DB_PATH" ".tables"
echo "---------------------------------------------------------------------"

# 3. Get user input for table name
print -n "Enter the name of the table you want to CLEAR: "
read -r TABLE_NAME

if [[ -z "$TABLE_NAME" ]]; then
    echo "❌ No table name specified. Exiting."
    exit 1
fi

# 4. Verify the table actually exists in the database
TABLE_EXISTS=$(sqlite3 "$DB_PATH" "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='$TABLE_NAME';")
if [[ "$TABLE_EXISTS" -eq 0 ]]; then
    echo "❌ Error: Table '$TABLE_NAME' does not exist."
    exit 1
fi

# 5. Double-confirm destructive action
print -n "⚠️ ARE YOU SURE? This will delete all entries inside '$TABLE_NAME'. Type 'YES' to confirm: "
read -r CONFIRM

# Convert input to lowercase for a case-insensitive check
if [[ "${CONFIRM:l}" == "yes" ]]; then
    sqlite3 "$DB_PATH" "DELETE FROM \"$TABLE_NAME\";"
    sqlite3 "$DB_PATH" "VACUUM;"
    echo "✅ All entries inside table '$TABLE_NAME' have been cleared successfully."
else
    echo "❌ Operation cancelled. Entries preserved."
fi
