"""
Auto Personalized Emails - A Replit-hosted application for sending personalized bulk emails
using Replit's native Gmail integration.

Supports the following Gmail API scopes:
- gmail.addons.current.message.metadata
- gmail.send
- gmail.addons.current.message.action
- gmail.labels
- gmail.addons.current.message.readonly
- gmail.addons.current.action.compose
"""

import os
import csv
import io
import base64
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
import requests

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')

# Replit Gmail Integration Configuration
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"

def get_gmail_token():
    """
    Get the Gmail access token from Replit's native integration.
    Replit provides OAuth tokens through environment variables or their connector system.
    """
    # Replit stores connector tokens in environment variables
    # The token is typically available as REPLIT_GMAIL_TOKEN or through the Replit DB
    token = os.environ.get('GMAIL_ACCESS_TOKEN') or os.environ.get('REPLIT_GMAIL_TOKEN')

    # Alternative: Check for Replit's connector authentication
    if not token:
        # Try to get from Replit's secrets/connector system
        try:
            from replit import db
            token = db.get('gmail_token')
        except:
            pass

    return token

def get_gmail_headers():
    """Get authorization headers for Gmail API requests."""
    token = get_gmail_token()
    if not token:
        return None
    return {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }

def create_message(sender, to, subject, body, is_html=False):
    """
    Create a message for the Gmail API.

    Args:
        sender: Email address of the sender
        to: Email address of the recipient
        subject: Subject line of the email
        body: Body content of the email
        is_html: Whether the body is HTML content

    Returns:
        A base64url encoded email message
    """
    if is_html:
        message = MIMEMultipart('alternative')
        message['to'] = to
        message['from'] = sender
        message['subject'] = subject

        # Create plain text and HTML versions
        text_part = MIMEText(body, 'plain')
        html_part = MIMEText(body, 'html')

        message.attach(text_part)
        message.attach(html_part)
    else:
        message = MIMEText(body)
        message['to'] = to
        message['from'] = sender
        message['subject'] = subject

    # Encode the message
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
    return {'raw': raw}

def send_email(to, subject, body, sender_email=None, is_html=False):
    """
    Send an email using the Gmail API.

    Args:
        to: Recipient email address
        subject: Email subject
        body: Email body content
        sender_email: Sender email (uses 'me' if not provided)
        is_html: Whether body is HTML

    Returns:
        Tuple of (success: bool, message: str)
    """
    headers = get_gmail_headers()
    if not headers:
        return False, "Gmail not connected. Please connect your Gmail account in Replit."

    # Get sender email if not provided
    if not sender_email:
        sender_email = "me"

    # Create the message
    message = create_message(sender_email, to, subject, body, is_html)

    # Send via Gmail API
    try:
        response = requests.post(
            f"{GMAIL_API_BASE}/users/me/messages/send",
            headers=headers,
            json=message
        )

        if response.status_code == 200:
            result = response.json()
            return True, f"Email sent successfully. Message ID: {result.get('id', 'unknown')}"
        else:
            error_data = response.json()
            error_msg = error_data.get('error', {}).get('message', 'Unknown error')
            return False, f"Failed to send email: {error_msg}"
    except Exception as e:
        return False, f"Error sending email: {str(e)}"

def get_user_profile():
    """Get the authenticated user's Gmail profile."""
    headers = get_gmail_headers()
    if not headers:
        return None

    try:
        response = requests.get(
            f"{GMAIL_API_BASE}/users/me/profile",
            headers=headers
        )
        if response.status_code == 200:
            return response.json()
    except:
        pass
    return None

def get_labels():
    """Get Gmail labels for the authenticated user."""
    headers = get_gmail_headers()
    if not headers:
        return []

    try:
        response = requests.get(
            f"{GMAIL_API_BASE}/users/me/labels",
            headers=headers
        )
        if response.status_code == 200:
            return response.json().get('labels', [])
    except:
        pass
    return []

def parse_csv(file_content, encoding='utf-8'):
    """
    Parse CSV content and extract email data.

    Expected columns:
    - email (required): Recipient email address
    - subject (required): Email subject line
    - message/body (required): Email body content
    - name (optional): Recipient name for personalization
    - Any other columns can be used as template variables

    Returns:
        Tuple of (success: bool, data: list or error message, columns: list)
    """
    try:
        # Decode if bytes
        if isinstance(file_content, bytes):
            file_content = file_content.decode(encoding)

        # Parse CSV
        reader = csv.DictReader(io.StringIO(file_content))
        columns = reader.fieldnames

        if not columns:
            return False, "CSV file is empty or has no headers", []

        # Normalize column names (lowercase, strip whitespace)
        normalized_columns = {col.lower().strip(): col for col in columns}

        # Check for required columns
        email_col = None
        subject_col = None
        message_col = None

        for normalized, original in normalized_columns.items():
            if normalized in ['email', 'email_address', 'recipient', 'to']:
                email_col = original
            elif normalized in ['subject', 'subject_line', 'email_subject']:
                subject_col = original
            elif normalized in ['message', 'body', 'content', 'email_body', 'text']:
                message_col = original

        if not email_col:
            return False, "Missing required column: 'email' (or 'email_address', 'recipient', 'to')", columns
        if not subject_col:
            return False, "Missing required column: 'subject' (or 'subject_line', 'email_subject')", columns
        if not message_col:
            return False, "Missing required column: 'message' (or 'body', 'content', 'email_body', 'text')", columns

        # Parse rows
        data = []
        for i, row in enumerate(reader, start=2):  # Start at 2 to account for header row
            email = row.get(email_col, '').strip()
            subject = row.get(subject_col, '').strip()
            message = row.get(message_col, '').strip()

            if not email:
                continue  # Skip rows without email

            # Validate email format (basic check)
            if '@' not in email or '.' not in email:
                continue  # Skip invalid emails

            data.append({
                'row': i,
                'email': email,
                'subject': subject,
                'message': message,
                'extra_fields': {k: v for k, v in row.items() if k not in [email_col, subject_col, message_col]}
            })

        if not data:
            return False, "No valid email entries found in the CSV", columns

        return True, data, columns

    except Exception as e:
        return False, f"Error parsing CSV: {str(e)}", []

