"""Prüfung der Compose-Dateien von Agenten-Apps (P0-1).

Geprüft wird das Modell aus ``docker compose config --format json`` — die Form,
die diese Tests nachbauen. Jede Sperre hat einen eigenen Fall (abgelehnt) und
jede Freigabe einen Gegenfall (durchgelassen), damit eine zu weite UND eine zu
enge Positivliste auffällt.
"""

import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.core import compose_pruefung as cp

PROJEKT = "agent-abcdef12-projects-todo"
QUELLE = cp.fuer_agent("abcdef12-0000", "workspace-abcdef12-0000")


def _modell(**dienst) -> dict:
    """Ein normalisiertes Modell mit einem Dienst ``web``, wie ``config`` es liefert."""
    web = {"image": "nginx:1.27", "networks": {"default": None}}
    web.update(dienst)
    return {
        "name": PROJEKT,
        "services": {"web": web},
        "networks": {"default": {"name": f"{PROJEKT}_default", "ipam": {}}},
    }


def _pruefen(modell: dict):
    return cp.pruefen(copy.deepcopy(modell), QUELLE, PROJEKT)


class GesperrtTests(unittest.TestCase):
    """Alles, was Zugriff auf den Server gibt, wird abgelehnt."""

    def assertAbgelehnt(self, modell: dict, *stichworte: str):
        with self.assertRaises(cp.ComposeAbgelehnt) as ctx:
            _pruefen(modell)
        text = " ".join(ctx.exception.verstoesse)
        for wort in stichworte:
            self.assertIn(wort, text)

    def test_privileged(self):
        self.assertAbgelehnt(_modell(privileged=True), "privileged")

    def test_bind_mount_der_server_wurzel(self):
        self.assertAbgelehnt(_modell(volumes=[{"type": "bind", "source": "/", "target": "/host"}]), "„/“")

    def test_docker_socket(self):
        self.assertAbgelehnt(
            _modell(volumes=[{"type": "bind", "source": "/var/run/docker.sock", "target": "/var/run/docker.sock"}]),
            "docker.sock",
        )

    def test_ausbruch_aus_dem_workspace_per_punktpunkt(self):
        # config schreibt ./../../etc als /etc — der Pfad liegt dann ausserhalb.
        self.assertAbgelehnt(_modell(volumes=[{"type": "bind", "source": "/etc", "target": "/x"}]), "/etc")

    def test_aehnlicher_praefix_ist_nicht_der_workspace(self):
        self.assertAbgelehnt(_modell(volumes=[{"type": "bind", "source": "/workspacefremd/x", "target": "/x"}]))

    def test_netz_des_hosts(self):
        self.assertAbgelehnt(_modell(network_mode="host"), "network_mode")

    def test_netz_eines_fremden_containers(self):
        self.assertAbgelehnt(_modell(network_mode="container:ai-employee-orchestrator"), "network_mode")

    def test_pid_des_hosts(self):
        self.assertAbgelehnt(_modell(pid="host"), "pid")

    def test_ipc_des_hosts(self):
        self.assertAbgelehnt(_modell(ipc="host"), "ipc")

    def test_uts_des_hosts(self):
        self.assertAbgelehnt(_modell(uts="host"), "uts")

    def test_userns_des_hosts(self):
        self.assertAbgelehnt(_modell(userns_mode="host"), "userns_mode")

    def test_zusaetzliche_faehigkeiten(self):
        self.assertAbgelehnt(_modell(cap_add=["SYS_ADMIN"]), "SYS_ADMIN")
        self.assertAbgelehnt(_modell(cap_add=["ALL"]), "ALL")
        self.assertAbgelehnt(_modell(cap_add=["CAP_SYS_PTRACE"]), "SYS_PTRACE")

    def test_geraete(self):
        self.assertAbgelehnt(_modell(devices=[{"source": "/dev/sda", "target": "/dev/sda"}]), "devices")

    def test_seccomp_und_apparmor_aus(self):
        self.assertAbgelehnt(_modell(security_opt=["seccomp=unconfined"]), "seccomp")
        self.assertAbgelehnt(_modell(security_opt=["apparmor=unconfined"]), "apparmor")
        self.assertAbgelehnt(_modell(security_opt=["label=disable"]), "label")

    def test_ordner_fremder_container(self):
        self.assertAbgelehnt(_modell(volumes_from=["ai-employee-orchestrator"]), "volumes_from")

    def test_secrets_und_configs_lesen_dateien_vom_server(self):
        self.assertAbgelehnt(_modell(secrets=[{"source": "s"}]), "secrets")
        m = _modell()
        m["secrets"] = {"s": {"file": "/etc/shadow"}}
        self.assertAbgelehnt(m, "secrets")
        m = _modell()
        m["configs"] = {"c": {"file": "/etc/shadow"}}
        self.assertAbgelehnt(m, "configs")

    def test_docker_api_im_container(self):
        self.assertAbgelehnt(_modell(use_api_socket=True), "use_api_socket")

    def test_provider_dienste(self):
        self.assertAbgelehnt(_modell(provider={"type": "x"}), "provider")

    def test_unbekannte_schluessel_sind_gesperrt(self):
        self.assertAbgelehnt(_modell(neuer_schalter_aus_compose_v9=True), "neuer_schalter_aus_compose_v9")

    def test_fremdes_volume_per_name(self):
        m = _modell(volumes=[{"type": "volume", "source": "db", "target": "/data"}])
        m["volumes"] = {"db": {"name": "ai-employee_postgres_data"}}
        self.assertAbgelehnt(m, "ai-employee_postgres_data")

    def test_fremdes_volume_per_external(self):
        m = _modell(volumes=[{"type": "volume", "source": "ws", "target": "/data"}])
        m["volumes"] = {"ws": {"name": "workspace-fremder-agent", "external": True}}
        self.assertAbgelehnt(m, "workspace-fremder-agent")

    def test_volume_treiberoptionen_binden_server_ordner(self):
        m = _modell(volumes=[{"type": "volume", "source": "x", "target": "/data"}])
        m["volumes"] = {"x": {"name": f"{PROJEKT}_x", "driver": "local",
                              "driver_opts": {"type": "none", "o": "bind", "device": "/"}}}
        self.assertAbgelehnt(m, "driver_opts")

    def test_nicht_angelegtes_volume(self):
        self.assertAbgelehnt(_modell(volumes=[{"type": "volume", "source": "nirgends", "target": "/d"}]), "nirgends")


    def test_netz_treiber_host_oder_macvlan(self):
        m = _modell()
        m["networks"]["default"]["driver"] = "macvlan"
        self.assertAbgelehnt(m, "macvlan")

    def test_build_mit_netz_des_hosts(self):
        self.assertAbgelehnt(_modell(build={"context": "/workspace/p", "network": "host"}), "build.network")

    def test_build_mit_rechten_und_schluesseln(self):
        self.assertAbgelehnt(_modell(build={"context": "/workspace/p", "entitlements": ["security.insecure"]}), "build.entitlements")
        self.assertAbgelehnt(_modell(build={"context": "/workspace/p", "ssh": ["default"]}), "build.ssh")
        self.assertAbgelehnt(_modell(build={"context": "/workspace/p", "secrets": ["x"]}), "build.secrets")
        self.assertAbgelehnt(_modell(build={"context": "/workspace/p", "privileged": True}), "build.privileged")

    def test_build_kontext_ausserhalb(self):
        self.assertAbgelehnt(_modell(build={"context": "/etc"}), "Build-Kontext")
        self.assertAbgelehnt(_modell(build={"context": "https://example.invalid/repo.git"}), "Build-Kontext")
        self.assertAbgelehnt(
            _modell(build={"context": "/workspace/p", "additional_contexts": {"x": "/var/run"}}), "„x“")

    def test_plattform_labels_faelschen(self):
        """Mit „ai-employee.type=agent“ gäbe sich eine App als Agent aus."""
        self.assertAbgelehnt(_modell(labels={"ai-employee.type": "agent"}), "ai-employee.")
        self.assertAbgelehnt(_modell(build={"context": "/workspace/p", "labels": {"ai-employee.type": "agent"}}),
                             "Build-Labels")
        m = _modell()
        m["networks"]["default"]["labels"] = {"ai-employee.apps-netz": "fremd123"}
        self.assertAbgelehnt(m, "Netz")

    def test_docker_labels_faelschen(self):
        self.assertAbgelehnt(_modell(labels={"com.docker.compose.project": "agent-fremd-x"}), "com.docker.")

    def test_log_treiber_mit_netz(self):
        self.assertAbgelehnt(_modell(logging={"driver": "syslog", "options": {"syslog-address": "udp://x"}}), "syslog")

    def test_geraete_ueber_deploy(self):
        self.assertAbgelehnt(
            _modell(deploy={"resources": {"reservations": {"devices": [{"capabilities": ["gpu"]}]}}}), "devices")

    def test_hook_mit_rechten(self):
        self.assertAbgelehnt(_modell(post_start=[{"command": "id", "privileged": True}]), "post_start")

    def test_negativer_oom_wert(self):
        self.assertAbgelehnt(_modell(oom_score_adj=-1000), "oom_score_adj")

    def test_env_file_ausserhalb(self):
        self.assertAbgelehnt(_modell(env_file=[{"path": "/proc/1/environ", "required": True}]), "env_file")

    def test_alle_verstoesse_auf_einmal(self):
        with self.assertRaises(cp.ComposeAbgelehnt) as ctx:
            _pruefen(_modell(privileged=True, pid="host", cap_add=["SYS_ADMIN"]))
        self.assertEqual(len(ctx.exception.verstoesse), 3)


