"""Add command policies.

Revision ID: i1j2k3l4m5n6
Revises: h1i2j3k4l5m6
Create Date: 2026-05-26
"""

from alembic import op
import sqlalchemy as sa


revision = "i1j2k3l4m5n6"
down_revision = "h1i2j3k4l5m6"
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
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_command_policies_agent_id", "command_policies", ["agent_id"])
    op.create_index("ix_command_policies_scope", "command_policies", ["scope"])
    op.create_index("ix_command_policies_is_active", "command_policies", ["is_active"])

    op.bulk_insert(table, [
        {"name": "Fork bomb", "pattern": r":\(\)\{.*:\|:.*\};:", "effect": "blocked", "description": "Prevents fork bomb attacks that exhaust system resources", "sort_order": 1},
        {"name": "Overwrite /etc/passwd", "pattern": r">\s*/etc/(passwd|shadow|sudoers|hosts)", "effect": "blocked", "description": "Blocks writes to critical authentication files", "sort_order": 2},
        {"name": "Overwrite /boot", "pattern": r">\s*/boot/", "effect": "blocked", "description": "Blocks writes to the boot partition", "sort_order": 3},
        {"name": "Write to main disk", "pattern": r"dd\s+.*of=/dev/(sda|nvme|xvda)", "effect": "blocked", "description": "Blocks raw writes to primary drives", "sort_order": 4},
        {"name": "Format main disk", "pattern": r"mkfs\.\w+\s+/dev/(sda|nvme|xvda)", "effect": "blocked", "description": "Blocks formatting primary drives", "sort_order": 5},

        {"name": "rm -rf root", "pattern": r"rm\s+.*(-rf?|--recursive).*\s+/[\s\*$]", "effect": "high", "description": "Recursive deletion from filesystem root", "sort_order": 10},
        {"name": "rm -rf system dirs", "pattern": r"rm\s+(-rf?|--recursive|--force).*(/home|/var|/opt|/etc|/usr)", "effect": "high", "description": "Recursive deletion in critical system directories", "sort_order": 11},
        {"name": "rm with force flags", "pattern": r"rm\s+.*\s+(-rf?|--recursive|--force)", "effect": "high", "description": "rm with recursive or force flags", "sort_order": 12},
        {"name": "dd to any device", "pattern": r"dd\s+.*of=/dev/", "effect": "high", "description": "Raw write to any device file", "sort_order": 13},
        {"name": "Format filesystem", "pattern": r"mkfs\.", "effect": "high", "description": "Creating filesystems", "sort_order": 14},
        {"name": "Partition manipulation (fdisk)", "pattern": r"fdisk", "effect": "high", "description": "Disk partition editing", "sort_order": 15},
        {"name": "Partition manipulation (parted)", "pattern": r"parted", "effect": "high", "description": "Disk partition editing", "sort_order": 16},
        {"name": "Curl pipe to shell", "pattern": r"curl.*\|\s*(bash|sh|zsh)", "effect": "high", "description": "Downloading and executing remote scripts", "sort_order": 17},
        {"name": "Wget pipe to shell", "pattern": r"wget.*\|\s*(bash|sh|zsh)", "effect": "high", "description": "Downloading and executing remote scripts", "sort_order": 18},
        {"name": "Netcat listener", "pattern": r"nc\s+-l", "effect": "high", "description": "Opening a network listener", "sort_order": 19},
        {"name": "Python HTTP server", "pattern": r"python.*-m\s+http\.server", "effect": "high", "description": "Exposing filesystem via HTTP", "sort_order": 20},
        {"name": "chmod 777", "pattern": r"chmod\s+777", "effect": "high", "description": "Setting world-writable permissions", "sort_order": 21},
        {"name": "chown root", "pattern": r"chown\s+root", "effect": "high", "description": "Changing ownership to root", "sort_order": 22},
        {"name": "Flush firewall", "pattern": r"iptables\s+-F", "effect": "high", "description": "Flushing all firewall rules", "sort_order": 23},
        {"name": "Disable firewall", "pattern": r"ufw\s+(disable|reset)", "effect": "high", "description": "Disabling the firewall", "sort_order": 24},
        {"name": "Shutdown", "pattern": r"shutdown", "effect": "high", "description": "Shutting down the system", "sort_order": 25},
        {"name": "Reboot", "pattern": r"reboot", "effect": "high", "description": "Rebooting the system", "sort_order": 26},
        {"name": "Sudo to root shell", "pattern": r"sudo\s+su\s+-", "effect": "high", "description": "Escalating to root shell", "sort_order": 27},
        {"name": "Sudo bash", "pattern": r"sudo\s+bash", "effect": "high", "description": "Running bash as root", "sort_order": 28},
        {"name": "Sudo sh", "pattern": r"sudo\s+sh", "effect": "high", "description": "Running sh as root", "sort_order": 29},
        {"name": "Edit crontab", "pattern": r"crontab\s+-e", "effect": "high", "description": "Editing scheduled tasks", "sort_order": 30},
        {"name": "Schedule with at", "pattern": r"\|\s*at\s+", "effect": "high", "description": "Scheduling deferred command execution", "sort_order": 31},
        {"name": "Global npm install", "pattern": r"npm\s+install\s+-g", "effect": "high", "description": "Installing global npm packages", "sort_order": 32},

        {"name": "rm in system dirs", "pattern": r"rm\s+.*(/etc|/var|/opt|/usr)", "effect": "medium", "description": "Deleting files in system directories", "sort_order": 50},
        {"name": "mv in system dirs", "pattern": r"mv\s+.*(/etc|/var|/opt|/usr)", "effect": "medium", "description": "Moving files in system directories", "sort_order": 51},
        {"name": "apt-get remove", "pattern": r"apt-get\s+(remove|purge)", "effect": "medium", "description": "Removing system packages", "sort_order": 52},
        {"name": "yum remove", "pattern": r"yum\s+remove", "effect": "medium", "description": "Removing system packages", "sort_order": 53},
        {"name": "dnf remove", "pattern": r"dnf\s+remove", "effect": "medium", "description": "Removing system packages", "sort_order": 54},
        {"name": "User management (useradd)", "pattern": r"useradd", "effect": "medium", "description": "Creating system users", "sort_order": 55},
        {"name": "User management (usermod)", "pattern": r"usermod", "effect": "medium", "description": "Modifying system users", "sort_order": 56},
        {"name": "User management (passwd)", "pattern": r"passwd", "effect": "medium", "description": "Changing passwords", "sort_order": 57},
        {"name": "systemctl stop/disable", "pattern": r"systemctl\s+(stop|disable|mask)", "effect": "medium", "description": "Stopping or disabling services", "sort_order": 58},
        {"name": "service stop", "pattern": r"service\s+\w+\s+stop", "effect": "medium", "description": "Stopping services", "sort_order": 59},
        {"name": "Network config (ifconfig)", "pattern": r"ifconfig", "effect": "medium", "description": "Changing network configuration", "sort_order": 60},
        {"name": "Network config (ip)", "pattern": r"ip\s+(addr|route|link)", "effect": "medium", "description": "Changing network configuration", "sort_order": 61},
        {"name": "Docker destructive ops", "pattern": r"docker\s+(rm|rmi|system\s+prune)", "effect": "medium", "description": "Removing Docker containers/images", "sort_order": 62},
        {"name": "Docker privileged", "pattern": r"docker.*--privileged", "effect": "medium", "description": "Running Docker with elevated privileges", "sort_order": 63},
        {"name": "git reset --hard", "pattern": r"git\s+reset\s+--hard", "effect": "medium", "description": "Discarding uncommitted changes", "sort_order": 64},
        {"name": "git clean -df", "pattern": r"git\s+clean\s+-[df]", "effect": "medium", "description": "Deleting untracked files", "sort_order": 65},
        {"name": "git push --force", "pattern": r"git\s+push\s+--force", "effect": "medium", "description": "Force-pushing history", "sort_order": 66},
    ])


def downgrade() -> None:
    op.drop_index("ix_command_policies_is_active", table_name="command_policies")
    op.drop_index("ix_command_policies_scope", table_name="command_policies")
    op.drop_index("ix_command_policies_agent_id", table_name="command_policies")
    op.drop_table("command_policies")
