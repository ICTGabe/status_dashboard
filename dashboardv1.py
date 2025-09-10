from flask import Flask, jsonify, render_template, request
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import pytz
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import paramiko
import socket
import json
import os
from functools import wraps

app = Flask(__name__)
auth = HTTPBasicAuth()

# Configuration
app.config['SECRET_KEY'] = 'pinewood-soc-secret-key-2024'
app.config['SMTP_SERVER'] = 'smtp.gmail.com'
app.config['SMTP_PORT'] = 587
app.config['SMTP_USERNAME'] = 'soc@pinewood.nl'
app.config['SMTP_PASSWORD'] = 'your-smtp-password'

# In-memory storage (replace with database in production)
users = {
    "soc_analyst": generate_password_hash("securepassword123"),
    "soc_manager": generate_password_hash("managerpassword456")
}

clients = [
    {
        "id": 1,
        "name": "Client A",
        "contact_email": "clienta@example.com",
        "servers": [
            {"name": "Firewall", "ip": "172.18.0.2", "username": "root", "password": "root", "status": "unknown"},
            {"name": "Sensor", "ip": "172.18.0.3", "username": "root", "password": "root", "status": "unknown"}
        ]
    },
    {
        "id": 2, 
        "name": "Client B",
        "contact_email": "clientb@example.com",
        "servers": [
            {"name": "Web Server", "ip": "172.18.0.4", "username": "root", "password": "root", "status": "unknown"},
            {"name": "Database", "ip": "172.18.0.5", "username": "root", "password": "root", "status": "unknown"}
        ]
    }
]

scheduled_updates = []
tickets = []
update_history = []

# Initialize scheduler
scheduler = BackgroundScheduler()
scheduler.start()

# Authentication
@auth.verify_password
def verify_password(username, password):
    if username in users and check_password_hash(users.get(username), password):
        return username
    return None

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Basic '):
            return jsonify({"error": "Authentication required"}), 401
            
        # Extract and verify credentials
        from base64 import b64decode
        credentials = b64decode(auth_header[6:]).decode('utf-8')
        username, password = credentials.split(':', 1)
        
        if not verify_password(username, password):
            return jsonify({"error": "Invalid credentials"}), 401
            
        return f(*args, **kwargs)
    return decorated