class ErlaubtTests(unittest.TestCase):
    """Übliche Apps laufen weiter — genau das, was die Agenten-Apps auf dem Pi nutzen."""

    def test_uebliche_app(self):
        modell = _modell(
            build={"context": "/workspace/projects/todo", "dockerfile": "Dockerfile", "target": "prod", "args": {"A": "1"}},
            environment={"PW": "a$$b"}, restart="unless-stopped", depends_on={"db": {"condition": "service_healthy"}},
            healthcheck={"test": ["CMD", "true"]}, command=["node", "server.js"], expose=["3000"],
            read_only=True, tmpfs=["/tmp"], cap_drop=["ALL"], cap_add=["NET_BIND_SERVICE", "CAP_CHOWN"],
            security_opt=["no-new-privileges:true"], pids_limit=200, mem_limit="512m", cpus=1.0,
            logging={"driver": "json-file", "options": {"max-size": "10m"}},
            deploy={"resources": {"limits": {"memory": "512M", "cpus": "1"}}, "restart_policy": {"condition": "on-failure"}},
            profiles=["web"], user="1000:1000", extra_hosts=["host.docker.internal=host-gateway"],
        )
        modell["services"]["db"] = {"image": "postgres:16", "networks": {"default": None},
                                    "volumes": [{"type": "volume", "source": "pg", "target": "/var/lib/postgresql/data"}]}
        modell["volumes"] = {"pg": {"name": f"{PROJEKT}_pg"}}
        neu, _hinweise, _ordner = _pruefen(modell)
        self.assertEqual(neu["services"]["web"]["environment"], {"PW": "a$$b"}, "$$ muss erhalten bleiben")
        self.assertEqual(neu["services"]["web"]["cap_add"], ["NET_BIND_SERVICE", "CHOWN"])
        self.assertEqual(neu["volumes"]["pg"]["name"], f"{PROJEKT}_pg")

    def test_eigenes_volume_mit_praefix_bleibt(self):
        """Wie die laufende App auf dem Pi: name: agent-<id8>-…-db, auch external."""
        m = _modell(volumes=[{"type": "volume", "source": "db", "target": "/d"},
                             {"type": "volume", "source": "alt", "target": "/e"}])
        m["volumes"] = {"db": {"name": "agent-abcdef12-clearlight-db"},
                        "alt": {"name": "agent-abcdef12-alt", "external": True}}
        neu, _, _ = _pruefen(m)
        self.assertEqual(neu["volumes"]["db"]["name"], "agent-abcdef12-clearlight-db")

    def test_netz_zwischen_diensten(self):
        m = _modell(network_mode="service:db")
        m["services"]["db"] = {"image": "postgres:16", "networks": {"default": None}}
        neu, _, _ = _pruefen(m)
        self.assertEqual(neu["services"]["web"]["network_mode"], "service:db")
        self.assertNotIn("networks", neu["services"]["web"])

    def test_ohne_netz(self):
        neu, _, _ = _pruefen(_modell(network_mode="none"))
        self.assertEqual(neu["services"]["web"]["network_mode"], "none")

    def test_privileged_false_ist_kein_verstoss(self):
        _pruefen(_modell(privileged=False, devices=[], secrets=[]))

    def test_projektname_kommt_vom_server(self):
        m = _modell()
        m["name"] = "ai-employee"
        neu, _, _ = _pruefen(m)
        self.assertEqual(neu["name"], PROJEKT)

    def test_erweiterungen_fallen_weg(self):
        m = _modell(**{"x-notiz": 1})
        m["x-logging"] = {"driver": "syslog"}
        neu, _, _ = _pruefen(m)
        self.assertNotIn("x-logging", neu)


