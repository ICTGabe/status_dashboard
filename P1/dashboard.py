from flask import Flask, jsonify, render_template, request
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import paramiko
import socket
import json
import time
from collections import deque
from functools import wraps
import os

app = Flask(__name__)
auth = HTTPBasicAuth()

def load_config():
    with open('config/config.json', 'r') as f:
        return json.load(f)

config = load_config()

# Configuration from config file
app.config['SMTP_SERVER'] = config['smtp']['server']
app.config['SMTP_PORT'] = config['smtp']['port']
app.config['SMTP_USERNAME'] = config['smtp']['username']
app.config['SMTP_PASSWORD'] = config['smtp']['password']

# Authentication setup
users = {user: generate_password_hash(password) for user, password in config['users'].items()}

# Client configuration from config file
clients = config['devices']

# Initialize scheduler
scheduler = BackgroundScheduler()

# Data storage
scheduled_updates = []
tickets = []
update_history = []

# Load scheduled updates from file if exists
def load_scheduled_updates():
    global scheduled_updates
    try:
        if os.path.exists('config/scheduled_updates.json'):
            with open('config/scheduled_updates.json', 'r') as f:
                scheduled_updates = json.load(f)
                # Re-schedule all updates
                for update in scheduled_updates:
                    if update['status'] == 'scheduled':
                        try:
                            run_date = datetime.fromisoformat(update['scheduled_time'].replace('Z', '+00:00'))
                            scheduler.add_job(
                                execute_scheduled_update,
                                'date',
                                run_date=run_date,
                                args=[update['id']],
                                id=f"update_{update['id']}"
                            )
                        except ValueError as e:
                            print(f"Error scheduling update {update['id']}: {e}")
                            update['status'] = 'failed'
    except Exception as e:
        print(f"Error loading scheduled updates: {e}")

# Save scheduled updates to file
def save_scheduled_updates():
    try:
        os.makedirs('config', exist_ok=True)
        with open('config/scheduled_updates.json', 'w') as f:
            json.dump(scheduled_updates, f, indent=2)
    except Exception as e:
        print(f"Error saving scheduled updates: {e}")

# Uptime monitoring - store status checks with timestamps
uptime_history = {}
for client in clients:
    for server in client['servers']:
        uptime_history[server['name']] = deque(maxlen=1000)  # Store last 1000 status checks

# Load uptime history from file if exists
def load_uptime_history():
    global uptime_history
    try:
        if os.path.exists('config/uptime_history.json'):
            with open('config/uptime_history.json', 'r') as f:
                saved_history = json.load(f)
                for device, records in saved_history.items():
                    if device in uptime_history:
                        uptime_history[device] = deque(records, maxlen=1000)
    except Exception as e:
        print(f"Error loading uptime history: {e}")

# Save uptime history to file
def save_uptime_history():
    try:
        os.makedirs('config', exist_ok=True)
        # Convert deques to lists for JSON serialization
        saveable_history = {}
        for device, records in uptime_history.items():
            saveable_history[device] = list(records)
        
        with open('config/uptime_history.json', 'w') as f:
            json.dump(saveable_history, f, indent=2)
    except Exception as e:
        print(f"Error saving uptime history: {e}")