# SSH function
def run_ssh_command(hostname, username, password, command):
    """Executes an SSH command and returns the output and success status."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(hostname, username=username, password=password, timeout=5)
        stdin, stdout, stderr = client.exec_command(command)
        output = stdout.read().decode()
        error = stderr.read().decode()
        client.close()
        return True, output + error
    except (paramiko.AuthenticationException, paramiko.SSHException, socket.timeout, socket.error) as e:
        return False, str(e)

# Email function
def send_email(to_email, subject, body):
    """Sends an email notification."""
    try:
        msg = MIMEMultipart()
        msg['From'] = app.config['SMTP_USERNAME']
        msg['To'] = to_email
        msg['Subject'] = subject
        
        msg.attach(MIMEText(body, 'plain'))
        
        server = smtplib.SMTP(app.config['SMTP_SERVER'], app.config['SMTP_PORT'])
        server.starttls()
        server.login(app.config['SMTP_USERNAME'], app.config['SMTP_PASSWORD'])
        text = msg.as_string()
        server.sendmail(app.config['SMTP_USERNAME'], to_email, text)
        server.quit()
        return True
    except Exception as e:
        print(f"Email sending failed: {e}")
        return False

# Update execution function
def execute_scheduled_update(update_id):
    """Executes a scheduled update."""
    update = next((u for u in scheduled_updates if u['id'] == update_id), None)
    if not update:
        return
    
    # Update status to in progress
    update['status'] = 'in_progress'
    
    # Find client
    client = next((c for c in clients if c['id'] == update['client_id']), None)
    if not client:
        update['status'] = 'failed'
        update['error'] = 'Client not found'
        return
    
    # Execute update on each server
    results = []
    for server_name in update['servers']:
        server = next((s for s in client['servers'] if s['name'] == server_name), None)
        if not server:
            results.append(f"Server {server_name} not found")
            continue
            
        # Run the update command
        success, output = run_ssh_command(
            server['ip'], server['username'], server['password'],
            update['command']
        )
        
        if success:
            results.append(f"Update successful on {server_name}: {output}")
            server['status'] = 'healthy'
        else:
            results.append(f"Update failed on {server_name}: {output}")
            server['status'] = 'critical'
    
    # Update status and results
    update['status'] = 'completed' if all('successful' in r for r in results) else 'partial'
    update['results'] = results
    update['completed_at'] = datetime.now().isoformat()
    
    # Create ticket
    create_ticket(update)
    
    # Send completion email
    if client['contact_email']:
        email_body = f"""
        Update completed for {client['name']}.
        
        Update Details:
        - Type: {update['update_type']}
        - Scheduled Time: {update['scheduled_time']}
        - Completed At: {update['completed_at']}
        - Status: {update['status']}
        
        Results:
        {chr(10).join(results)}
        
        This is an automated message from Pinewood SOC.
        """
        send_email(client['contact_email'], f"Update Completed - {client['name']}", email_body)
    
    # Add to history
    update_history.append(update.copy())

# Ticket creation function
def create_ticket(update):
    """Creates a ticket for an update."""
    client = next((c for c in clients if c['id'] == update['client_id']), None)
    if not client:
        return False
    
    ticket = {
        "id": len(tickets) + 1,
        "client_id": update['client_id'],
        "update_id": update['id'],
        "title": f"Update {update['status']} for {client['name']}",
        "description": f"Automated update {update['status']} for servers: {', '.join(update['servers'])}",
        "status": "closed" if update['status'] == 'completed' else "open",
        "created_at": datetime.now().isoformat(),
        "results": update.get('results', [])
    }
    
    tickets.append(ticket)
    return True

# Routes
@app.route('/')
def index():
    """Serve the main dashboard page."""
    return render_template('index.html')

@app.route('/api/devices', methods=['GET'])
@require_auth
def get_devices():
    """API endpoint to get the current status of all devices."""
    for client in clients:
        for server in client['servers']:
            # Check status by running a simple command like 'hostname'
            is_success, _ = run_ssh_command(server['ip'], server['username'], server['password'], 'hostname')
            server['status'] = 'healthy' if is_success else 'critical'
    
    return jsonify(clients)

@app.route('/api/device/<client_id>/<device_name>/restart', methods=['POST'])
@require_auth
def restart_service(client_id, device_name):
    """API endpoint to restart a service on a device."""
    client = next((c for c in clients if c['id'] == int(client_id)), None)
    if not client:
        return jsonify({"error": "Client not found"}), 404
        
    target_device = next((d for d in client['servers'] if d['name'] == device_name), None)
    if not target_device:
        return jsonify({"error": "Device not found"}), 404

    # Restart service command
    is_success, output = run_ssh_command(
        target_device['ip'], target_device['username'], target_device['password'],
        'echo "Service restart simulated successfully $(date)"'
    )

    if is_success:
        return jsonify({"message": f"Restart command sent to {device_name}", "output": output})
    else:
        return jsonify({"error": f"Failed to connect to {device_name}", "details": output}), 500

@app.route('/api/clients', methods=['GET'])
@require_auth
def get_clients():
    """API endpoint to get all clients."""
    return jsonify(clients)

@app.route('/api/schedule-update', methods=['POST'])
@require_auth
def schedule_update():
    """API endpoint to schedule an update."""
    data = request.json
    
    # Validate required fields
    required_fields = ['client_id', 'servers', 'scheduled_time', 'update_type', 'command']
    for field in required_fields:
        if field not in data:
            return jsonify({"error": f"Missing required field: {field}"}), 400
    
    # Create update ID
    update_id = len(scheduled_updates) + 1
    
    # Create scheduled update
    scheduled_update = {
        "id": update_id,
        "client_id": data['client_id'],
        "servers": data['servers'],
        "scheduled_time": data['scheduled_time'],
        "update_type": data['update_type'],
        "command": data['command'],
        "status": "scheduled",
        "created_at": datetime.now().isoformat()
    }
    
    scheduled_updates.append(scheduled_update)
    
    # Schedule the job
    try:
        run_date = datetime.fromisoformat(data['scheduled_time'].replace('Z', '+00:00'))
        scheduler.add_job(
            execute_scheduled_update,
            'date',
            run_date=run_date,
            args=[update_id],
            id=f"update_{update_id}"
        )
    except ValueError as e:
        return jsonify({"error": f"Invalid date format: {e}"}), 400
    
    # Find client for notification
    client = next((c for c in clients if c['id'] == data['client_id']), None)
    if client and client['contact_email']:
        # Send notification email
        email_body = f"""
        A system update has been scheduled for your servers.
        
        Update Details:
        - Scheduled Time: {data['scheduled_time']}
        - Servers: {', '.join(data['servers'])}
        - Update Type: {data['update_type']}
        
        This is an automated message from Pinewood SOC.
        """
        send_email(client['contact_email'], f"Scheduled Update Notification - {client['name']}", email_body)
    
    return jsonify({"message": "Update scheduled successfully", "update_id": update_id})

@app.route('/api/scheduled-updates', methods=['GET'])
@require_auth
def get_scheduled_updates():
    """API endpoint to get all scheduled updates."""
    return jsonify(scheduled_updates)

@app.route('/api/update-history', methods=['GET'])
@require_auth
def get_update_history():
    """API endpoint to get update history."""
    return jsonify(update_history)

@app.route('/api/tickets', methods=['GET'])
@require_auth
def get_tickets():
    """API endpoint to get all tickets."""
    return jsonify(tickets)

@app.route('/api/reports/updates', methods=['GET'])
@require_auth
def get_update_reports():
    """API endpoint to get update reports."""
    time_range = request.args.get('range', '7d')  # 7 days by default
    
    # Calculate date range
    if time_range.endswith('d'):
        days = int(time_range[:-1])
        start_date = datetime.now() - timedelta(days=days)
    else:
        start_date = datetime.now() - timedelta(days=7)
    
    # Filter updates by date
    recent_updates = [u for u in update_history if datetime.fromisoformat(u.get('completed_at', '2000-01-01')) >= start_date]
    
    # Generate report data
    report_data = {
        "total_updates": len(recent_updates),
        "successful_updates": len([u for u in recent_updates if u['status'] == 'completed']),
        "failed_updates": len([u for u in recent_updates if u['status'] in ['failed', 'partial']]),
        "updates_by_client": {},
        "updates_by_type": {}
    }
    
    for update in recent_updates:
        client = next((c for c in clients if c['id'] == update['client_id']), None)
        client_name = client['name'] if client else "Unknown"
        report_data['updates_by_client'][client_name] = report_data['updates_by_client'].get(client_name, 0) + 1
        report_data['updates_by_type'][update['update_type']] = report_data['updates_by_type'].get(update['update_type'], 0) + 1
    
    return jsonify(report_data)

@app.route('/api/health', methods=['GET'])
def health_check():
    """API endpoint for health check."""
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)