class UmschreibungTests(unittest.TestCase):

    def test_ports_nur_lokal_und_frei(self):
        neu, _, _ = _pruefen(_modell(ports=[
            {"mode": "ingress", "target": 3000, "published": "3001", "protocol": "tcp"},
            {"mode": "ingress", "target": 53, "published": "53", "protocol": "udp", "host_ip": "0.0.0.0"},
        ]))
        self.assertEqual(neu["services"]["web"]["ports"], [
            {"target": 3000, "host_ip": "127.0.0.1", "protocol": "tcp"},
            {"target": 53, "host_ip": "127.0.0.1", "protocol": "udp"},
        ])

    def test_relativer_ordner_wird_unterordner_im_eigenen_volume(self):
        neu, hinweise, ordner = _pruefen(_modell(volumes=[
            {"type": "bind", "source": "/workspace/projects/todo/data", "target": "/data", "bind": {"create_host_path": True}},
            {"type": "bind", "source": "/workspace/projects/todo/nginx.conf", "target": "/etc/nginx.conf", "read_only": True},
        ]))
        self.assertEqual(neu["services"]["web"]["volumes"], [
            {"type": "volume", "source": "aie_workspace", "target": "/data", "volume": {"subpath": "projects/todo/data"}},
            {"type": "volume", "source": "aie_workspace", "target": "/etc/nginx.conf", "read_only": True,
             "volume": {"subpath": "projects/todo/nginx.conf"}},
        ])
        self.assertEqual(neu["volumes"]["aie_workspace"], {"external": True, "name": "workspace-abcdef12-0000"})
        self.assertEqual(ordner, ["/workspace/projects/todo/data", "/workspace/projects/todo/nginx.conf"])
        self.assertTrue(hinweise)

    def test_ohne_ordner_kein_workspace_volume(self):
        neu, _, _ = _pruefen(_modell())
        self.assertNotIn("volumes", neu)

    def test_containername_bekommt_praefix_und_behaelt_alias(self):
        neu, hinweise, _ = _pruefen(_modell(container_name="voicy-api"))
        web = neu["services"]["web"]
        self.assertEqual(web["container_name"], "agent-abcdef12-voicy-api")
        self.assertEqual(web["networks"]["default"]["aliases"], ["voicy-api"])
        self.assertTrue(any("voicy-api" in h for h in hinweise))

    def test_containername_der_plattform_wird_umbenannt(self):
        neu, _, _ = _pruefen(_modell(container_name="ai-employee-redis"))
        self.assertEqual(neu["services"]["web"]["container_name"], "agent-abcdef12-ai-employee-redis")

    def test_netzname_bekommt_praefix(self):
        m = _modell(networks={"app": None})
        m["networks"]["app"] = {"name": "aios-quick-eva-net", "driver": "bridge",
                                "ipam": {"config": [{"subnet": "192.168.178.0/24"}]}}
        neu, hinweise, _ = _pruefen(m)
        self.assertEqual(neu["networks"]["app"]["name"], "agent-abcdef12-aios-quick-eva-net")
        self.assertEqual(neu["networks"]["app"]["ipam"], {}, "eigener Adressbereich kann das Firmennetz überdecken")
        self.assertTrue(hinweise)

    def test_fremdes_netz_wird_zum_eigenen(self):
        """Ein externes Netz der Plattform heisst nach der Umschreibung anders und ist
        damit nicht erreichbar; zwei Projekte desselben Agenten teilen sich ihres weiter."""
        m = _modell(networks={"plattform": None})
        m["networks"]["plattform"] = {"name": "ai-employee-network", "external": True}
        neu, hinweise, _ = _pruefen(m)
        self.assertEqual(neu["networks"]["plattform"], {"name": "agent-abcdef12-ai-employee-network", "external": True})
        self.assertTrue(any("nicht erreichbar" in h for h in hinweise))

    def test_bridge_wird_zum_eigenen_netz(self):
        neu, hinweise, _ = _pruefen(_modell(network_mode="bridge"))
        self.assertNotIn("network_mode", neu["services"]["web"])
        self.assertTrue(any("bridge" in h for h in hinweise))

    def test_feste_adresse_im_netz_entfaellt(self):
        neu, _, _ = _pruefen(_modell(networks={"default": {"ipv4_address": "10.0.0.5", "aliases": ["w"]}}))
        self.assertEqual(neu["services"]["web"]["networks"]["default"], {"aliases": ["w"]})