def apply_template_variables(text, variables):
    """
    Replace template variables in text with actual values.

    Supports formats:
    - {{variable_name}}
    - {variable_name}
    - [[variable_name]]
    """
    if not text or not variables:
        return text

    result = text
    for key, value in variables.items():
        if value:
            # Support multiple template formats
            result = result.replace(f'{{{{{key}}}}}', str(value))  # {{var}}
            result = result.replace(f'{{{key}}}', str(value))  # {var}
            result = result.replace(f'[[{key}]]', str(value))  # [[var]]

    return result

# Routes

@app.route('/')
def index():
    """Main page - Upload CSV and configure email campaign."""
    profile = get_user_profile()
    is_connected = profile is not None
    user_email = profile.get('emailAddress', '') if profile else ''

    return render_template('index.html',
                         is_connected=is_connected,
                         user_email=user_email)

@app.route('/upload', methods=['POST'])
def upload_csv():
    """Handle CSV file upload and return preview data."""
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded'})

    file = request.files['file']

    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected'})

    if not file.filename.endswith('.csv'):
        return jsonify({'success': False, 'error': 'Please upload a CSV file'})

    try:
        content = file.read()
        success, data, columns = parse_csv(content)

        if not success:
            return jsonify({'success': False, 'error': data})

        # Store in session for later use
        session['email_data'] = data
        session['columns'] = columns

        # Return preview (first 5 rows)
        preview = data[:5]

        return jsonify({
            'success': True,
            'total_count': len(data),
            'preview': preview,
            'columns': columns
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/preview')
def preview():
    """Preview page - Show emails before sending."""
    if 'email_data' not in session:
        flash('Please upload a CSV file first', 'error')
        return redirect(url_for('index'))

    data = session.get('email_data', [])
    profile = get_user_profile()
    user_email = profile.get('emailAddress', '') if profile else 'Not connected'

    return render_template('preview.html',
                         emails=data,
                         total_count=len(data),
                         user_email=user_email)

@app.route('/send', methods=['POST'])
def send_emails():
    """Send all emails from the uploaded CSV."""
    if 'email_data' not in session:
        return jsonify({'success': False, 'error': 'No email data found. Please upload a CSV first.'})

    data = session.get('email_data', [])
    is_html = request.json.get('is_html', False) if request.is_json else False

    results = {
        'success': [],
        'failed': []
    }

    for entry in data:
        # Apply template variables to subject and message
        subject = apply_template_variables(entry['subject'], entry.get('extra_fields', {}))
        message = apply_template_variables(entry['message'], entry.get('extra_fields', {}))

        success, msg = send_email(
            to=entry['email'],
            subject=subject,
            body=message,
            is_html=is_html
        )

        if success:
            results['success'].append({
                'email': entry['email'],
                'message': msg
            })
        else:
            results['failed'].append({
                'email': entry['email'],
                'error': msg
            })

    # Clear session data after sending
    session.pop('email_data', None)
    session.pop('columns', None)

    return jsonify({
        'success': True,
        'results': results,
        'total_sent': len(results['success']),
        'total_failed': len(results['failed'])
    })

@app.route('/send-single', methods=['POST'])
def send_single_email():
    """Send a single test email."""
    data = request.get_json()

    if not data:
        return jsonify({'success': False, 'error': 'No data provided'})

    to = data.get('to', '').strip()
    subject = data.get('subject', '').strip()
    body = data.get('body', '').strip()
    is_html = data.get('is_html', False)

    if not to or not subject or not body:
        return jsonify({'success': False, 'error': 'Missing required fields: to, subject, body'})

    success, msg = send_email(to, subject, body, is_html=is_html)

    return jsonify({
        'success': success,
        'message': msg
    })

@app.route('/status')
def status():
    """Check Gmail connection status."""
    profile = get_user_profile()
    labels = get_labels() if profile else []

    return jsonify({
        'connected': profile is not None,
        'profile': profile,
        'labels_count': len(labels)
    })

@app.route('/labels')
def list_labels():
    """Get all Gmail labels."""
    labels = get_labels()
    return jsonify({
        'success': True,
        'labels': labels
    })

@app.route('/clear')
def clear_session():
    """Clear uploaded data from session."""
    session.pop('email_data', None)
    session.pop('columns', None)
    flash('Session data cleared', 'info')
    return redirect(url_for('index'))

# Error handlers

@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', error='Page not found'), 404

@app.errorhandler(500)
def server_error(e):
    return render_template('error.html', error='Internal server error'), 500

if __name__ == '__main__':
    # Use environment port for Replit compatibility
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
