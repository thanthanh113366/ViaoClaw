// Logging function
export function log(message, type = 'info') {
    // Split message by newlines
    const lines = message.split('\n');
    const now = new Date();
    // const timestamp = `[${now.toLocaleTimeString()}] `;
    const timestamp = `[${now.toLocaleTimeString()}.${now.getMilliseconds().toString().padStart(3, '0')}] `;

    // Check if log container exists
    const logContainer = document.getElementById('logContainer');
    if (!logContainer) {
        // If log container does not exist, output to console only
        console.log(`[${type.toUpperCase()}] ${message}`);
        return;
    }

    // Create a log entry for each line
    lines.forEach((line, index) => {
        const logEntry = document.createElement('div');
        logEntry.className = `log-entry log-${type}`;
        // Show timestamp for the first log entry
        const prefix = index === 0 ? timestamp : ' '.repeat(timestamp.length);
        logEntry.textContent = `${prefix}${line}`;
        // logEntry.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
        // logEntry.style preserves leading whitespace
        logEntry.style.whiteSpace = 'pre';
        if (type === 'error') {
            logEntry.style.color = 'red';
        } else if (type === 'debug') {
            logEntry.style.color = 'gray';
            return;
        } else if (type === 'warning') {
            logEntry.style.color = 'orange';
        } else if (type === 'success') {
            logEntry.style.color = 'green';
        } else {
            logEntry.style.color = 'black';
        }
        logContainer.appendChild(logEntry);
    });

    logContainer.scrollTop = logContainer.scrollHeight;
}