class AbbildUndGrenzenTests(unittest.TestCase):
    """Funde der Sicherheitsprüfung: Abbilder der Plattform überschreiben, Server lahmlegen."""

    def test_build_darf_kein_abbild_der_plattform_ueberschreiben(self):
        for ziel in ("docker:cli", "ai-employee-agent:latest", "ghcr.io/greeves89/ai-employee-orchestrator:1.407.2"):
            neu, hinweise, _ = _pruefen(_modell(image=ziel, build={"context": "/workspace/p"}))
            name = neu["services"]["web"]["image"]
            self.assertTrue(name.startswith("agent-abcdef12-"), name)
            self.assertNotIn("/", name, "kein Registry-Pfad im neuen Namen")
            self.assertTrue(hinweise)

    def test_andere_dienste_nutzen_das_umbenannte_abbild(self):
        m = _modell(image="meine-app:1.0", build={"context": "/workspace/p"})
        m["services"]["worker"] = {"image": "meine-app:1.0", "networks": {"default": None}}
        neu, _, _ = _pruefen(m)
        self.assertEqual(neu["services"]["worker"]["image"], "agent-abcdef12-meine-app:1.0")
        self.assertEqual(neu["services"]["web"]["image"], "agent-abcdef12-meine-app:1.0")

    def test_fertige_abbilder_ohne_build_bleiben(self):
        neu, _, _ = _pruefen(_modell(image="postgres:16"))
        self.assertEqual(neu["services"]["web"]["image"], "postgres:16")

    def test_zusatznamen_beim_bauen_fallen_weg(self):
        neu, _, _ = _pruefen(_modell(build={"context": "/workspace/p", "tags": ["docker:cli"]}))
        self.assertNotIn("tags", neu["services"]["web"]["build"])

    def test_reservierter_volume_name(self):
        m = _modell(volumes=[{"type": "volume", "source": "aie_workspace", "target": "/d",
                              "volume": {"subpath": "x"}}])
        m["volumes"] = {"aie_workspace": {"name": "agent-abcdef12-x"}}
        with self.assertRaises(cp.ComposeAbgelehnt) as ctx:
            _pruefen(m)
        self.assertIn("reserviert", " ".join(ctx.exception.verstoesse))

    def test_standardgrenzen(self):
        neu, _, _ = _pruefen(_modell())
        web = neu["services"]["web"]
        self.assertEqual((web["pids_limit"], web["mem_limit"]), (cp.APP_PROZESSE, cp.APP_SPEICHER))

    def test_eigene_grenzen_bleiben_in_der_spanne(self):
        neu, _, _ = _pruefen(_modell(mem_limit="512m", pids_limit=200))
        self.assertEqual((neu["services"]["web"]["mem_limit"], neu["services"]["web"]["pids_limit"]), ("512m", 200))
        neu, hinweise, _ = _pruefen(_modell(pids_limit=-1))
        self.assertEqual(neu["services"]["web"]["pids_limit"], cp.APP_PROZESSE)
        neu, _, _ = _pruefen(_modell(deploy={"resources": {"limits": {"memory": "1G"}}}))
        self.assertNotIn("mem_limit", neu["services"]["web"])

    def test_zu_viele_kopien(self):
        for m in (_modell(scale=50), _modell(deploy={"replicas": 50})):
            with self.assertRaises(cp.ComposeAbgelehnt):
                _pruefen(m)


