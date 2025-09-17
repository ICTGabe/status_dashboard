#!/bin/bash

# Create Docker network if it doesn't exist
docker network inspect demo-net >/dev/null 2>&1 || docker network create --subnet=172.18.0.0/16 demo-net

# Remove existing containers if any
docker stop source-firewall source-sensor server1 server2 2>/dev/null
docker rm source-firewall source-sensor server1 server2 2>/dev/null

# Start containers with fixed SSH setup
echo "Starting demo containers with proper SSH configuration..."

# Firewall container (Alpine)
docker run -d --name source-firewall --network demo-net --ip 172.18.0.2 alpine sh -c '
    apk add openssh-server && 
    ssh-keygen -A && 
    echo "root:root" | chpasswd && 
    sed -i "s/#PermitRootLogin.*/PermitRootLogin yes/" /etc/ssh/sshd_config && 
    /usr/sbin/sshd -D
'

# Sensor container (Alpine)
docker run -d --name source-sensor --network demo-net --ip 172.18.0.3 alpine sh -c '
    apk add openssh-server && 
    ssh-keygen -A && 
    echo "root:root" | chpasswd && 
    sed -i "s/#PermitRootLogin.*/PermitRootLogin yes/" /etc/ssh/sshd_config && 
    /usr/sbin/sshd -D
'

# First Linux server (Ubuntu with nginx)
docker run -d --name server1 --network demo-net --ip 172.18.0.4 ubuntu:20.04 sh -c '
    apt-get update && 
    DEBIAN_FRONTEND=noninteractive apt-get install -y openssh-server nginx && 
    mkdir -p /var/run/sshd && 
    echo "root:root" | chpasswd && 
    sed -i "s/#PermitRootLogin.*/PermitRootLogin yes/" /etc/ssh/sshd_config && 
    sed -i "s/UsePAM yes/UsePAM no/" /etc/ssh/sshd_config && 
    service nginx start &&
    /usr/sbin/sshd -D
'

# Second Linux server (Ubuntu with nginx)
docker run -d --name server2 --network demo-net --ip 172.18.0.5 ubuntu:20.04 sh -c '
    apt-get update && 
    DEBIAN_FRONTEND=noninteractive apt-get install -y openssh-server nginx && 
    mkdir -p /var/run/sshd && 
    echo "root:root" | chpasswd && 
    sed -i "s/#PermitRootLogin.*/PermitRootLogin yes/" /etc/ssh/sshd_config && 
    sed -i "s/UsePAM yes/UsePAM no/" /etc/ssh/sshd_config && 
    service nginx start &&
    /usr/sbin/sshd -D
'

echo "Waiting for containers to start..."
sleep 15

echo "Testing SSH connections..."
for ip in 172.18.0.2 172.18.0.3 172.18.0.4 172.18.0.5; do
    if ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 root@$ip "echo 'SSH successful to $ip'"; then
        echo "✓ SSH to $ip successful"
    else
        echo "✗ SSH to $ip failed"
    fi
done

echo "Demo containers are running!"
echo ""
echo "IP Addresses:"
echo "Firewall: 172.18.0.2"
echo "Sensor: 172.18.0.3"
echo "Server1: 172.18.0.4"
echo "Server2: 172.18.0.5"
echo ""
echo "All devices use username: root, password: root"