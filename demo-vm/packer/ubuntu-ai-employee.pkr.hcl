# Packer template for AI Employee Demo VM
# Builds an Ubuntu 22.04 OVA with AI Employee pre-installed and configured
#
# Prerequisites:
#   - Packer >= 1.10: https://developer.hashicorp.com/packer/install
#   - VirtualBox >= 7.0 OR VMware Workstation/Fusion
#
# Usage:
#   # Build OVA with VirtualBox
#   packer init .
#   packer build -var "anthropic_api_key=sk-ant-..." ubuntu-ai-employee.pkr.hcl
#
#   # Build VMDK with VMware (requires vmware-iso plugin)
#   packer build -var "builder=vmware" -var "anthropic_api_key=sk-ant-..." ubuntu-ai-employee.pkr.hcl

packer {
  required_version = ">= 1.10"
  required_plugins {
    virtualbox = {
      version = ">= 1.1.1"
      source  = "github.com/hashicorp/virtualbox"
    }
  }
}

# ─── Variables ───────────────────────────────────────────────────────────────

variable "anthropic_api_key" {
  type        = string
  description = "Anthropic API key to embed in the VM (set to empty to skip)"
  default     = ""
  sensitive   = true
}

variable "vm_name" {
  type    = string
  default = "ai-employee-demo"
}

variable "vm_memory" {
  type    = number
  default = 4096  # 4 GB RAM
}

variable "vm_cpus" {
  type    = number
  default = 2
}

variable "disk_size" {
  type    = number
  default = 40960  # 40 GB
}

variable "ssh_username" {
  type    = string
  default = "demo"
}

variable "ssh_password" {
  type      = string
  default   = "demo"
  sensitive = true
}

variable "ubuntu_iso_url" {
  type    = string
  default = "https://releases.ubuntu.com/22.04/ubuntu-22.04.4-live-server-amd64.iso"
}

variable "ubuntu_iso_checksum" {
  type    = string
  default = "sha256:45f873de9f8cb637345d6e66a583762730bbea30277ef7b32c9c3bd6700a32b2"
}

# ─── Source: VirtualBox ISO ───────────────────────────────────────────────────

source "virtualbox-iso" "ai-employee" {
  vm_name          = var.vm_name
  memory           = var.vm_memory
  cpus             = var.vm_cpus
  disk_size        = var.disk_size
  headless         = true
  guest_os_type    = "Ubuntu_64"
  hard_drive_interface = "sata"

  iso_url      = var.ubuntu_iso_url
  iso_checksum = var.ubuntu_iso_checksum

  # Ubuntu autoinstall (cloud-init style)
  http_directory = "${path.root}/http"
  boot_command = [
    "<esc><esc><esc>",
    "<enter><wait>",
    "/casper/vmlinuz ",
    "initrd=/casper/initrd ",
    "autoinstall ",
    "ds=nocloud-net;seedfrom=http://{{ .HTTPIP }}:{{ .HTTPPort }}/",
    "<enter>"
  ]
  boot_wait = "5s"

  ssh_username         = var.ssh_username
  ssh_password         = var.ssh_password
  ssh_timeout          = "30m"
  ssh_handshake_attempts = 50

  shutdown_command = "sudo shutdown -P now"

  # Export as OVA
  format = "ova"
  export_opts = [
    "--manifest",
    "--vsys", "0",
    "--description", "AI Employee Demo VM - Pre-configured with Docker and AI Employee stack",
    "--version", "1.0"
  ]

  vboxmanage = [
    ["modifyvm", "{{.Name}}", "--vram", "16"],
    ["modifyvm", "{{.Name}}", "--graphicscontroller", "vmsvga"],
    ["modifyvm", "{{.Name}}", "--nat-localhostreachable1", "on"],
  ]

  # Port forward: host 8080 → guest 80 (Traefik), host 3000 → guest 3000 (Frontend)
  vboxmanage_post = [
    ["modifyvm", "{{.Name}}", "--natpf1", "traefik,tcp,,8080,,80"],
    ["modifyvm", "{{.Name}}", "--natpf1", "frontend,tcp,,3000,,3000"],
    ["modifyvm", "{{.Name}}", "--natpf1", "api,tcp,,8000,,8000"],
    ["modifyvm", "{{.Name}}", "--natpf1", "ssh,tcp,,2222,,22"],
  ]
}

# ─── Build ────────────────────────────────────────────────────────────────────

build {
  name    = "ai-employee-demo"
  sources = ["source.virtualbox-iso.ai-employee"]

  # 1. Wait for cloud-init to finish
  provisioner "shell" {
    inline = [
      "while [ ! -f /var/lib/cloud/instance/boot-finished ]; do echo 'Waiting for cloud-init...'; sleep 5; done",
      "sudo apt-get update -qq"
    ]
  }

  # 2. Install Docker Engine
  provisioner "shell" {
    script = "${path.root}/../scripts/vm-install-docker.sh"
  }

  # 3. Clone AI Employee and configure
  provisioner "shell" {
    environment_vars = [
      "ANTHROPIC_API_KEY=${var.anthropic_api_key}",
      "DEMO_USER=${var.ssh_username}"
    ]
    script = "${path.root}/../scripts/vm-setup-ai-employee.sh"
  }

  # 4. Install demo data and configure autostart
  provisioner "shell" {
    script = "${path.root}/../scripts/vm-configure-demo.sh"
  }

  # 5. Cleanup to reduce image size
  provisioner "shell" {
    script = "${path.root}/../scripts/vm-cleanup.sh"
  }
}