class TaskforceQuelleTests(unittest.TestCase):
    """Im geteilten Volume liegt der Ordner unter dem Unterordner des Besitzers."""

    def test_unterordner_des_besitzers(self):
        q = cp.Quelle(volume="ai-employee-shared", sicht="/shared", praefix="agent-abcdef12-", teilpfad="u1")
        self.assertEqual(q.im_volume("/shared/taskforce/r1/data"), "u1/taskforce/r1/data")
        self.assertEqual(q.im_volume("/shared"), "u1")
        self.assertIsNone(q.im_volume("/shared/../etc"))
        self.assertIsNone(q.im_volume("/sharedx"))

    def test_nur_der_raumordner_und_keine_ordner_mounts(self):
        q = cp.Quelle(volume="ai-employee-shared", sicht="/shared", praefix="agent-abcdef12-", teilpfad="u1",
                      bereich="/shared/taskforce/r1", ordner_erlaubt=False)
        self.assertIsNone(q.im_volume("/shared/taskforce/r2/data"), "Ordner eines anderen Raums")
        self.assertIsNone(q.im_volume("/shared/privat"))
        self.assertEqual(q.im_volume("/shared/taskforce/r1/app"), "u1/taskforce/r1/app")
        m = _modell(volumes=[{"type": "bind", "source": "/shared/taskforce/r1/data", "target": "/d"}],
                    build={"context": "/shared/taskforce/r1"})
        with self.assertRaises(cp.ComposeAbgelehnt) as ctx:
            cp.pruefen(m, q, PROJEKT)
        self.assertIn("nur benannte Volumes", " ".join(ctx.exception.verstoesse))
        with self.assertRaises(cp.ComposeAbgelehnt):
            cp.pruefen(_modell(build={"context": "/shared/taskforce/r2"}), q, PROJEKT)

    def test_mount_nur_mit_unterordner(self):
        q = cp.Quelle(volume="ai-employee-shared", sicht="/shared", praefix="agent-abcdef12-", teilpfad="u1")
        m = q.mount(nur_lesen=True)
        self.assertEqual(m["VolumeOptions"], {"Subpath": "u1"})
        self.assertTrue(m["ReadOnly"])


class RunnerTests(unittest.TestCase):
    """Wie die drei Runner laufen — Rechte, Netz und was eingebunden ist."""

    def _docker(self, ausgabe=b"{}", code=0, digests=("docker@sha256:abc",)):
        docker = SimpleNamespace(client=SimpleNamespace(containers=MagicMock(), images=MagicMock()))
        container = MagicMock()
        container.wait.return_value = {"StatusCode": code}
        container.logs.return_value = ausgabe
        docker.client.containers.run.return_value = container
        docker.client.images.get.return_value = SimpleNamespace(id="sha256:runner", attrs={"RepoDigests": list(digests)})
        docker.client.images.pull.return_value = SimpleNamespace(id="sha256:frisch")
        return docker

    def test_normalisieren_ohne_socket_ohne_netz_nur_lesend(self):
        docker = self._docker(json.dumps(_modell()).encode())
        cp.normalisieren(docker, QUELLE, PROJEKT, "/workspace/projects/todo/docker-compose.yml")
        kw = docker.client.containers.run.call_args.kwargs
        self.assertEqual(kw["volumes"], {}, "kein Docker-Socket beim Auswerten")
        self.assertEqual(kw["network_mode"], "none")
        self.assertEqual(kw["user"], "1000:1000")
        self.assertTrue(kw["mounts"][0]["ReadOnly"])
        self.assertFalse(docker.client.containers.run.return_value.logs.call_args.kwargs["stderr"],
                         "Warnungen dürfen das JSON nicht verderben")
        self.assertIn("--project-directory", kw["command"])
        self.assertIn("/workspace/projects/todo", kw["command"])

    def test_ungueltige_datei_gibt_422(self):
        docker = self._docker(b"yaml: line 3: bad", code=1)
        with self.assertRaises(HTTPException) as ctx:
            cp.normalisieren(docker, QUELLE, PROJEKT, "/workspace/p/docker-compose.yml")
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("line 3", ctx.exception.detail, "im Fehlerfall kommt stderr mit")

    def test_runner_mit_grenzen_und_ohne_rest(self):
        docker = self._docker()
        cp.ausfuehren(docker, QUELLE, PROJEKT, {"services": {}}, ["up", "-d"])
        kw = docker.client.containers.run.call_args.kwargs
        self.assertEqual((kw["mem_limit"], kw["pids_limit"]), (cp.RUNNER_SPEICHER, cp.RUNNER_PROZESSE))
        self.assertTrue(kw["detach"])
        docker.client.containers.run.return_value.remove.assert_called_once()

    def test_runner_haengt_nicht(self):
        docker = self._docker()
        c = docker.client.containers.run.return_value
        c.wait.side_effect = TimeoutError("read timeout")
        code, ausgabe = cp.ausfuehren(docker, QUELLE, PROJEKT, None, ["down"])
        self.assertEqual(code, 124)
        self.assertIn("Zeitüberschreitung", ausgabe)
        c.kill.assert_called_once()
        c.remove.assert_called_once()

    def test_runner_nur_aus_der_registry_und_ueber_die_kennung(self):
        docker = self._docker()
        cp.ausfuehren(docker, QUELLE, PROJEKT, None, ["down"])
        self.assertEqual(docker.client.containers.run.call_args.kwargs["image"], "sha256:runner")

    def test_lokal_gebautes_runner_abbild_wird_ersetzt(self):
        """Hiesse ein Build docker:cli, liefe sonst Agenten-Code mit dem Docker-Socket."""
        docker = self._docker(digests=())
        cp.ausfuehren(docker, QUELLE, PROJEKT, None, ["down"])
        docker.client.images.pull.assert_called_once_with("docker", tag="cli")
        self.assertEqual(docker.client.containers.run.call_args.kwargs["image"], "sha256:frisch")

    def test_ausfuehren_ueber_stdin_ohne_datei_im_workspace(self):
        docker = self._docker(b"Started")
        modell = {"name": PROJEKT, "services": {"web": {"image": "nginx"}}}
        cp.ausfuehren(docker, QUELLE, PROJEKT, modell, ["up", "-d", "--build"])
        kw = docker.client.containers.run.call_args.kwargs
        self.assertEqual(json.loads(kw["environment"]["AIE_COMPOSE"]), modell)
        self.assertEqual(kw["environment"]["AIE_PROJEKT"], PROJEKT)
        self.assertIn("-f -", kw["command"][2])
        self.assertEqual(kw["command"][-3:], ["up", "-d", "--build"])
        self.assertTrue(kw["mounts"][0]["ReadOnly"], "der Workspace hängt beim Start nur lesend")
        self.assertIn("/var/run/docker.sock", kw["volumes"])

    def test_stoppen_ohne_datei_und_ohne_workspace(self):
        docker = self._docker(b"")
        cp.ausfuehren(docker, QUELLE, PROJEKT, None, ["down"])
        kw = docker.client.containers.run.call_args.kwargs
        self.assertEqual(kw["command"], ["docker", "compose", "-p", PROJEKT, "down"])
        self.assertEqual(kw["mounts"], [])

    def test_zu_grosses_modell(self):
        modell = {"services": {"web": {"environment": {"X": "y" * 130_000}}}}
        with self.assertRaises(HTTPException):
            cp.ausfuehren(self._docker(), QUELLE, PROJEKT, modell, ["up"])

    def test_ordner_anlegen_als_agent_ohne_socket(self):
        docker = self._docker(b"")
        cp.ordner_anlegen(docker, QUELLE, ["/workspace/p/data"])
        kw = docker.client.containers.run.call_args.kwargs
        self.assertEqual(kw["user"], "1000:1000")
        self.assertEqual(kw["volumes"], {})
        self.assertEqual(kw["network_mode"], "none")
        self.assertEqual(kw["command"][-1], "/workspace/p/data", "Pfade als Argumente, nie im Skripttext")


