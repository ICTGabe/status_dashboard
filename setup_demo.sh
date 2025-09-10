#!/bin/bash

# Create Docker network if it doesn't exist
docker network inspect demo-net >/dev/null 2>&1 || docker network create --subnet=172.18.0.0/16 demo-net

# Remove existing containers if any
docker stop source-firewall source-sensor 2>/dev/null
docker rm source-firewall source-sensor 2>/dev/null

# Start containers with fixed SSH setup
echo "Starting demo containers with proper SSH configuration..."
docker run -d --name source-firewall --network demo-net --ip 172.18.0.2 alpine sh -c '
    apk add openssh-server && 
    ssh-keygen -A && 
    echo "root:root" | chpasswd && 
    sed -i "s/#PermitRootLogin.*/PermitRootLogin yes/" /etc/ssh/sshd_config && 
    /usr/sbin/sshd -D
'

docker run -d --name source-sensor --network demo-net --ip 172.18.0.3 alpine sh -c '
    apk add openssh-server && 
    ssh-keygen -A && 
    echo "root:root" | chpasswd && 
    sed -i "s/#PermitRootLogin.*/PermitRootLogin yes/" /etc/ssh/sshd_config && 
    /usr/sbin/sshd -D
'

echo "Waiting for containers to start..."
sleep 5

echo "Testing SSH connections..."
ssh -o StrictHostKeyChecking=no root@172.18.0.2 "echo 'Firewall SSH successful'" || echo "Firewall SSH failed"
ssh -o StrictHostKeyChecking=no root@172.18.0.3 "echo 'Sensor SSH successful'" || echo "Sensor SSH failed"

echo "Demo containers are running!"