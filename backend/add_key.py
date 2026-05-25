from database import SessionLocal
from models import Node
import os

db = SessionLocal()
node = db.query(Node).filter(Node.id == 1).first()
if node and node.ssh_pub_key:
    auth_path = "/root/.ssh/authorized_keys"
    os.makedirs(os.path.dirname(auth_path), exist_ok=True)
    restrict = f'command="borg serve --restrict-to-path /data/borg/{node.hostname}",no-port-forwarding,no-X11-forwarding,no-pty '
    entry = f"{restrict}{node.ssh_pub_key}\n"
    with open(auth_path, "a") as f:
        f.write(entry)
    print("Successfully added to authorized_keys!")
else:
    print("Node or SSH public key not found in DB")
db.close()