class VorbereitenTests(unittest.IsolatedAsyncioTestCase):

    async def test_ablehnung_gibt_422_und_steht_im_pruefprotokoll(self):
        protokoll = MagicMock()

        async def _prot(*a, **k):
            protokoll(*a, **k)

        with patch.object(cp, "normalisieren", return_value=_modell(privileged=True)), \
             patch.object(cp, "_ablehnung_protokollieren", side_effect=_prot), \
             patch.object(cp, "ordner_anlegen") as anlegen:
            with self.assertRaises(HTTPException) as ctx:
                await cp.vorbereiten(MagicMock(), QUELLE, PROJEKT, "/workspace/p/c.yml", agent_id="abcdef12")
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("privileged", ctx.exception.detail)
        protokoll.assert_called_once()
        anlegen.assert_not_called()


class StartwegeTests(unittest.IsolatedAsyncioTestCase):
    """Jeder Weg, der eine Compose-Datei startet, läuft über die Prüfung."""

    async def test_start_und_rebuild_pruefen_vor_dem_start(self):
        from app.api import docker_apps

        agent = SimpleNamespace(container_id="c1", volume_name="workspace-x")
        docker = SimpleNamespace(client=SimpleNamespace(containers=MagicMock()),
                                 exec_in_container=MagicMock(return_value=(0, "")))
        abgelehnt = HTTPException(status_code=422, detail="gesperrt")
        for kern in (docker_apps._start_core, docker_apps._rebuild_core):
            with patch.object(docker_apps, "_resolve_compose_file", return_value="/workspace/p/docker-compose.yml"), \
                 patch.object(docker_apps, "_ensure_env_files"), \
                 patch.object(cp, "vorbereiten", side_effect=abgelehnt), \
                 patch.object(cp, "ausfuehren") as ausfuehren:
                with self.assertRaises(HTTPException):
                    await kern(docker, agent, "abcdef12", "p")
            ausfuehren.assert_not_called()

    def test_kein_runner_ausserhalb_des_pruefbausteins(self):
        """Ein neuer Weg an der Prüfung vorbei wäre wieder root auf dem Host."""
        import pathlib
        app = pathlib.Path(cp.__file__).resolve().parents[1]
        treffer = []
        for datei in app.rglob("*.py"):
            if datei.name == "compose_pruefung.py":
                continue
            text = datei.read_text(encoding="utf-8")
            if "docker:cli" in text or '"compose", "-p"' in text or "'compose', '-p'" in text:
                treffer.append(str(datei.relative_to(app)))
        self.assertEqual(treffer, [], "Compose läuft nur über core/compose_pruefung.py")


