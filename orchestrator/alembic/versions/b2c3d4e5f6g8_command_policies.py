"""Add command_policies table with seed data from command_filter.py

Revision ID: b2c3d4e5f6g8
Revises: a1b2c3d4e5f7
Create Date: 2026-05-07
"""
from alembic import op
import sqlalchemy as sa


revision = "b2c3d4e5f6g8"
down_revision = "a1b2c3d4e5f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = op.create_table(
        "command_policies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column("effect", sa.String(20), nullable=False, server_default="blocked"),
        sa.Column("scope", sa.String(20), nullable=False, server_default="global"),
        sa.Column("agent_id", sa.String(32), nullable=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("100")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_command_policies_agent_id", "command_policies", ["agent_id"])
    op.create_index("ix_command_policies_scope", "command_policies", ["scope"])

    # Seed data from command_filter.py
    op.bulk_insert(table, [
        # BLOCKED (effect="blocked") — always denied, no approval possible
        {"name": "Fork bomb", "pattern": r":\(\)\{.*:\|:.*\};:", "effect": "blocked", "scope": "global", "description": "Prevents fork bomb attacks that exhaust system resources", "sort_order": 1},
        {"name": "Overwrite /etc/passwd", "pattern": r">\s*/etc/(passwd|shadow|sudoers|hosts)", "effect": "blocked", "scope": "global", "description": "Blocks writing to critical system authentication files", "sort_order": 2},
        {"name": "Overwrite /boot", "pattern": r">\s*/boot/", "effect": "blocked", "scope": "global", "description": "Blocks writing to boot partition", "sort_order": 3},
        {"name": "Write to main disk", "pattern": r"dd\s+.*of=/dev/(sda|nvme|xvda)", "effect": "blocked", "scope": "global", "description": "Blocks raw disk writes to primary drive", "sort_order": 4},
        {"name": "Format main disk", "pattern": r"mkfs\.\w+\s+/dev/(sda|nvme|xvda)", "effect": "blocked", "scope": "global", "description": "Blocks formatting the primary drive", "sort_order": 5},

        # HIGH RISK (effect="high") — requires explicit approval
        {"name": "rm -rf root", "pattern": r"rm\s+.*(-rf?|--recursive).*\s+/[\s\*$]", "effect": "high", "scope": "global", "description": "Recursive deletion from filesystem root", "sort_order": 10},
        {"name": "rm -rf system dirs", "pattern": r"rm\s+(-rf?|--recursive|--force).*(/home|/var|/opt|/etc|/usr)", "effect": "high", "scope": "global", "description": "Recursive deletion in critical system directories", "sort_order": 11},
        {"name": "rm with force flags", "pattern": r"rm\s+.*\s+(-rf?|--recursive|--force)", "effect": "high", "scope": "global", "description": "Any rm with recursive/force flags", "sort_order": 12},
        {"name": "dd to any device", "pattern": r"dd\s+.*of=/dev/", "effect": "high", "scope": "global", "description": "Raw write to any device file", "sort_order": 13},
        {"name": "Format filesystem", "pattern": r"mkfs\.", "effect": "high", "scope": "global", "description": "Creating new filesystems", "sort_order": 14},
        {"name": "Partition manipulation (fdisk)", "pattern": r"fdisk", "effect": "high", "scope": "global", "description": "Disk partition editing", "sort_order": 15},
        {"name": "Partition manipulation (parted)", "pattern": r"parted", "effect": "high", "scope": "global", "description": "Disk partition editing", "sort_order": 16},
        {"name": "Curl pipe to shell", "pattern": r"curl.*\|\s*(bash|sh|zsh)", "effect": "high", "scope": "global", "description": "Downloading and executing remote scripts", "sort_order": 17},
        {"name": "Wget pipe to shell", "pattern": r"wget.*\|\s*(bash|sh|zsh)", "effect": "high", "scope": "global", "description": "Downloading and executing remote scripts", "sort_order": 18},
        {"name": "Netcat listener", "pattern": r"nc\s+-l", "effect": "high", "scope": "global", "description": "Opening a network listener (potential reverse shell)", "sort_order": 19},
        {"name": "Python HTTP server (legacy)", "pattern": r"python.*SimpleHTTPServer", "effect": "high", "scope": "global", "description": "Exposing filesystem via HTTP", "sort_order": 20},
        {"name": "Python HTTP server", "pattern": r"python.*-m\s+http\.server", "effect": "high", "scope": "global", "description": "Exposing filesystem via HTTP", "sort_order": 21},
        {"name": "chmod 777", "pattern": r"chmod\s+777", "effect": "high", "scope": "global", "description": "Setting world-writable permissions", "sort_order": 22},
        {"name": "chown root", "pattern": r"chown\s+root", "effect": "high", "scope": "global", "description": "Changing file ownership to root", "sort_order": 23},
        {"name": "Flush firewall", "pattern": r"iptables\s+-F", "effect": "high", "scope": "global", "description": "Flushing all firewall rules", "sort_order": 24},
        {"name": "Disable firewall", "pattern": r"ufw\s+(disable|reset)", "effect": "high", "scope": "global", "description": "Disabling the firewall", "sort_order": 25},
        {"name": "Shutdown", "pattern": r"shutdown", "effect": "high", "scope": "global", "description": "Shutting down the system", "sort_order": 26},
        {"name": "Reboot", "pattern": r"reboot", "effect": "high", "scope": "global", "description": "Rebooting the system", "sort_order": 27},
        {"name": "Init shutdown/reboot", "pattern": r"init\s+[06]", "effect": "high", "scope": "global", "description": "Shutdown or reboot via init", "sort_order": 28},
        {"name": "Sudo to root shell", "pattern": r"sudo\s+su\s+-", "effect": "high", "scope": "global", "description": "Escalating to root shell", "sort_order": 29},
        {"name": "Sudo bash", "pattern": r"sudo\s+bash", "effect": "high", "scope": "global", "description": "Running bash as root", "sort_order": 30},
        {"name": "Sudo sh", "pattern": r"sudo\s+sh", "effect": "high", "scope": "global", "description": "Running sh as root", "sort_order": 31},
        {"name": "Edit crontab", "pattern": r"crontab\s+-e", "effect": "high", "scope": "global", "description": "Editing scheduled tasks (persistence mechanism)", "sort_order": 32},
        {"name": "Schedule with at", "pattern": r"\|\s*at\s+", "effect": "high", "scope": "global", "description": "Scheduling deferred command execution", "sort_order": 33},
        {"name": "Upgrade pip itself", "pattern": r"pip\s+install.*--upgrade.*pip", "effect": "high", "scope": "global", "description": "Upgrading pip (potential supply chain risk)", "sort_order": 34},
        {"name": "Global npm install", "pattern": r"npm\s+install\s+-g", "effect": "high", "scope": "global", "description": "Installing global npm packages", "sort_order": 35},

        # MEDIUM RISK (effect="medium") — approval recommended
        {"name": "rm in system dirs", "pattern": r"rm\s+.*(/etc|/var|/opt|/usr)", "effect": "medium", "scope": "global", "description": "Deleting files in system directories", "sort_order": 50},
        {"name": "mv in system dirs", "pattern": r"mv\s+.*(/etc|/var|/opt|/usr)", "effect": "medium", "scope": "global", "description": "Moving files in system directories", "sort_order": 51},
        {"name": "apt-get remove", "pattern": r"apt-get\s+(remove|purge)", "effect": "medium", "scope": "global", "description": "Removing system packages", "sort_order": 52},
        {"name": "yum remove", "pattern": r"yum\s+remove", "effect": "medium", "scope": "global", "description": "Removing system packages", "sort_order": 53},
        {"name": "dnf remove", "pattern": r"dnf\s+remove", "effect": "medium", "scope": "global", "description": "Removing system packages", "sort_order": 54},
        {"name": "User management (useradd)", "pattern": r"useradd", "effect": "medium", "scope": "global", "description": "Creating system users", "sort_order": 55},
        {"name": "User management (usermod)", "pattern": r"usermod", "effect": "medium", "scope": "global", "description": "Modifying system users", "sort_order": 56},
        {"name": "User management (passwd)", "pattern": r"passwd", "effect": "medium", "scope": "global", "description": "Changing passwords", "sort_order": 57},
        {"name": "User management (adduser)", "pattern": r"adduser", "effect": "medium", "scope": "global", "description": "Creating system users", "sort_order": 58},
        {"name": "systemctl stop/disable", "pattern": r"systemctl\s+(stop|disable|mask)", "effect": "medium", "scope": "global", "description": "Stopping or disabling system services", "sort_order": 59},
        {"name": "service stop", "pattern": r"service\s+\w+\s+stop", "effect": "medium", "scope": "global", "description": "Stopping system services", "sort_order": 60},
        {"name": "Network config (ifconfig)", "pattern": r"ifconfig", "effect": "medium", "scope": "global", "description": "Modifying network configuration", "sort_order": 61},
        {"name": "Network config (ip)", "pattern": r"ip\s+(addr|route|link)", "effect": "medium", "scope": "global", "description": "Modifying network configuration", "sort_order": 62},
        {"name": "Docker destructive ops", "pattern": r"docker\s+(rm|rmi|system\s+prune)", "effect": "medium", "scope": "global", "description": "Removing Docker containers or images", "sort_order": 63},
        {"name": "Docker privileged", "pattern": r"docker.*--privileged", "effect": "medium", "scope": "global", "description": "Running Docker with elevated privileges", "sort_order": 64},
        {"name": "git reset --hard", "pattern": r"git\s+reset\s+--hard", "effect": "medium", "scope": "global", "description": "Discarding all uncommitted changes", "sort_order": 65},
        {"name": "git clean -df", "pattern": r"git\s+clean\s+-[df]", "effect": "medium", "scope": "global", "description": "Deleting untracked files", "sort_order": 66},
        {"name": "git push --force", "pattern": r"git\s+push\s+--force", "effect": "medium", "scope": "global", "description": "Force-pushing (may overwrite remote history)", "sort_order": 67},
    ])


def downgrade() -> None:
    op.drop_index("ix_command_policies_scope")
    op.drop_index("ix_command_policies_agent_id")
    op.drop_table("command_policies")
