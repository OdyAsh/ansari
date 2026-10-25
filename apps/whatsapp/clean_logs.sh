#!/bin/bash

# This script cleans log files in the ./logs directory by removing entries older than a specified number of minutes.

# NOTE: to run in powershell, you have to prefix with `sh `
# E.g.: `sh ./logs/clean_logs.sh 5`

# Default to 0 minutes if no argument is provided
MINUTES=${1:-0}

# Current timestamp in seconds
CURRENT_TIME=$(date +%s)

# Calculate the cutoff time in seconds
if [ $MINUTES -eq 0 ]; then
    echo "Removing all log entries from files in ./logs"
else
    # Convert minutes to seconds and calculate cutoff time
    CUTOFF_TIME=$((CURRENT_TIME - MINUTES * 60))
    echo "Keeping only logs from the last $MINUTES minutes"
    echo "Current time: $(date -d @$CURRENT_TIME +'%Y-%m-%d %H:%M:%S')"
    echo "Cutoff time: $(date -d @$CUTOFF_TIME +'%Y-%m-%d %H:%M:%S')"
fi

# Process each log file
for LOG_FILE in ./logs/*.log ./logs/*.log.temp; do
    # Skip if the pattern doesn't match any files
    [ -e "$LOG_FILE" ] || continue
    
    if [ -f "$LOG_FILE" ]; then
        echo "Processing $LOG_FILE..."
        
        if [ $MINUTES -eq 0 ]; then
            # If minutes is 0, empty the file
            > "$LOG_FILE"
            echo "Cleared all entries in $LOG_FILE"
        else
            # Create a temporary file to store matching lines
            TEMP_FILE="${LOG_FILE}.temp"
            
            # Use grep to extract the timestamp and filter rows
            while IFS= read -r line; do
                # Extract timestamp from log line (format: 2025-04-10 05:01:18)
                if [[ $line =~ ^([0-9]{4}-[0-9]{2}-[0-9]{2}[[:space:]][0-9]{2}:[0-9]{2}:[0-9]{2}) ]]; then
                    LOG_TIMESTAMP="${BASH_REMATCH[1]}"
                    # Convert timestamp to seconds since epoch
                    LOG_TIME=$(date -d "$LOG_TIMESTAMP" +%s)
                    
                    # Keep line if timestamp is after the cutoff
                    if [ $LOG_TIME -ge $CUTOFF_TIME ]; then
                        echo "$line" >> "$TEMP_FILE"
                    fi
                fi
            done < "$LOG_FILE"
            
            # Replace the original file with the filtered content
            if [ -f "$TEMP_FILE" ]; then
                mv "$TEMP_FILE" "$LOG_FILE"
                echo "Kept $(wc -l < "$LOG_FILE") entries in $LOG_FILE"
            else
                > "$LOG_FILE"
                echo "No recent entries found in $LOG_FILE, file cleared"
            fi
        fi
    fi
done

echo "Log cleaning complete"