class AppNetzTests(unittest.TestCase):
    """Die App kommt ins interne App-Netz ihres Agenten, nicht ins Plattformnetz
    (früher erreichte jede App Postgres und Redis). Der Orchestrator tritt den
    eigenen Netzen der App NICHT bei — dort könnte die App Namen der Plattform
    für sich beanspruchen."""

    def _docker(self, app_im_plattformnetz=True, apps_netz=None):
        from docker.errors import NotFound
        app = SimpleNamespace(id="app1", name="agent-abcdef12-p-web-1",
                              labels={"com.docker.compose.project": PROJEKT},
                              attrs={"NetworkSettings": {"Networks": (
                                  {"ai-employee-network": {}, f"{PROJEKT}_default": {}} if app_im_plattformnetz
                                  else {f"{PROJEKT}_default": {}})}})
        orch = SimpleNamespace(id="orch1", name="ai-employee-orchestrator", labels={})
        agent = SimpleNamespace(id="agent1", name="ai-agent-x", labels={})
        plattform = MagicMock()
        neu = MagicMock()
        neu.name = "ai-employee-apps-abcdef12"
        neu.attrs = {"Containers": {}}
        alle = {"orch1": orch, "ai-employee-orchestrator": orch, "agent1": agent}

        def netz_holen(name):
            if name == "ai-employee-network":
                return plattform
            if apps_netz is None:
                raise NotFound("weg")
            return apps_netz

        client = SimpleNamespace(
            networks=SimpleNamespace(get=MagicMock(side_effect=netz_holen), create=MagicMock(return_value=neu)),
            containers=SimpleNamespace(list=MagicMock(return_value=[app]), get=MagicMock(side_effect=alle.__getitem__)),
        )
        return SimpleNamespace(client=client), plattform, neu, app, orch, agent

    def test_app_verlaesst_das_plattformnetz_und_kommt_ins_apps_netz(self):
        from app.api import docker_apps
        docker, plattform, apps, app, orch, agent = self._docker()
        docker_apps._app_netz_anbinden(docker, PROJEKT, "abcdef12", "agent1")
        plattform.disconnect.assert_called_once_with(app, force=True)
        docker.client.networks.create.assert_called_once_with(
            "ai-employee-apps-abcdef12", driver="bridge", internal=True,
            labels={docker_apps.APPS_NETZ_LABEL: "abcdef12"})
        verbunden = [c.args[0] for c in apps.connect.call_args_list]
        self.assertEqual(verbunden, [app, orch, agent])
        for c in apps.connect.call_args_list:
            self.assertNotIn("aliases", c.kwargs, "ohne Zusatznamen — sonst wieder DNS-Umleitung möglich")

    def test_fremdes_netz_mit_dem_namen_wird_nicht_genutzt(self):
        from app.api import docker_apps
        fremd = MagicMock()
        fremd.attrs = {"Labels": {}}
        docker, *_ = self._docker(apps_netz=fremd)
        docker_apps._app_netz_anbinden(docker, PROJEKT, "abcdef12", "agent1")
        fremd.connect.assert_not_called()

    def test_wer_schon_drin_ist_wird_nicht_erneut_verbunden(self):
        from app.api import docker_apps
        vorhanden = MagicMock()
        vorhanden.name = "ai-employee-apps-abcdef12"
        vorhanden.attrs = {"Labels": {docker_apps.APPS_NETZ_LABEL: "abcdef12"},
                           "Containers": {"app1": {}, "orch1": {}, "agent1": {}}}
        docker, plattform, *_ = self._docker(app_im_plattformnetz=False, apps_netz=vorhanden)
        docker_apps._app_netz_anbinden(docker, PROJEKT, "abcdef12", "agent1")
        plattform.disconnect.assert_not_called()
        vorhanden.connect.assert_not_called()

    def test_nach_neustart_nur_plattform_netze_wieder_beitreten(self):
        from app.api import docker_apps
        eigenes = MagicMock(attrs={"Labels": {docker_apps.APPS_NETZ_LABEL: "abcdef12"}})
        getarnt = MagicMock(attrs={"Labels": {}})
        netze = {"ai-employee-apps-abcdef12": eigenes, "ai-employee-apps-falsch": getarnt}
        orch = SimpleNamespace(id="orch1")
        docker = SimpleNamespace(client=SimpleNamespace(
            networks=SimpleNamespace(get=MagicMock(side_effect=netze.__getitem__)),
            containers=SimpleNamespace(get=MagicMock(return_value=orch))))
        docker_apps._orchestrator_anbinden(docker, [f"{PROJEKT}_default", "ai-employee-apps-abcdef12", "ai-employee-apps-falsch"])
        eigenes.connect.assert_called_once_with(orch)
        getarnt.connect.assert_not_called()
        docker.client.networks.get.assert_any_call("ai-employee-apps-abcdef12")
        self.assertNotIn(((f"{PROJEKT}_default",),), docker.client.networks.get.call_args_list,
                         "ein Netz der App wird nie betreten")

    def test_laufende_apps_ziehen_beim_start_um(self):
        from app.api import docker_apps
        plattform = MagicMock(attrs={"Containers": {"a": {}, "t": {}, "p": {}}})
        cs = {"a": SimpleNamespace(labels={"com.docker.compose.project": "agent-abcdef12-projects-todo"}),
              "t": SimpleNamespace(labels={"com.docker.compose.project": "agent-abcdef12-tf-room123456789xyz"}),
              "p": SimpleNamespace(labels={})}
        agent = SimpleNamespace(name="ai-agent-x")
        cs["agentcid"] = agent
        docker = SimpleNamespace(client=SimpleNamespace(
            networks=SimpleNamespace(get=MagicMock(return_value=plattform)),
            containers=SimpleNamespace(get=MagicMock(side_effect=cs.__getitem__),
                                       list=MagicMock(side_effect=AssertionError("nie nach Labels suchen")))),
            ans_apps_netz=MagicMock())
        with patch.object(docker_apps, "_app_netz_anbinden") as anbinden:
            self.assertEqual(docker_apps.apps_aus_dem_plattformnetz(docker, [("abcdef12-0000", "agentcid")]), 2)
        # Agent-ID und Container kommen aus der Datenbank — nie aus Label oder Umgebung,
        # die eine App selbst setzen kann.
        docker.ans_apps_netz.assert_called_once_with(agent, "abcdef12-0000")
        aufrufe = sorted(c.args[1:] for c in anbinden.call_args_list)
        self.assertEqual(aufrufe, [("agent-abcdef12-projects-todo", "abcdef12"),
                                   ("agent-abcdef12-tf-room123456789xyz", "tf-room12345678")])

    def test_orchestrator_tritt_keinem_netz_der_app_bei(self):
        """Im Netz der App heißen Dienste, wie die App will — auch „ai-employee-redis“."""
        import inspect

        from app.api import docker_apps
        quelltext = inspect.getsource(docker_apps)
        self.assertNotIn("networks.list(filters=_projekt_filter", quelltext)


