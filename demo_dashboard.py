from flask import Flask, jsonify, render_template, request
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import generate_password_hash, check_password_hash
import paramiko
import socket
from datetime import datetime
from functools import wraps

app = Flask(__name__)
auth = HTTPBasicAuth()

# Authentication setup
users = {
    "soc_analyst": generate_password_hash("securepassword123"),
    "soc_manager": generate_password_hash("managerpassword456")
}

# Client configuration with your Docker containers
clients = [
    {
        "id": 1,
        "name": "Client A",
        "contact_email": "clienta@example.com",
        "servers": [
            {"name": "Firewall", "ip": "172.18.0.2", "username": "root", "password": "root", "status": "unknown"},
            {"name": "Sensor", "ip": "172.18.0.3", "username": "root", "password": "root", "status": "unknown"}
        ]
    }
]

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

@app.route('/api/health', methods=['GET'])
def health_check():
    """API endpoint for health check."""
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)