"""Device-code login flow for Codex CLI ChatGPT auth."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.db.session import async_session_factory
from app.services.codex_auth_service import CodexAuthService
from app.services.oauth_service import OAuthService

logger = logging.getLogger(__name__)

DEVICE_URL_RE = re.compile(r"https://auth\.openai\.com/codex/device")
DEVICE_CODE_RE = re.compile(r"\b[A-Z0-9]{4}-[A-Z0-9]{5}\b")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
SESSION_TTL_SECONDS = 15 * 60


@dataclass
class CodexDeviceAuthSession:
    id: str
    code: str
    verification_uri: str
    codex_home: str
    expires_at: datetime
    status: str = "pending"
    error: str | None = None
    account_label: str | None = None
    #: Fuer wen die Anmeldung laeuft. ``None`` = fuer die ganze Anlage (der
    #: Administrator-Weg, wie bisher). Gesetzt = persoenlicher Zugang dieses
    #: Nutzers; das Ergebnis landet dann in ``user_ai_credentials`` statt als
    #: plattformweite Integration.
    for_user_id: str | None = None
    process: asyncio.subprocess.Process | None = field(default=None, repr=False)
    task: asyncio.Task | None = field(default=None, repr=False)


class CodexDeviceAuthService:
    """Runs `codex login --device-auth` and stores the resulting auth.json."""

    def __init__(self) -> None:
        self._sessions: dict[str, CodexDeviceAuthSession] = {}
        self._lock = asyncio.Lock()

    async def start(self, for_user_id: str | None = None) -> CodexDeviceAuthSession:
        if not shutil.which("codex"):
            raise RuntimeError("Codex CLI is not installed in the orchestrator container")

        session_id = os.urandom(12).hex()
        codex_home = tempfile.mkdtemp(prefix=f"codex-auth-{session_id}-")
        env = {
            **os.environ,
            "CODEX_HOME": codex_home,
            "NO_COLOR": "1",
            "TERM": "dumb",
        }
        process = await asyncio.create_subprocess_exec(
            "codex",
            "login",
            "--device-auth",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )

        try:
            initial_output = await self._read_until_device_code(process)
        except Exception:
            await self._stop_process(process)
            shutil.rmtree(codex_home, ignore_errors=True)
            raise

        clean_output = ANSI_RE.sub("", initial_output)
        url_match = DEVICE_URL_RE.search(clean_output)
        code_match = DEVICE_CODE_RE.search(clean_output)
        if not url_match or not code_match:
            await self._stop_process(process)
            shutil.rmtree(codex_home, ignore_errors=True)
            logger.warning("Could not parse Codex device-auth output: %s", clean_output)
            raise RuntimeError("Could not parse Codex device login output")

        session = CodexDeviceAuthSession(
            id=session_id,
            code=code_match.group(0),
            verification_uri=url_match.group(0),
            codex_home=codex_home,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=SESSION_TTL_SECONDS),
            process=process,
            # Ohne diese Zeile war die ganze persoenliche Anmeldung wirkungslos:
            # ``for_user_id`` kam als Parameter an, wurde aber nie in die
            # Sitzung uebernommen. Folge (beobachtet am 2026-08-16): die
            # Zustandsabfrage antwortete 404 (fremde Sitzung), die Oberflaeche
            # wartete ewig — und das Ergebnis landete im ``else``-Zweig als
            # Zugang der GANZEN ANLAGE samt ``sync_auth_json()``, wodurch das
            # private ChatGPT-Konto des Nutzers die gemeinsame Datei aller
            # Codex-Agenten ueberschrieb.
            for_user_id=for_user_id,
        )
        session.task = asyncio.create_task(self._wait_and_store(session))
        async with self._lock:
            self._sessions[session.id] = session
            self._cleanup_expired_locked()
        return session

    async def get(self, session_id: str) -> CodexDeviceAuthSession | None:
        async with self._lock:
            self._cleanup_expired_locked()
            return self._sessions.get(session_id)

    async def cancel(self, session_id: str) -> bool:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
        if not session:
            return False
        session.status = "cancelled"
        if session.task:
            session.task.cancel()
        if session.process and session.process.returncode is None:
            await self._stop_process(session.process)
        shutil.rmtree(session.codex_home, ignore_errors=True)
        return True

    async def _read_until_device_code(self, process: asyncio.subprocess.Process) -> str:
        assert process.stdout is not None
        output = ""
        deadline = asyncio.get_running_loop().time() + 20
        while asyncio.get_running_loop().time() < deadline:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=deadline - asyncio.get_running_loop().time())
            if not line:
                break
            text = line.decode("utf-8", errors="replace")
            output += text
            clean = ANSI_RE.sub("", output)
            if DEVICE_URL_RE.search(clean) and DEVICE_CODE_RE.search(clean):
                return output
        raise RuntimeError("Codex device login did not return a code in time")

    async def _wait_and_store(self, session: CodexDeviceAuthSession) -> None:
        try:
            try:
                await asyncio.wait_for(session.process.wait(), timeout=SESSION_TTL_SECONDS)
            except asyncio.TimeoutError:
                session.status = "expired"
                session.error = "Device code expired"
                if session.process and session.process.returncode is None:
                    await self._stop_process(session.process)
                return

            auth_path = os.path.join(session.codex_home, "auth.json")
            if not os.path.exists(auth_path):
                session.status = "error"
                session.error = "Codex login finished without auth.json"
                return

            with open(auth_path) as f:
                auth_json = json.dumps(json.load(f))

            if session.for_user_id:
                # Persoenlicher Zugang: nur dieser Nutzer, keine Anlage.
                #
                # Der Nutzer bekommt bei dieser Anmeldung NIE eine Datei zu
                # sehen — Codex legt sie im Container an und raeumt sie gleich
                # wieder weg. Ihn nach ihrem Inhalt zu fragen (so stand es bis
                # 2026-08-15 in der Oberflaeche) war deshalb unerfuellbar: die
                # ChatGPT-Seite meldet „kann geschlossen werden", und danach
                # gibt es nichts einzufuegen.
                from app.core.encryption import encrypt_token
                from app.models.user_ai_credential import UserAiCredential
                from sqlalchemy import select as _select

                async with async_session_factory() as db:
                    row = (await db.execute(_select(UserAiCredential).where(
                        UserAiCredential.user_id == session.for_user_id,
                        UserAiCredential.harness == "codex",
                    ))).scalar_one_or_none()
                    if row is None:
                        row = UserAiCredential(user_id=session.for_user_id, harness="codex")
                        db.add(row)
                    row.secret_encrypted = encrypt_token(auth_json)
                    row.label = row.label or "ChatGPT-Abo"
                    row.last_status = "ok"
                    await db.commit()
                    session.account_label = row.label
            else:
                async with async_session_factory() as db:
                    integration = await OAuthService(db, redis=None).store_auth_json("codex", auth_json)
                    session.account_label = integration.account_label
                # Nur der Anlagen-Zugang gehoert in die gemeinsame Datei.
                await CodexAuthService().sync_auth_json()
            session.status = "connected"
        except asyncio.CancelledError:
            session.status = "cancelled"
            raise
        except Exception as exc:
            logger.warning("Codex device auth failed: %s", exc)
            session.status = "error"
            session.error = str(exc)
        finally:
            shutil.rmtree(session.codex_home, ignore_errors=True)

    def _cleanup_expired_locked(self) -> None:
        now = datetime.now(timezone.utc)
        stale = [
            session_id
            for session_id, session in self._sessions.items()
            if session.expires_at < now and session.status in {"connected", "error", "expired", "cancelled"}
        ]
        for session_id in stale:
            self._sessions.pop(session_id, None)

    async def _stop_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            process.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.communicate(), timeout=2)
            return
        except asyncio.TimeoutError:
            pass
        except Exception:
            return

        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                return
            try:
                await asyncio.wait_for(process.communicate(), timeout=2)
            except Exception:
                logger.warning("Codex device-auth process did not exit cleanly")


codex_device_auth_service = CodexDeviceAuthService()