class AgentImAppsNetzTests(unittest.TestCase):
    """Ein neu erstellter Agent erreicht seine Apps (etwa einen eigenen MCP-Server
    als App) — live aufgefallen nach dem Neuerstellen am 31.10."""

    def _dienst(self, netz):
        from app.services.docker_service import DockerService
        dienst = DockerService.__new__(DockerService)
        dienst.client = SimpleNamespace(networks=SimpleNamespace(get=MagicMock(side_effect=netz)))
        return dienst

    def test_tritt_dem_eigenen_apps_netz_bei(self):
        netz = MagicMock(attrs={"Labels": {cp.APPS_NETZ_LABEL: "abcdef12"}})
        dienst = self._dienst(lambda name: netz)
        dienst.ans_apps_netz("container", "abcdef12-0000")
        netz.connect.assert_called_once_with("container")
        dienst.client.networks.get.assert_called_once_with("ai-employee-apps-abcdef12")

    def test_fremdes_netz_gleichen_namens_nie(self):
        netz = MagicMock(attrs={"Labels": {}})
        self._dienst(lambda name: netz).ans_apps_netz("container", "abcdef12-0000")
        netz.connect.assert_not_called()

    def test_ohne_apps_netz_passiert_nichts(self):
        from docker.errors import NotFound

        def fehlt(_name):
            raise NotFound("weg")
        self._dienst(fehlt).ans_apps_netz("container", "abcdef12-0000")

    def test_beim_anlegen_wird_beigetreten(self):
        import inspect

        from app.services.docker_service import DockerService
        self.assertIn("self.ans_apps_netz(container", inspect.getsource(DockerService.create_container))


class SpeicherTests(unittest.TestCase):

    def test_eigener_speicher_wird_gekappt(self):
        neu, hinweise, _ = _pruefen(_modell(mem_limit="64g", memswap_limit=-1))
        self.assertEqual(neu["services"]["web"]["mem_limit"], cp.APP_SPEICHER_MAX)
        self.assertNotIn("memswap_limit", neu["services"]["web"])
        neu, _, _ = _pruefen(_modell(deploy={"resources": {"limits": {"memory": "100G"}}}))
        self.assertEqual(neu["services"]["web"]["deploy"]["resources"]["limits"]["memory"], cp.APP_SPEICHER_MAX)

    def test_null_oder_negativ_heisst_nicht_unbegrenzt(self):
        neu, _, _ = _pruefen(_modell(mem_limit=0))
        self.assertEqual(neu["services"]["web"]["mem_limit"], cp.APP_SPEICHER, "0 bekommt die Standardgrenze")
        for wert in (-1, "0", "-1"):
            neu, _, _ = _pruefen(_modell(mem_limit=wert))
            self.assertEqual(neu["services"]["web"]["mem_limit"], cp.APP_SPEICHER_MAX, wert)

    def test_hostname_nur_mit_praefix(self):
        neu, hinweise, _ = _pruefen(_modell(hostname="ai-employee-postgres", domainname="example.invalid"))
        self.assertNotIn("hostname", neu["services"]["web"])
        self.assertNotIn("domainname", neu["services"]["web"])
        neu, _, _ = _pruefen(_modell(hostname="agent-abcdef12-web"))
        self.assertEqual(neu["services"]["web"]["hostname"], "agent-abcdef12-web")

    def test_unlesbarer_speicher_zaehlt_als_zu_viel(self):
        neu, _, _ = _pruefen(_modell(mem_limit="unendlich"))
        self.assertEqual(neu["services"]["web"]["mem_limit"], cp.APP_SPEICHER_MAX)


if __name__ == "__main__":
    unittest.main()