# SSH function
def run_ssh_command(hostname, username, password, command):
    """Executes an SSH command and returns the output and success status."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        start_time = time.time()
        client.connect(hostname, username=username, password=password, timeout=5)
        stdin, stdout, stderr = client.exec_command(command)
        output = stdout.read().decode()
        error = stderr.read().decode()
        client.close()
        response_time = round((time.time() - start_time) * 1000, 2)  # Convert to ms
        return True, output + error, response_time
    except (paramiko.AuthenticationException, paramiko.SSHException, socket.timeout, socket.error) as e:
        return False, str(e), 0

# Start background job to monitor device status
def monitor_devices():
    """Background job to monitor device status and track uptime"""
    for client in clients:
        for server in client['servers']:
            # Check status by running a simple command like 'hostname'
            is_success, _, response_time = run_ssh_command(server['ip'], server['username'], server['password'], 'hostname')
            status = 'healthy' if is_success else 'critical'
            server['status'] = status
            
            # Record status with timestamp
            uptime_history[server['name']].append({
                'timestamp': datetime.now().isoformat(),
                'status': status,
                'response_time': response_time
            })
    
    # Save uptime history to file
    save_uptime_history()

# Schedule device monitoring to run every 5 minutes
scheduler.add_job(monitor_devices, 'interval', minutes=5, id='device_monitoring')

# Authentication function
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
            
        from base64 import b64decode
        credentials = b64decode(auth_header[6:]).decode('utf-8')
        username, password = credentials.split(':', 1)
        
        if not verify_password(username, password):
            return jsonify({"error": "Invalid credentials"}), 401
            
        return f(*args, **kwargs)
    return decorated

# Email function using free SMTP service
def send_email(to_email, subject, body):
    """Sends an email notification using free SMTP service."""
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
    save_scheduled_updates()
    
    # Find client
    client = next((c for c in clients if c['id'] == update['client_id']), None)
    if not client:
        update['status'] = 'failed'
        update['error'] = 'Client not found'
        save_scheduled_updates()
        return
    
    # Execute update on each server
    results = []
    for server_name in update['servers']:
        server = next((s for s in client['servers'] if s['name'] == server_name), None)
        if not server:
            results.append(f"Server {server_name} not found")
            continue
            
        # Run the update command
        success, output, response_time = run_ssh_command(
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
    save_scheduled_updates()
    
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
            is_success, _, response_time = run_ssh_command(server['ip'], server['username'], server['password'], 'hostname')
            server['status'] = 'healthy' if is_success else 'critical'
            server['response_time'] = response_time
    
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
    is_success, output, response_time = run_ssh_command(
        target_device['ip'], target_device['username'], target_device['password'],
        'sudo systemctl reboot'
    )

    if is_success:
        # Send notification email
        email_body = f"Service restarted successfully on {device_name} at {datetime.now()}"
        send_email(client['contact_email'], f"Service Restart - {device_name}", email_body)
        
        return jsonify({"message": f"Restart command sent to {device_name}", "output": output})
    else:
        return jsonify({"error": f"Failed to connect to {device_name}", "details": output}), 500

@app.route('/api/device/<client_id>/<device_name>/shutdown', methods=['POST'])
@require_auth
def shutdown_device(client_id, device_name):
    """API endpoint to shutdown a device."""
    client = next((c for c in clients if c['id'] == int(client_id)), None)
    if not client:
        return jsonify({"error": "Client not found"}), 404
        
    target_device = next((d for d in client['servers'] if d['name'] == device_name), None)
    if not target_device:
        return jsonify({"error": "Device not found"}), 404

    # Shutdown command
    is_success, output, response_time = run_ssh_command(
        target_device['ip'], target_device['username'], target_device['password'],
        'sudo shutdown -h now'
    )

    if is_success:
        # Send notification email
        email_body = f"Device shutdown initiated on {device_name} at {datetime.now()}"
        send_email(client['contact_email'], f"Device Shutdown - {device_name}", email_body)
        
        return jsonify({"message": f"Shutdown command sent to {device_name}", "output": output})
    else:
        return jsonify({"error": f"Failed to connect to {device_name}", "details": output}), 500

@app.route('/api/device/<client_id>/<device_name>/fix', methods=['POST'])
@require_auth
def common_fix(client_id, device_name):
    """API endpoint to apply a common fix to a device."""
    client = next((c for c in clients if c['id'] == int(client_id)), None)
    if not client:
        return jsonify({"error": "Client not found"}), 404
        
    target_device = next((d for d in client['servers'] if d['name'] == device_name), None)
    if not target_device:
        return jsonify({"error": "Device not found"}), 404

    # Get current time for filename
    now = datetime.now()
    timestamp = now.strftime("%M_%S::%d_%m")  # Minutes_Seconds::Day_Month
    
    # Create a fix file on the device
    is_success, output, response_time = run_ssh_command(
        target_device['ip'], target_device['username'], target_device['password'],
        f'echo "Fixed at $(date)" > /tmp/fixed_{timestamp}.txt && echo "Fix file created: /tmp/fixed_{timestamp}.txt"'
    )

    if is_success:
        # Send notification email
        email_body = f"Common fix applied to {device_name} at {datetime.now()}. Fix file created: /tmp/fixed_{timestamp}.txt"
        send_email(client['contact_email'], f"Common Fix Applied - {device_name}", email_body)
        
        return jsonify({"message": f"Common fix applied to {device_name}", "output": output})
    else:
        return jsonify({"error": f"Failed to apply fix to {device_name}", "details": output}), 500

@app.route('/api/device/<client_id>/<device_name>/alert', methods=['POST'])
@require_auth
def create_alert(client_id, device_name):
    """API endpoint to create an alert for a device."""
    client = next((c for c in clients if c['id'] == int(client_id)), None)
    if not client:
        return jsonify({"error": "Client not found"}), 404
        
    target_device = next((d for d in client['servers'] if d['name'] == device_name), None)
    if not target_device:
        return jsonify({"error": "Device not found"}), 404

    # For now, just create a simple alert file
    is_success, output, response_time = run_ssh_command(
        target_device['ip'], target_device['username'], target_device['password'],
        f'echo "Alert created at $(date)" > /tmp/alert_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt'
    )

    if is_success:
        return jsonify({"message": f"Alert created for {device_name}", "output": output})
    else:
        return jsonify({"error": f"Failed to create alert for {device_name}", "details": output}), 500

@app.route('/api/uptime/<device_name>', methods=['GET'])
@require_auth
def get_uptime_data(device_name):
    """API endpoint to get uptime history for a device."""
    if device_name not in uptime_history:
        return jsonify({"error": "Device not found"}), 404
    
    # Get time range from query parameters (default: 24 hours)
    hours = int(request.args.get('hours', 24))
    since_time = datetime.now() - timedelta(hours=hours)
    
    # Filter data by time range
    filtered_data = []
    for record in uptime_history[device_name]:
        record_time = datetime.fromisoformat(record['timestamp'])
        if record_time >= since_time:
            filtered_data.append(record)
    
    # Calculate uptime statistics
    total_checks = len(filtered_data)
    if total_checks == 0:
        return jsonify({
            "device": device_name,
            "data": [],
            "stats": {
                "uptime_percentage": 0,
                "downtime_percentage": 0,
                "total_checks": 0,
                "healthy_checks": 0,
                "critical_checks": 0
            }
        })
    
    healthy_checks = sum(1 for record in filtered_data if record['status'] == 'healthy')
    uptime_percentage = (healthy_checks / total_checks) * 100
    
    return jsonify({
        "device": device_name,
        "data": filtered_data,
        "stats": {
            "uptime_percentage": round(uptime_percentage, 2),
            "downtime_percentage": round(100 - uptime_percentage, 2),
            "total_checks": total_checks,
            "healthy_checks": healthy_checks,
            "critical_checks": total_checks - healthy_checks
        }
    })

@app.route('/api/uptime', methods=['GET'])
@require_auth
def get_all_uptime_data():
    """API endpoint to get uptime history for all devices."""
    hours = int(request.args.get('hours', 24))
    since_time = datetime.now() - timedelta(hours=hours)
    
    result = {}
    for device_name in uptime_history:
        # Filter data by time range
        filtered_data = []
        for record in uptime_history[device_name]:
            record_time = datetime.fromisoformat(record['timestamp'])
            if record_time >= since_time:
                filtered_data.append(record)
        
        # Calculate uptime statistics
        total_checks = len(filtered_data)
        healthy_checks = sum(1 for record in filtered_data if record['status'] == 'healthy')
        uptime_percentage = (healthy_checks / total_checks) * 100 if total_checks > 0 else 0
        
        result[device_name] = {
            "data": filtered_data,
            "stats": {
                "uptime_percentage": round(uptime_percentage, 2),
                "downtime_percentage": round(100 - uptime_percentage, 2),
                "total_checks": total_checks,
                "healthy_checks": healthy_checks,
                "critical_checks": total_checks - healthy_checks
            }
        }
    
    return jsonify(result)

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
    save_scheduled_updates()
    
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

@app.route('/api/run-update/<update_id>', methods=['POST'])
@require_auth
def run_update_now(update_id):
    """API endpoint to run a scheduled update immediately."""
    update_id = int(update_id)
    execute_scheduled_update(update_id)
    return jsonify({"message": f"Update {update_id} executed"})

@app.route('/api/delete-update/<update_id>', methods=['DELETE'])
@require_auth
def delete_scheduled_update(update_id):
    """API endpoint to delete a scheduled update."""
    update_id = int(update_id)
    global scheduled_updates
    scheduled_updates = [u for u in scheduled_updates if u['id'] != update_id]
    
    # Also remove the job from the scheduler if it exists
    try:
        scheduler.remove_job(f"update_{update_id}")
    except:
        pass
    
    save_scheduled_updates()
    return jsonify({"message": f"Update {update_id} deleted"})

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
    # Load saved data
    load_scheduled_updates()
    load_uptime_history()
    
    # Run initial device monitoring
    monitor_devices()
    
    # Start the scheduler only if it's not already running
    if not scheduler.running:
        scheduler.start()
    
    # Run the Flask app
    app.run(debug=True, host='0.0.0.0', port=5000)