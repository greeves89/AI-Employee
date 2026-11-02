"""Prüfregeln des Docker-Wächters (P1-13).

Die Körper entstehen mit der echten Serialisierung des Docker-SDK (``HostConfig``),
genau wie die Anfragen, die der Orchestrator schickt.
"""

import unittest

from docker.types import HostConfig, Mount

from app.docker_waechter import regeln

API = "1.45"
ERLAUBT = ["/srv/secondbrain", "/mnt/nas/documents"]


def _anlegen(**hostconfig):
    return {"Image": "ai-employee-agent:latest", "HostConfig": dict(HostConfig(version=API, **hostconfig))}


def _pruefen(**hostconfig):
    return regeln.container_anlegen(_anlegen(**hostconfig), ERLAUBT)


class WasDiePlattformAnlegt(unittest.TestCase):
    def test_agent_container_wie_docker_service(self):
        """Dieselben Einstellungen wie docker_service.create_container."""
        grund = _pruefen(
            binds={"workspace-a1": {"bind": "/workspace", "mode": "rw"},
                   "build-tools-a1": {"bind": "/opt/build-tools", "mode": "rw"},
                   "/srv/secondbrain/projekt-1": {"bind": "/mnt/brains/projekt-1", "mode": "rw"},
                   "/mnt/nas/documents": {"bind": "/mnt/documents", "mode": "ro"}},
            mounts=[Mount(target="/shared", source="ai-employee-shared", type="volume")],
            cap_drop=["ALL"], cap_add=["CHOWN", "SETUID", "SETGID", "DAC_OVERRIDE", "FOWNER", "KILL"],
            security_opt=["no-new-privileges:true", 'seccomp={"defaultAction":"SCMP_ACT_ERRNO"}'],
            init=True, shm_size="512m", pids_limit=512, mem_limit="2g", network_mode="ai-employee-network",
        )
        self.assertIsNone(grund)

    def test_hilfscontainer_und_runner_ohne_socket(self):
        self.assertIsNone(_pruefen(binds={"workspace-a1": {"bind": "/from", "mode": "ro"}}))
        self.assertIsNone(_pruefen(network_mode="none", mounts=[
            Mount(target="/workspace", source="workspace-a1", type="volume", read_only=True)]))

    def test_exec_und_volume_der_plattform(self):
        self.assertIsNone(regeln.exec_anlegen({"Cmd": ["ls"], "User": "root"}))
        self.assertIsNone(regeln.volume_anlegen({"Name": "workspace-a1"}))
        self.assertIsNone(regeln.volume_anlegen({"Name": "x", "Driver": "local", "DriverOpts": {}}))


class WasGesperrtIst(unittest.TestCase):
    def test_privileged_und_fremde_faehigkeiten(self):
        self.assertIn("Privileged", _pruefen(privileged=True))
        for cap in ("SYS_ADMIN", "CAP_SYS_PTRACE", "NET_ADMIN", "ALL"):
            with self.subTest(cap):
                self.assertIsNotNone(_pruefen(cap_add=[cap]))

    def test_host_namespaces_und_netze(self):
        for feld in ({"network_mode": "host"}, {"network_mode": "container:orchestrator"},
                     {"pid_mode": "host"}, {"ipc_mode": "host"}, {"userns_mode": "host"},
                     {"uts_mode": "host"}, {"pid_mode": "container:abc"}):
            with self.subTest(feld):
                self.assertIsNotNone(_pruefen(**feld))

    def test_host_pfade(self):
        for quelle in ("/", "/etc", "/var/run/docker.sock", "/run/docker.sock", "/root", "/proc",
                       "/var/lib/docker/volumes", "/home/pi", "/srv/secondbrain/../../etc",
                       "/srv/secondbrainX", "/mnt/nas"):
            with self.subTest(quelle):
                self.assertIsNotNone(_pruefen(binds={quelle: {"bind": "/mnt/x", "mode": "rw"}}))
                self.assertIsNotNone(_pruefen(mounts=[Mount(target="/mnt/x", source=quelle, type="bind")]))

    def test_erlaubter_host_pfad_mit_gesperrtem_ziel(self):
        self.assertIsNotNone(_pruefen(binds={"/srv/secondbrain/a": {"bind": "/opt/mcp", "mode": "rw"}}))

    def test_geraete_sysctls_und_sonstiges(self):
        for feld in ({"devices": ["/dev/sda:/dev/sda:rwm"]}, {"sysctls": {"net.ipv4.ip_forward": "1"}},
                     {"cgroup_parent": "/"}, {"volume_driver": "x"}, {"runtime": "nvidia"},
                     {"volumes_from": ["orchestrator"]},
                     {"security_opt": ["apparmor=unconfined"]}, {"security_opt": ["seccomp=unconfined"]},
                     {"security_opt": ["label=disable"]}, {"security_opt": ["systempaths=unconfined"]}):
            with self.subTest(feld):
                self.assertIsNotNone(_pruefen(**feld))

    def test_volume_mit_treiberoptionen_ist_ein_host_mount(self):
        self.assertIsNotNone(regeln.volume_anlegen(
            {"Name": "x", "Driver": "local", "DriverOpts": {"type": "none", "o": "bind", "device": "/"}}))
        self.assertIsNotNone(regeln.volume_anlegen({"Name": "x", "Driver": "fremd"}))
        self.assertIsNotNone(_pruefen(mounts=[{"Type": "volume", "Source": "x", "Target": "/x",
                                               "VolumeOptions": {"DriverConfig": {"Name": "local",
                                                                                  "Options": {"device": "/"}}}}]))

    def test_proc_abschirmung_und_start_mit_hostconfig(self):
        for feld in ("MaskedPaths", "ReadonlyPaths"):
            with self.subTest(feld):
                self.assertIsNotNone(regeln.container_anlegen({"HostConfig": {feld: []}}, ERLAUBT))
        self.assertIsNone(regeln.container_starten({}))
        self.assertIsNone(regeln.container_starten(None))
        self.assertIsNotNone(regeln.container_starten({"HostConfig": {"Privileged": True}}))

    def test_privilegiertes_exec(self):
        self.assertIsNotNone(regeln.exec_anlegen({"Cmd": ["sh"], "Privileged": True}))

    def test_kaputte_koerper(self):
        for koerper in (None, [], "x", {"HostConfig": "x"}):
            with self.subTest(koerper):
                self.assertIsNotNone(regeln.container_anlegen(koerper, ERLAUBT))


if __name__ == "__main__":
    unittest.main()
