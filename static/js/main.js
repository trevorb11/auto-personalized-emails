/**
 * Main JavaScript for Auto Personalized Emails
 * Handles connection status checking and UI interactions
 */

// Check connection status on page load
document.addEventListener('DOMContentLoaded', async () => {
    await checkConnectionStatus();
});

/**
 * Check Gmail connection status and update UI
 */
async function checkConnectionStatus() {
    const statusElement = document.getElementById('connection-status');
    if (!statusElement) return;

    try {
        const response = await fetch('/status');
        const data = await response.json();

        if (data.connected) {
            statusElement.textContent = 'Connected';
            statusElement.classList.remove('disconnected');
            statusElement.classList.add('connected');
        } else {
            statusElement.textContent = 'Not Connected';
            statusElement.classList.remove('connected');
            statusElement.classList.add('disconnected');
        }
    } catch (error) {
        statusElement.textContent = 'Error';
        statusElement.classList.add('disconnected');
        console.error('Failed to check connection status:', error);
    }
}

/**
 * Show a toast notification
 * @param {string} message - The message to display
 * @param {string} type - Type of notification: 'success', 'error', 'info'
 */
function showToast(message, type = 'info') {
    // Remove existing toasts
    const existingToast = document.querySelector('.toast');
    if (existingToast) {
        existingToast.remove();
    }

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;

    // Add styles dynamically
    toast.style.cssText = `
        position: fixed;
        bottom: 20px;
        right: 20px;
        padding: 1rem 1.5rem;
        border-radius: 8px;
        color: white;
        font-weight: 500;
        z-index: 1001;
        animation: slideIn 0.3s ease;
    `;

    switch (type) {
        case 'success':
            toast.style.backgroundColor = '#34a853';
            break;
        case 'error':
            toast.style.backgroundColor = '#ea4335';
            break;
        default:
            toast.style.backgroundColor = '#4285f4';
    }

    document.body.appendChild(toast);

    // Remove after 3 seconds
    setTimeout(() => {
        toast.style.animation = 'slideOut 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

/**
 * Format a number with commas
 * @param {number} num - The number to format
 * @returns {string} Formatted number
 */
function formatNumber(num) {
    return num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

/**
 * Truncate text to a maximum length
 * @param {string} text - The text to truncate
 * @param {number} maxLength - Maximum length
 * @returns {string} Truncated text
 */
function truncateText(text, maxLength = 100) {
    if (!text || text.length <= maxLength) return text;
    return text.substring(0, maxLength) + '...';
}

/**
 * Validate email format
 * @param {string} email - Email address to validate
 * @returns {boolean} True if valid
 */
function isValidEmail(email) {
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return emailRegex.test(email);
}

/**
 * Debounce function for performance
 * @param {Function} func - Function to debounce
 * @param {number} wait - Wait time in ms
 * @returns {Function} Debounced function
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Add CSS animations
const style = document.createElement('style');
style.textContent = `
    @keyframes slideIn {
        from {
            transform: translateX(100%);
            opacity: 0;
        }
        to {
            transform: translateX(0);
            opacity: 1;
        }
    }

    @keyframes slideOut {
        from {
            transform: translateX(0);
            opacity: 1;
        }
        to {
            transform: translateX(100%);
            opacity: 0;
        }
    }
`;
document.head.appendChild(style);
