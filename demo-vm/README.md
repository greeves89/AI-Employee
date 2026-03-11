# AI Employee Demo VM

Pre-configured VM image with AI Employee installed and ready to run. Two build options:

| Option | Tool | Output | Build time |
|--------|------|--------|-----------|
| [Vagrant](#option-1-vagrant-quickest) | Vagrant + VirtualBox | Running VM | ~10 min |
| [Packer](#option-2-packer-shareable-ova) | Packer + VirtualBox | Portable OVA/VMDK | ~30-45 min |

## Prerequisites

- VirtualBox 7.0+ ([download](https://www.virtualbox.org/wiki/Downloads))
- **For Vagrant**: Vagrant 2.3+ ([download](https://developer.hashicorp.com/vagrant/install))
- **For Packer**: Packer 1.10+ ([download](https://developer.hashicorp.com/packer/install))
- An Anthropic API key ([get one](https://console.anthropic.com/))

## Option 1: Vagrant (Quickest)

Fastest way to get a running demo — downloads a pre-built Ubuntu base box and provisions on top.

```bash
cd demo-vm/

# Start the VM (provisions on first run, ~10 minutes)
ANTHROPIC_API_KEY=sk-ant-... vagrant up

# AI Employee is now running at:
#   Frontend:  http://localhost:3000
#   API:       http://localhost:8000

# SSH into the VM
vagrant ssh

# Stop (but keep state)
vagrant halt

# Restart
vagrant up

# Delete completely
vagrant destroy
```

## Option 2: Packer (Shareable OVA)

Builds a portable OVA file that can be imported into VirtualBox, VMware, or Parallels. Share with teammates without them needing to provision anything.

```bash
cd demo-vm/packer/

# Initialize plugins
packer init .

# Build the OVA (~30-45 minutes)
packer build \
  -var "anthropic_api_key=sk-ant-..." \
  ubuntu-ai-employee.pkr.hcl

# Output: output-ai-employee-demo/ai-employee-demo.ova
```

### Importing the OVA

**VirtualBox:**
```bash
VBoxManage import ai-employee-demo.ova --vsys 0 --vmname "AI Employee Demo"
VBoxManage startvm "AI Employee Demo" --type headless
# Access at http://localhost:3000 (via NAT port forwarding)
```

**VMware:**
- File → Open → select the `.ova` file
- Start the VM; it will auto-start AI Employee on boot

**Command line (VMware):**
```bash
ovftool ai-employee-demo.ova ai-employee-demo.vmx
vmrun start ai-employee-demo.vmx nogui
```

## VM Credentials

| Item | Value |
|------|-------|
| Username | `demo` (Vagrant: `vagrant`) |
| Password | `demo` (Vagrant: `vagrant`) |
| SSH port (host) | `2222` (Vagrant: `vagrant ssh`) |
| Frontend | `http://localhost:3000` |
| API | `http://localhost:8000` |

## Configuring the API Key

If you built without an API key or want to change it:

```bash
# SSH into the VM
vagrant ssh   # or: ssh -p 2222 demo@localhost

# Edit the configuration
nano ~/ai-employee/.env
# Set: ANTHROPIC_API_KEY=sk-ant-...

# Restart AI Employee
cd ~/ai-employee
docker compose restart
```

## Helper Scripts

Inside the VM:

```bash
~/start-demo.sh   # Start AI Employee
~/stop-demo.sh    # Stop AI Employee

# View logs
cd ~/ai-employee && docker compose logs -f

# Check status
docker compose ps
```

## Autostart

AI Employee starts automatically on VM boot via systemd:

```bash
# Check status
systemctl status ai-employee

# Disable autostart
sudo systemctl disable ai-employee

# Re-enable autostart
sudo systemctl enable ai-employee
```

## Building Without an API Key

You can build the VM without embedding an API key — users will need to add their own:

```bash
# Packer (no embedded key)
packer build ubuntu-ai-employee.pkr.hcl

# Vagrant (no embedded key)
vagrant up
```

The VM will start, but AI Employee won't function until `ANTHROPIC_API_KEY` is set in `~/ai-employee/.env`.

## Customizing the Build

Edit `packer/ubuntu-ai-employee.pkr.hcl` variables:

```hcl
# Increase memory for running more agents
variable "vm_memory" { default = 8192 }

# More CPUs for faster builds
variable "vm_cpus" { default = 4 }
```

Or pass at build time:
```bash
packer build \
  -var "vm_memory=8192" \
  -var "vm_cpus=4" \
  -var "anthropic_api_key=sk-ant-..." \
  ubuntu-ai-employee.pkr.hcl
```
